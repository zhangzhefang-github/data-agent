"""Read-only catalog and profile evidence for supported DuckDB sources."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import duckdb
from pydantic import BaseModel, Field


class CatalogColumn(BaseModel):
    name: str
    data_type: str
    nullable: bool


class CatalogObject(BaseModel):
    name: str
    object_type: Literal["table", "view"]
    row_count: int
    columns: list[CatalogColumn]


class CandidateKeyProfile(BaseModel):
    object_name: str
    field_name: str
    row_count: int
    distinct_non_null_count: int
    null_count: int
    duplicate_count: int


class RelationshipProfile(BaseModel):
    source_dataset_id: str
    target_dataset_id: str
    source_field: str
    target_field: str
    cardinality: Literal["many_to_one"]
    verification_status: Literal["proposed", "profiled", "confirmed"]
    non_null_count: int
    match_count: int
    match_rate: float
    null_count: int
    orphan_count: int
    matched_parent_count: int
    multi_child_parent_count: int
    unreferenced_parent_count: int


class StagingLineage(BaseModel):
    source_object: str
    renamed_fields: dict[str, str]
    transforms: list[str] = Field(default_factory=list)
    view_sql: str


class PaymentMethodAmount(BaseModel):
    order_amount: float
    payment_amount: float


class OrderBaseline(BaseModel):
    total_orders: int
    total_amount: float
    payment_total: float
    payment_join_rows: int
    repeated_order_amount: float
    preaggregated_join_rows: int
    preaggregated_order_amount: float
    amounts_by_payment_method: dict[str, PaymentMethodAmount]


class CustomerBaseline(BaseModel):
    total_customers: int
    customers_with_orders: int
    customers_without_orders: int
    null_order_counts: int
    order_count_mismatches: int
    date_mismatches: int
    lifetime_value_mismatches: int


class JaffleShopSourceProfile(BaseModel):
    schema_name: str
    objects: list[CatalogObject]
    candidate_keys: dict[str, CandidateKeyProfile]
    relationships: dict[str, RelationshipProfile]
    staging_lineage: dict[str, StagingLineage]
    status_values: dict[str, int]
    payment_method_values: dict[str, int]
    order_baseline: OrderBaseline
    customer_baseline: CustomerBaseline


_CANDIDATE_KEYS = {
    "customers": "customer_id",
    "orders": "order_id",
    "raw_customers": "id",
    "raw_orders": "id",
    "raw_payments": "id",
    "stg_customers": "customer_id",
    "stg_orders": "order_id",
    "stg_payments": "payment_id",
}

_RELATIONSHIPS = {
    "order_mart_to_customer_mart": ("order_mart", "customer_mart", "orders", "customers", "customer_id", "customer_id"),
    "order_staging_to_customer_staging": ("order_staging", "customer_staging", "stg_orders", "stg_customers", "customer_id", "customer_id"),
    "order_source_to_customer_source": ("order_source", "customer_source", "raw_orders", "raw_customers", "user_id", "id"),
    "payment_staging_to_order_staging": ("payment_staging", "order_staging", "stg_payments", "stg_orders", "order_id", "order_id"),
    "payment_source_to_order_source": ("payment_source", "order_source", "raw_payments", "raw_orders", "order_id", "id"),
}

_STAGING_LINEAGE = {
    "stg_customers": ("raw_customers", {"id": "customer_id"}, []),
    "stg_orders": ("raw_orders", {"id": "order_id", "user_id": "customer_id"}, []),
    "stg_payments": ("raw_payments", {"id": "payment_id"}, ["amount / 100"]),
}

# 订单上的四类金额列与 stg_payments.payment_method 的对应关系。
_ORDER_AMOUNT_BY_PAYMENT_METHOD = {
    "credit_card": "credit_card_amount",
    "coupon": "coupon_amount",
    "bank_transfer": "bank_transfer_amount",
    "gift_card": "gift_card_amount",
}


def profile_jaffle_shop(path: Path) -> JaffleShopSourceProfile:
    """Profile the supported Jaffle Shop source with a read-only DuckDB connection."""
    if not path.is_file():
        raise FileNotFoundError(f"DuckDB source does not exist: {path}")
    with duckdb.connect(str(path), read_only=True) as connection:
        objects = _catalog_objects(connection)
        _require_expected_objects(objects)
        view_definitions = _view_definitions(connection)
        return JaffleShopSourceProfile(
            schema_name="main",
            objects=objects,
            candidate_keys={name: _candidate_key_profile(connection, name, field) for name, field in _CANDIDATE_KEYS.items()},
            relationships={name: _relationship_profile(connection, *specification) for name, specification in _RELATIONSHIPS.items()},
            staging_lineage=_staging_lineage(view_definitions),
            status_values=_value_counts(connection, "orders", "status"),
            payment_method_values=_value_counts(connection, "stg_payments", "payment_method"),
            order_baseline=_order_baseline(connection),
            customer_baseline=_customer_baseline(connection),
        )


def _catalog_objects(connection: duckdb.DuckDBPyConnection) -> list[CatalogObject]:
    objects = connection.execute(
        """
        SELECT table_name, table_type
        FROM information_schema.tables
        WHERE table_schema = 'main'
        ORDER BY table_name
        """
    ).fetchall()
    catalog = []
    for name, object_type in objects:
        columns = connection.execute(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'main' AND table_name = ?
            ORDER BY ordinal_position
            """,
            [name],
        ).fetchall()
        row_count = connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
        catalog.append(
            CatalogObject(
                name=name,
                object_type="view" if object_type == "VIEW" else "table",
                row_count=row_count,
                columns=[CatalogColumn(name=column, data_type=data_type, nullable=nullable == "YES") for column, data_type, nullable in columns],
            )
        )
    return catalog


def _require_expected_objects(objects: list[CatalogObject]) -> None:
    names = {item.name for item in objects}
    expected = set(_CANDIDATE_KEYS)
    missing = expected - names
    if missing:
        raise ValueError(f"Jaffle Shop source is missing expected objects: {', '.join(sorted(missing))}")


def _candidate_key_profile(connection: duckdb.DuckDBPyConnection, table: str, field: str) -> CandidateKeyProfile:
    row_count, distinct_non_null_count, null_count, duplicate_count = connection.execute(
        f"""
        SELECT count(*), count(DISTINCT \"{field}\"), count(*) - count(\"{field}\"),
               count(\"{field}\") - count(DISTINCT \"{field}\")
        FROM \"{table}\"
        """
    ).fetchone()
    return CandidateKeyProfile(
        object_name=table,
        field_name=field,
        row_count=row_count,
        distinct_non_null_count=distinct_non_null_count,
        null_count=null_count,
        duplicate_count=duplicate_count,
    )


def _relationship_profile(
    connection: duckdb.DuckDBPyConnection,
    source_dataset_id: str,
    target_dataset_id: str,
    source_table: str,
    target_table: str,
    source_field: str,
    target_field: str,
) -> RelationshipProfile:
    non_null_count, match_count, null_count, orphan_count, matched_parent_count = connection.execute(
        f"""
        SELECT count(source.\"{source_field}\"), count(target.\"{target_field}\"),
               count(*) - count(source.\"{source_field}\"),
               count(*) FILTER (WHERE source.\"{source_field}\" IS NOT NULL AND target.\"{target_field}\" IS NULL),
               count(DISTINCT target.\"{target_field}\")
        FROM \"{source_table}\" AS source
        LEFT JOIN \"{target_table}\" AS target
          ON source.\"{source_field}\" = target.\"{target_field}\"
        """
    ).fetchone()
    multi_child_parent_count = connection.execute(
        f"""
        SELECT count(*) FROM (
            SELECT \"{source_field}\"
            FROM \"{source_table}\"
            WHERE \"{source_field}\" IS NOT NULL
            GROUP BY \"{source_field}\"
            HAVING count(*) > 1
        )
        """
    ).fetchone()[0]
    unreferenced_parent_count = connection.execute(
        f"""
        SELECT count(*)
        FROM \"{target_table}\" AS target
        WHERE NOT EXISTS (
            SELECT 1 FROM \"{source_table}\" AS source
            WHERE source.\"{source_field}\" = target.\"{target_field}\"
        )
        """
    ).fetchone()[0]
    return RelationshipProfile(
        source_dataset_id=source_dataset_id,
        target_dataset_id=target_dataset_id,
        source_field=source_field,
        target_field=target_field,
        cardinality="many_to_one",
        verification_status="profiled",
        non_null_count=non_null_count,
        match_count=match_count,
        match_rate=match_count / non_null_count if non_null_count else 0.0,
        null_count=null_count,
        orphan_count=orphan_count,
        matched_parent_count=matched_parent_count,
        multi_child_parent_count=multi_child_parent_count,
        unreferenced_parent_count=unreferenced_parent_count,
    )


def _view_definitions(connection: duckdb.DuckDBPyConnection) -> dict[str, str]:
    return dict(
        connection.execute(
            """
            SELECT view_name, sql
            FROM duckdb_views()
            WHERE schema_name = 'main' AND view_name LIKE 'stg_%'
            """
        ).fetchall()
    )


def _staging_lineage(view_definitions: dict[str, str]) -> dict[str, StagingLineage]:
    missing = set(_STAGING_LINEAGE) - set(view_definitions)
    if missing:
        raise ValueError(f"Jaffle Shop source is missing expected staging views: {', '.join(sorted(missing))}")
    return {
        view_name: StagingLineage(
            source_object=source_object,
            renamed_fields=renamed_fields,
            transforms=transforms,
            view_sql=view_definitions[view_name],
        )
        for view_name, (source_object, renamed_fields, transforms) in _STAGING_LINEAGE.items()
    }


def _value_counts(connection: duckdb.DuckDBPyConnection, table: str, field: str) -> dict[str, int]:
    return dict(
        connection.execute(
            f'SELECT "{field}", count(*) FROM "{table}" GROUP BY "{field}" ORDER BY "{field}"'
        ).fetchall()
    )


def _order_baseline(connection: duckdb.DuckDBPyConnection) -> OrderBaseline:
    total_orders, total_amount = connection.execute("SELECT count(*), sum(amount) FROM orders").fetchone()
    payment_total = connection.execute("SELECT sum(amount) FROM stg_payments").fetchone()[0]
    payment_join_rows, repeated_order_amount = connection.execute(
        """
        SELECT count(*), sum(orders.amount)
        FROM stg_orders
        JOIN orders USING (order_id)
        LEFT JOIN stg_payments USING (order_id)
        """
    ).fetchone()
    preaggregated_join_rows, preaggregated_order_amount = connection.execute(
        """
        WITH paid_per_order AS (
            SELECT order_id, sum(amount) AS paid_amount
            FROM stg_payments
            GROUP BY order_id
        )
        SELECT count(*), sum(orders.amount)
        FROM stg_orders
        JOIN orders USING (order_id)
        LEFT JOIN paid_per_order USING (order_id)
        """
    ).fetchone()
    # 按支付方式分别汇总，保留订单列与支付金额两侧，供核对而不是在这里判等。
    order_amount_columns = ", ".join(
        f'sum("{column}")' for column in _ORDER_AMOUNT_BY_PAYMENT_METHOD.values()
    )
    order_amounts = connection.execute(f"SELECT {order_amount_columns} FROM orders").fetchone()
    payment_amounts = dict(
        connection.execute(
            """
            SELECT payment_method, sum(amount)
            FROM stg_payments
            GROUP BY payment_method
            """
        ).fetchall()
    )
    amounts_by_payment_method = {
        method: PaymentMethodAmount(
            order_amount=order_amounts[index],
            payment_amount=payment_amounts[method],
        )
        for index, method in enumerate(_ORDER_AMOUNT_BY_PAYMENT_METHOD)
    }
    return OrderBaseline(
        total_orders=total_orders,
        total_amount=total_amount,
        payment_total=payment_total,
        payment_join_rows=payment_join_rows,
        repeated_order_amount=repeated_order_amount,
        preaggregated_join_rows=preaggregated_join_rows,
        preaggregated_order_amount=preaggregated_order_amount,
        amounts_by_payment_method=amounts_by_payment_method,
    )


def _customer_baseline(connection: duckdb.DuckDBPyConnection) -> CustomerBaseline:
    values = connection.execute(
        """
        WITH customer_orders AS (
            SELECT customer_id, count(*) AS order_count, min(order_date) AS first_order,
                   max(order_date) AS most_recent_order, sum(amount) AS lifetime_value
            FROM orders
            GROUP BY customer_id
        )
        SELECT count(*), count(*) FILTER (WHERE customer_orders.customer_id IS NOT NULL),
               count(*) FILTER (WHERE customer_orders.customer_id IS NULL),
               count(*) FILTER (WHERE customers.number_of_orders IS NULL),
               count(*) FILTER (WHERE customers.number_of_orders IS DISTINCT FROM customer_orders.order_count),
               count(*) FILTER (WHERE customers.first_order IS DISTINCT FROM customer_orders.first_order
                                  OR customers.most_recent_order IS DISTINCT FROM customer_orders.most_recent_order),
               count(*) FILTER (WHERE customers.customer_lifetime_value IS DISTINCT FROM customer_orders.lifetime_value)
        FROM customers
        LEFT JOIN customer_orders USING (customer_id)
        """
    ).fetchone()
    return CustomerBaseline(
        total_customers=values[0],
        customers_with_orders=values[1],
        customers_without_orders=values[2],
        null_order_counts=values[3],
        order_count_mismatches=values[4],
        date_mismatches=values[5],
        lifetime_value_mismatches=values[6],
    )
