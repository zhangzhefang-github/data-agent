"""Validate and execute the order Space's single-table DuckDB SQL."""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from threading import Timer
from time import monotonic

import duckdb
from sqlglot import exp, parse, parse_one
from sqlglot.errors import ParseError

from app.query import physical_value

MAX_ROWS = 500
QUERY_TIMEOUT = 5
COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)


class SqlRejected(ValueError):
    """The SQL requests a capability outside the authorized Space."""


class SqlCorrection(ValueError):
    """The model can correct the SQL using a bounded, public diagnostic."""


class SqlTimeout(TimeoutError):
    """DuckDB interrupted execution when the query reached its time limit."""


def _connect(path: Path):
    return duckdb.connect(str(path), read_only=True, config={"enable_external_access": "false"})


def _fetch(db, sql: str):
    timer = Timer(QUERY_TIMEOUT, db.interrupt)
    timer.start()
    try:
        return db.execute(sql).fetchmany(MAX_ROWS)
    except duckdb.InterruptException as exc:
        raise SqlTimeout("订单查询超时") from exc
    finally:
        timer.cancel()
        timer.join()


def _order_table(catalog):
    model, = catalog["models"].values()
    if catalog["id"] != "orders" or model["table"] != "orders":
        raise SqlRejected("当前主题空间未授权订单数据集")
    return model


def _require_order_table(db):
    table = db.execute("""
        SELECT table_type FROM information_schema.tables
        WHERE table_catalog = current_database()
          AND table_schema = 'main' AND table_name = 'orders'
    """).fetchone()
    if table != ("BASE TABLE",):
        raise SqlRejected("订单数据源必须为本地订单表")


def order_database_context(path: Path, tables: list[tuple[str, set[str]]]) -> dict:
    described = {}
    with _connect(path) as db:
        version = db.execute("SELECT version()").fetchone()[0]
        for table, allowed in tables:
            if not table.isidentifier():
                raise SqlRejected("授权表名不合法")
            columns = _fetch(db, f"DESCRIBE main.{table}")
            described[f"main.{table}"] = {name: kind for name, kind, *_ in columns if name in allowed}
            if set(described[f"main.{table}"]) != allowed:
                raise SqlRejected("授权列在数据库中不存在")
    return {"version": version, "tables": described}


def _column(node, names, qualifier=""):
    return (isinstance(node, exp.Column)
            and node.name.lower() in names
            and node.table.lower() in {"", qualifier}
            and not node.db and not node.catalog)


def _date_literal(node) -> exp.Literal:
    if isinstance(node, exp.Cast) and node.to.this == exp.DataType.Type.DATE:
        node = node.this
    if not isinstance(node, exp.Literal) or not node.is_string:
        raise SqlCorrection("订单日期使用 YYYY-MM-DD 字面量")
    try:
        date.fromisoformat(node.this)
    except ValueError as exc:
        raise SqlCorrection("订单日期使用合法的 YYYY-MM-DD 日期") from exc
    return node


def _filter_dates(node, dimensions, qualifier) -> list[exp.Literal]:
    if isinstance(node, (exp.And, exp.Or)):
        return _filter_dates(node.left, dimensions, qualifier) + _filter_dates(node.right, dimensions, qualifier)
    if isinstance(node, (exp.Paren, exp.Not)):
        return _filter_dates(node.this, dimensions, qualifier)
    if not isinstance(node, (*COMPARISONS, exp.Between, exp.In)) or not _column(node.this, dimensions, qualifier):
        raise SqlRejected("查询筛选限于已授权的订单日期和状态")
    field = node.this.name.lower()
    if isinstance(node, exp.Between):
        if node.args.get("symmetric"):
            raise SqlRejected("不支持对称日期区间")
        values = [node.args["low"], node.args["high"]]
    elif isinstance(node, exp.In):
        if not node.expressions or node.args.get("query") or node.args.get("unnest"):
            raise SqlRejected("筛选只能使用字面量列表")
        values = node.expressions
    else:
        values = [node.expression]
    dimension = dimensions[field]
    if dimension.get("values"):
        for value in values:
            if not isinstance(value, exp.Literal) or not value.is_string:
                raise SqlRejected("订单状态筛选只能使用状态字面量")
            try:
                value.set("this", physical_value(dimension, value.this))
            except ValueError as exc:
                raise SqlCorrection("订单状态须使用授权字典") from exc
        return []
    return [_date_literal(value) for value in values]


def _validate_order_sql(sql: str, catalog: dict, today: date):
    if not sql.strip():
        raise SqlCorrection("请提交一条 DuckDB SELECT")
    if len(sql) > 12000:
        raise SqlRejected("SQL 超过长度上限")
    try:
        statements = parse(sql, read="duckdb")
    except ParseError as exc:
        raise SqlCorrection("DuckDB SQL 语法错误") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise SqlRejected("只允许单条 SELECT 查询")
    tree = statements[0]
    supported = {"expressions", "from", "where", "group", "order", "limit", "having"}
    if any(value and key not in supported for key, value in tree.args.items()):
        raise SqlRejected("订单查询只支持单表汇总")
    model = _order_table(catalog)
    source = tree.args.get("from")
    table = source.this if source else None
    if (not isinstance(table, exp.Table) or not isinstance(table.this, exp.Identifier)
            or table.name.lower() != model["table"]
            or table.db.lower() not in {"", "main"} or table.catalog
            or any(value and key not in {"this", "db", "alias"} for key, value in table.args.items())
            or (table.args.get("alias") and table.args["alias"].columns)):
        raise SqlRejected("查询只允许访问已授权的订单表")
    table.set("db", exp.to_identifier("main"))
    qualifier = (table.alias or table.name).lower()
    dimensions = {item["column"]: item for item in catalog["dimensions"].values()}
    metrics = {name: parse_one(metric["expression"], read="duckdb") for name, metric in catalog["metrics"].items()}
    columns = []
    grouped = []
    missing_aliases = []
    for selection in tree.expressions:
        name = selection.alias_or_name.lower()
        value = selection.this if isinstance(selection, exp.Alias) else selection
        if _column(value, dimensions, qualifier) and name == value.name.lower():
            grouped.append(name)
        elif name in metrics:
            if not _matches_metric(value, metrics[name], qualifier):
                raise SqlCorrection(f"指标 {name} 须使用已发布的聚合口径")
        else:
            matching = [metric for metric, expression in metrics.items() if _matches_metric(value, expression, qualifier)]
            if len(matching) != 1:
                raise SqlRejected("查询包含未授权的指标或维度")
            missing_aliases.append(matching[0])
        columns.append(name)
    if missing_aliases:
        raise SqlCorrection(f"请使用获准的指标结果列别名：{'、'.join(missing_aliases)}")
    if len(set(columns)) != len(columns) or not set(columns).intersection(metrics):
        raise SqlCorrection("请使用至少一个获准指标及唯一的结果列名")
    group = tree.args.get("group")
    if group and any(value for key, value in group.args.items() if key != "expressions"):
        raise SqlRejected("仅支持按获准维度分组")
    keys = group.expressions if group else []
    if (any(not _column(key, dimensions, qualifier) for key in keys)
            or {key.name.lower() for key in keys} != set(grouped)):
        raise SqlCorrection("分组字段必须与输出维度一致")
    order = tree.args.get("order")
    if order:
        for item in order.expressions:
            value = item.this
            if isinstance(value, exp.Literal) and value.is_int and 1 <= int(value.this) <= len(columns):
                continue
            if _column(value, set(columns)) or _column(value, set(grouped), qualifier):
                name = value.name.lower()
            else:
                name = next((name for name in columns if name in metrics and _matches_metric(
                    value, metrics[name], qualifier,
                )), None)
            if name is None:
                raise SqlRejected("排序限于已选择的指标及维度")
            item.set("this", exp.Literal.number(columns.index(name) + 1))
    where = tree.args.get("where")
    dates = _filter_dates(where.this, dimensions, qualifier) if where else []
    if not dates:
        field = exp.column(model["date_column"], table=qualifier)
        tree = tree.where(exp.Between(this=field, low=exp.Literal.string(today.replace(month=1, day=1).isoformat()),
                                      high=exp.Literal.string(today.isoformat())), copy=False)
        dates = _filter_dates(tree.args["where"].this, dimensions, qualifier)
    having = tree.args.get("having")
    count_matches = having and isinstance(having.this, exp.GT) and _matches_metric(
        having.this.left, parse_one("COUNT(*)"), qualifier) and having.this.right == exp.Literal.number(0)
    if having and (grouped or not count_matches):
        raise SqlRejected("HAVING 仅用于无分组结果的空数据判断")
    if not grouped and not having:
        tree.set("having", exp.Having(this=parse_one("COUNT(*) > 0")))
    limit = tree.args.get("limit")
    if limit:
        value = limit.expression
        if not isinstance(limit, exp.Limit) or not isinstance(value, exp.Literal) or not value.is_int or int(value.this) < 0:
            raise SqlRejected("LIMIT 必须为非负整数")
        cap = min(int(value.this), MAX_ROWS)
    else:
        cap = MAX_ROWS
    tree.set("limit", exp.Limit(expression=exp.Literal.number(cap)))
    return tree, columns, dates


def _matches_metric(value, expected, qualifier):
    if type(value) is not type(expected):
        return False
    if isinstance(expected, exp.Count):
        return (isinstance(value.this, exp.Star) and not any(value.this.args.values())
                and not value.expressions)
    return isinstance(expected, exp.Sum) and _column(value.this, {expected.this.name}, qualifier)


def execute_order_sql(path: Path, sql: str, catalog: dict, today: date) -> dict:
    tree, columns, _ = _validate_order_sql(sql, catalog, today)
    checked = tree.sql(dialect="duckdb", comments=False)
    start = monotonic()
    try:
        with _connect(path) as db:
            _require_order_table(db)
            rows = _fetch(db, checked)
    except (duckdb.BinderException, duckdb.ConversionException, duckdb.ParserException) as exc:
        raise SqlCorrection("SQL 无法绑定字段或转换数据类型") from exc
    status_names = {entry["value"]: entry["name"] for entry in catalog["dimensions"]["status"].get("values", [])}
    return {
        "columns": columns,
        "rows": [[status_names.get(value, value) if name == "status" else value
                  for name, value in zip(columns, row, strict=True)] for row in _json_rows(rows)],
        "sql": tree.sql(dialect="duckdb", pretty=True, comments=False),
        "source": "DuckDB",
        "elapsed_ms": round((monotonic() - start) * 1000, 2),
    }


def _json_rows(rows):
    for row in rows:
        yield [value.isoformat() if isinstance(value, (date, datetime)) else float(value) if isinstance(value, Decimal)
               else value for value in row]


def prior_order_sql(sql: str, catalog: dict, today: date, *, shift=None) -> str:
    tree, _, dates = _validate_order_sql(sql, catalog, today)
    if shift:
        for literal in dates:
            literal.set("this", shift(date.fromisoformat(literal.this)).isoformat())
    return tree.sql(dialect="duckdb", comments=False)
