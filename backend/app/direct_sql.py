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


def _in_main(node) -> bool:
    schema = node.args.get("db")
    name = "" if schema is None else schema.name.lower()
    return name in {"", "main"} and not node.args.get("catalog")


def _column(node, names, qualifier=""):
    return (isinstance(node, exp.Column)
            and node.name.lower() in names
            and node.table.lower() in {"", qualifier}
            and _in_main(node))


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


def _filter_dates(node, dimensions, dimension_column) -> list[exp.Literal]:
    # IS NOT NULL 会被解析成 Not(Is)，不能把它展开成普通筛选。
    if isinstance(node, exp.Not) and isinstance(node.this, exp.Is):
        node = node.this
    if isinstance(node, exp.Is):
        if dimension_column(node.this, dimensions) and node.this.name.lower() == "customer_id":
            raise SqlCorrection("客户记录是否存在要用已授权的等值内连接，不要用客户标识是否为空代替。")
        raise SqlRejected("查询筛选限于已授权维度")
    if isinstance(node, (exp.And, exp.Or)):
        return _filter_dates(node.left, dimensions, dimension_column) + _filter_dates(node.right, dimensions, dimension_column)
    if isinstance(node, (exp.Paren, exp.Not)):
        return _filter_dates(node.this, dimensions, dimension_column)
    if not isinstance(node, (*COMPARISONS, exp.Between, exp.In)) or not dimension_column(node.this, dimensions):
        raise SqlRejected("查询筛选限于已授权维度")
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
    if dimension.get("is_time"):
        return [_date_literal(value) for value in values]
    for value in values:
        if not isinstance(value, exp.Literal) or not value.is_int:
            raise SqlRejected("非时间维度的筛选只能使用整数")
    return []


def _plain_table(table) -> bool:
    return (isinstance(table, exp.Table) and isinstance(table.this, exp.Identifier)
            and table.db.lower() in {"", "main"} and not table.catalog
            and not any(value and key not in {"this", "db", "alias"} for key, value in table.args.items())
            and not (table.args.get("alias") and table.args["alias"].columns))


def _equijoin_columns(node) -> tuple[tuple[str, str], tuple[str, str]]:
    if not isinstance(node, exp.EQ):
        raise SqlRejected("关联必须使用已授权的等值条件")
    columns = []
    for side in (node.this, node.expression):
        if not isinstance(side, exp.Column) or not side.table or not _in_main(side):
            raise SqlRejected("关联必须使用已授权的等值条件")
        columns.append((side.table.lower(), side.name.lower()))
    return columns[0], columns[1]


def _authorize_joins(joins, base: exp.Table, allowed: set[tuple[tuple[str, str], tuple[str, str]]]) -> dict[str, str]:
    known = {base.name.lower()}
    aliases = {(base.alias or base.name).lower(): base.name.lower()}
    for join in joins:
        kind = join.args.get("kind")
        if join.args.get("side") or join.args.get("pivots") or kind not in {None, "INNER"}:
            raise SqlRejected("只允许使用已授权关系的内连接")
        target = join.this
        if not _plain_table(target):
            raise SqlRejected("查询包含未授权的表关联")
        target.set("db", exp.to_identifier("main"))
        name = target.name.lower()
        visible = dict(aliases)
        visible[(target.alias or target.name).lower()] = name
        left, right = _equijoin_columns(join.args.get("on"))
        left_table = visible.get(left[0], left[0])
        right_table = visible.get(right[0], right[0])
        pair = tuple(sorted(((left_table, left[1]), (right_table, right[1]))))
        other = right_table if name == left_table else left_table
        if pair not in allowed or name not in {left_table, right_table} or name in known or other not in known:
            raise SqlRejected("查询包含未授权的表关联")
        known.add(name)
        aliases[(target.alias or target.name).lower()] = name
    return aliases


def _join_keys_by_table(allowed) -> dict[str, set[str]]:
    keys: dict[str, set[str]] = {}
    for left, right in allowed:
        keys.setdefault(left[0], set()).add(left[1])
        keys.setdefault(right[0], set()).add(right[1])
    return keys


def _dimension_column(node, names, qualifier: str, aliases: dict[str, str], anchor: str, join_keys: dict[str, set[str]]) -> bool:
    """订单表上的维度，以及已授权连接里对侧的同名连接键，都可以筛选和分组。"""
    if not isinstance(node, exp.Column) or not _in_main(node) or node.name.lower() not in names:
        return False
    table = node.table.lower()
    if table in {"", qualifier}:
        return True
    physical = aliases.get(table)
    return bool(physical and physical != anchor and node.name.lower() in join_keys.get(physical, ()))


def _validate_order_sql(sql: str, catalog: dict, today: date, joins=()):
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
    supported = {"expressions", "from", "joins", "where", "group", "order", "limit", "having"}
    if any(value and key not in supported for key, value in tree.args.items()):
        raise SqlRejected("订单查询只支持单表汇总或已授权的等值关联")
    model = _order_table(catalog)
    source = tree.args.get("from")
    table = source.this if source else None
    if not _plain_table(table):
        raise SqlRejected("查询只允许访问已授权的订单表")
    table.set("db", exp.to_identifier("main"))
    query_joins = tree.args.get("joins") or []
    if table.name.lower() != model["table"]:
        # 客户表在前、订单表在后的已授权等值连接不是越权，但指标仍必须从订单表汇总。
        try:
            _authorize_joins(query_joins, table, set(joins))
        except SqlRejected:
            raise SqlRejected("查询只允许访问已授权的订单表") from None
        if model["table"] not in {join.this.name.lower() for join in query_joins}:
            if _is_authorized_partner(table.name.lower(), model["table"], joins):
                raise SqlCorrection(
                    "查询必须从订单表开始，客户表只能按客户标识做等值内连接。统计客户时按客户标识分组，并使用订单笔数。"
                )
            raise SqlRejected("查询只允许访问已授权的订单表")
        raise SqlCorrection(
            "查询必须从订单表开始，客户表只能按客户标识做等值内连接。统计客户时按客户标识分组，并使用订单笔数。"
        )
    aliases = _authorize_joins(query_joins, table, set(joins))
    qualifier = (table.alias or table.name).lower()
    anchor = table.name.lower()
    join_keys = _join_keys_by_table(joins)
    dimensions = {item["column"]: item for item in catalog["dimensions"].values()}

    def dimension_column(node, names) -> bool:
        return _dimension_column(node, names, qualifier, aliases, anchor, join_keys)

    metrics = {name: parse_one(metric["expression"], read="duckdb") for name, metric in catalog["metrics"].items()}
    columns = []
    grouped = []
    missing_aliases = []
    for selection in tree.expressions:
        name = selection.alias_or_name.lower()
        value = selection.this if isinstance(selection, exp.Alias) else selection
        if dimension_column(value, dimensions) and name == value.name.lower():
            grouped.append(name)
        elif name in metrics:
            if not _matches_metric(value, metrics[name], qualifier):
                raise SqlCorrection(f"指标 {name} 须使用已发布的聚合口径")
        else:
            matching = [metric for metric, expression in metrics.items() if _matches_metric(value, expression, qualifier)]
            if len(matching) != 1:
                if _counts_customer_identifier(value):
                    raise SqlCorrection("没有已发布的客户数。请从订单表按客户标识分组，并使用订单笔数。")
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
    if (any(not dimension_column(key, dimensions) for key in keys)
            or {key.name.lower() for key in keys} != set(grouped)):
        raise SqlCorrection("分组字段必须与输出维度一致")
    order = tree.args.get("order")
    if order:
        for item in order.expressions:
            value = item.this
            if isinstance(value, exp.Literal) and value.is_int and 1 <= int(value.this) <= len(columns):
                continue
            if _column(value, set(columns)) or dimension_column(value, set(grouped)):
                name = value.name.lower()
            else:
                name = next((name for name in columns if name in metrics and _matches_metric(
                    value, metrics[name], qualifier,
                )), None)
            if name is None:
                raise SqlRejected("排序限于已选择的指标及维度")
            item.set("this", exp.Literal.number(columns.index(name) + 1))
    where = tree.args.get("where")
    dates = _filter_dates(where.this, dimensions, dimension_column) if where else []
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


def context_coverage_gap(sql: str, authorization, presentation) -> list[str]:
    """已授权但本次提示没展开的数据集。未授权表不在这里补，交给校验器拒绝。"""
    try:
        statements = parse(sql, read="duckdb")
    except ParseError:
        return []
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        return []
    names = []
    source = statements[0].args.get("from")
    if source is not None and isinstance(source.this, exp.Table):
        names.append(source.this.name.lower())
    for join in statements[0].args.get("joins") or []:
        if isinstance(join.this, exp.Table):
            names.append(join.this.name.lower())
    physical = {dataset.physical_object.lower(): dataset.id for dataset in authorization.sql.datasets}
    if any(name not in physical for name in names):
        return []
    presented = {dataset.id for dataset in presentation.sql.datasets}
    return sorted({physical[name] for name in names if physical[name] not in presented})


def _is_authorized_partner(table_name: str, order_table: str, allowed) -> bool:
    return any(
        table_name != order_table and order_table in {left[0], right[0]} and table_name in {left[0], right[0]}
        for left, right in allowed
    )


def _counts_customer_identifier(value) -> bool:
    if not isinstance(value, exp.Count):
        return False
    target = value.this
    columns = target.expressions if isinstance(target, exp.Distinct) else [target]
    return (
        len(columns) == 1
        and isinstance(columns[0], exp.Column)
        and columns[0].name.lower() == "customer_id"
        and _in_main(columns[0])
    )


def _matches_metric(value, expected, qualifier):
    if type(value) is not type(expected):
        return False
    if isinstance(expected, exp.Count):
        return (isinstance(value.this, exp.Star) and not any(value.this.args.values())
                and not value.expressions)
    return isinstance(expected, exp.Sum) and _column(value.this, {expected.this.name}, qualifier)


def execute_order_sql(path: Path, sql: str, catalog: dict, today: date, joins=()) -> dict:
    tree, columns, _ = _validate_order_sql(sql, catalog, today, joins)
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


def prior_order_sql(sql: str, catalog: dict, today: date, *, shift=None, joins=()) -> str:
    tree, _, dates = _validate_order_sql(sql, catalog, today, joins)
    if shift:
        for literal in dates:
            literal.set("this", shift(date.fromisoformat(literal.this)).isoformat())
    return tree.sql(dialect="duckdb", comments=False)
