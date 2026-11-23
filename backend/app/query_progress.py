"""把一次问数里已经发生的动作，收成页面可显示的步骤。"""
import asyncio
import re
from collections.abc import AsyncIterator
from typing import Any


class QueryProgress:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict] = asyncio.Queue()
        self.steps: list[dict] = []

    def record(self, step_id: str, title: str, state: str) -> dict:
        payload = {"id": step_id, "title": title, "state": state}
        for index, step in enumerate(self.steps):
            if step["id"] == step_id:
                self.steps[index] = payload
                break
        else:
            self.steps.append(payload)
        return payload

    def publish(self, step_id: str, title: str, state: str) -> None:
        # 工具在模型流内部执行。进度先入队，外层才能在 SQL 结束前把步骤写给页面。
        self.queue.put_nowait(self.record(step_id, title, state))

    def state_of(self, step_id: str) -> str | None:
        for step in self.steps:
            if step["id"] == step_id:
                return step["state"]
        return None


def describe_sql(sql: str) -> str:
    """从即将执行的 SQL 抽出一句业务标题，不把语句本身展示成步骤。"""
    dates = list(dict.fromkeys(re.findall(r"\d{4}-\d{2}-\d{2}", sql)))
    lowered = sql.lower()
    if len(dates) >= 2:
        when = f"{dates[0]} 至 {dates[-1]}"
    elif dates:
        when = dates[0]
    else:
        when = ""
    qualified = r"(?:[\w\"]+\.)?"
    if re.search(rf"\b{qualified}payments?\b", lowered):
        subject = "支付"
    elif re.search(rf"\b{qualified}customers?\b", lowered):
        subject = "客户"
    elif re.search(rf"\b(from|join)\s+{qualified}orders\b", lowered):
        subject = "订单"
    else:
        subject = "数据"
    monthly = bool(re.search(
        r"date_trunc\s*\(\s*'month'|strftime\s*\([^)]*%m|date_part\s*\(\s*'month'",
        lowered,
    ))
    measures = []
    if re.search(r"\bcount\s*\(", lowered):
        measures.append("笔数")
    if re.search(r"\bsum\s*\(", lowered):
        measures.append("金额")
    if re.search(r"\bavg\s*\(", lowered):
        measures.append("平均值")
    if monthly:
        title = f"{when}的{subject}月度趋势" if when else f"{subject}月度趋势"
    elif measures:
        title = f"{when + '的' if when else ''}{subject}{'和'.join(measures)}"
    elif when:
        title = f"{when}的{subject}"
    else:
        title = f"查询{subject}"
    if not monthly and re.search(r"\bgroup\s+by\b", lowered):
        clause = re.split(r"\bgroup\s+by\b", lowered, maxsplit=1)[1]
        clause = re.split(r"\b(?:order\s+by|limit)\b", clause, maxsplit=1)[0]
        if "payment_method" in clause:
            title += "，按支付方式"
        elif "status" in clause:
            title += "，按订单状态"
        elif "customer" in clause:
            title += "，按客户"
        else:
            title += "，分组汇总"
    if re.search(r"\border\s+by\b", lowered):
        title += "，按结果排序"
    return title[:60]


def describe_structured(
    metrics: list[str], dimensions: list[str], start: str | None, end: str | None, catalog: dict,
) -> str:
    metric_names = [_catalog_name(catalog, "metrics", item) for item in metrics]
    dimension_names = [_catalog_name(catalog, "dimensions", item) for item in dimensions]
    title = "、".join(name for name in metric_names if name) or "查询"
    if dimension_names:
        title += "，按" + "、".join(dimension_names)
    if start and end:
        title = f"{start} 至 {end}的{title}"
    return title[:60]


def _catalog_name(catalog: dict, kind: str, identifier: str) -> str:
    item = catalog.get(kind, {}).get(identifier, {})
    if isinstance(item, dict) and item.get("name"):
        return str(item["name"])
    return identifier


async def merge_agent_events(agent_events: AsyncIterator[Any], queue: asyncio.Queue[dict]):
    """同时读模型流和查询进度，避免页面等到整段执行结束才出现步骤。"""
    agent_iter = agent_events.__aiter__()

    async def next_agent():
        try:
            return await anext(agent_iter)
        except StopAsyncIteration:
            return None

    agent_task = asyncio.create_task(next_agent())
    progress_task = asyncio.create_task(queue.get())
    try:
        while True:
            done, _pending = await asyncio.wait(
                {agent_task, progress_task}, return_when=asyncio.FIRST_COMPLETED,
            )
            if progress_task in done and not progress_task.cancelled():
                yield ("progress", progress_task.result())
                progress_task = asyncio.create_task(queue.get())
            if agent_task in done and not agent_task.cancelled():
                item = agent_task.result()
                if item is None:
                    if progress_task.done() and not progress_task.cancelled():
                        yield ("progress", progress_task.result())
                    else:
                        progress_task.cancel()
                        await asyncio.gather(progress_task, return_exceptions=True)
                    while not queue.empty():
                        yield ("progress", queue.get_nowait())
                    break
                yield ("agent", item)
                agent_task = asyncio.create_task(next_agent())
    finally:
        agent_task.cancel()
        progress_task.cancel()
        await asyncio.gather(agent_task, progress_task, return_exceptions=True)
