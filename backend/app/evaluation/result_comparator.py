"""比较实际结果与预期结果。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any, Sequence


@dataclass(frozen=True)
class ComparisonPolicy:
    """结果比较策略。"""

    ordered: bool = False
    absolute_tolerance: Decimal = Decimal('0')
    relative_tolerance: Decimal = Decimal('0')
    decimal_places: int | None = None
    dimension_keys: tuple[str, ...] = ()
    duplicate_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class Comparison:
    """比较结论。matched 为 False 时表示不匹配。"""

    matched: bool


def _align_rows(
    actual_columns: Sequence[str],
    actual_rows: Sequence[Sequence[Any]],
    expected_columns: Sequence[str],
) -> list[list[Any]] | None:
    """按预期列名重排实际行。列集合不一致或行宽不够时无法对齐。"""
    if sorted(actual_columns) != sorted(expected_columns):
        return None
    used: set[int] = set()
    positions: list[int] = []
    for name in expected_columns:
        found = next(
            (index for index, actual_name in enumerate(actual_columns) if index not in used and actual_name == name),
            None,
        )
        if found is None:
            return None
        used.add(found)
        positions.append(found)
    aligned: list[list[Any]] = []
    for row in actual_rows:
        if any(index >= len(row) for index in positions):
            return None
        aligned.append([row[index] for index in positions])
    return aligned


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _as_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(value)


def _as_date_only(value: Any) -> date | None:
    """日期值或 YYYY-MM-DD 字符串。带时间的值留给时间比较。"""
    if isinstance(value, datetime):
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _as_time(value: Any) -> time | None:
    if isinstance(value, datetime):
        return None
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        # 紧凑日期会被 time.fromisoformat 误读成时钟，先排除日期。
        if _as_date_only(value) is not None:
            return None
        try:
            return time.fromisoformat(value)
        except ValueError:
            return None
    return None


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and any(separator in value for separator in ('T', ' ')):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _values_equal(actual: Any, expected: Any, policy: ComparisonPolicy, *, exact_number: bool = False) -> bool:
    """空值只等于空值。数字按绝对误差和相对误差比较，相对误差以预期值为基准。

    维度键和重复键上的数字不吃误差。
    """
    if actual is None or expected is None:
        return actual is None and expected is None
    if _is_number(actual) and _is_number(expected):
        actual_number = _as_decimal(actual)
        expected_number = _as_decimal(expected)
        if exact_number:
            return actual_number == expected_number
        if policy.decimal_places is not None:
            quantum = Decimal(1).scaleb(-policy.decimal_places)
            actual_number = actual_number.quantize(quantum, rounding=ROUND_HALF_EVEN)
            expected_number = expected_number.quantize(quantum, rounding=ROUND_HALF_EVEN)
        difference = abs(actual_number - expected_number)
        limit = policy.absolute_tolerance + policy.relative_tolerance * abs(expected_number)
        return difference <= limit
    actual_day = _as_date_only(actual)
    expected_day = _as_date_only(expected)
    if actual_day is not None and expected_day is not None:
        return actual_day == expected_day
    actual_time = _as_time(actual)
    expected_time = _as_time(expected)
    if actual_time is not None and expected_time is not None:
        return actual_time == expected_time
    actual_at = _as_datetime(actual)
    expected_at = _as_datetime(expected)
    if actual_at is not None and expected_at is not None:
        try:
            return actual_at == expected_at
        except TypeError:
            return False
    return actual == expected


def _key_indexes(columns: Sequence[str], policy: ComparisonPolicy) -> set[int] | None:
    indexes: set[int] = set()
    for name in (*policy.dimension_keys, *policy.duplicate_keys):
        if name not in columns:
            return None
        indexes.add(columns.index(name))
    return indexes


def _rows_equal(
    actual: Sequence[Any],
    expected: Sequence[Any],
    policy: ComparisonPolicy,
    key_indexes: set[int],
) -> bool:
    if len(actual) != len(expected):
        return False
    return all(
        _values_equal(actual_value, expected_value, policy, exact_number=index in key_indexes)
        for index, (actual_value, expected_value) in enumerate(zip(actual, expected))
    )


def _sort_token(value: Any) -> tuple:
    """给无序比较一个不会因类型不同而失败的顺序。日期字符串和日期值放在同一组。"""
    if value is None:
        return (0,)
    if isinstance(value, bool):
        return (1, value)
    if _is_number(value):
        return (2, _as_decimal(value))
    if isinstance(value, datetime):
        return (3, value.isoformat())
    if isinstance(value, date):
        return (4, value.isoformat())
    if isinstance(value, time):
        return (5, value.isoformat())
    if isinstance(value, str):
        day = _as_date_only(value)
        if day is not None:
            return (4, day.isoformat())
        clock = _as_time(value)
        if clock is not None:
            return (5, clock.isoformat())
        moment = _as_datetime(value)
        if moment is not None:
            return (3, moment.isoformat())
        return (6, value)
    return (7, type(value).__name__, repr(value))


def _tolerance_active(policy: ComparisonPolicy) -> bool:
    return policy.absolute_tolerance != 0 or policy.relative_tolerance != 0 or policy.decimal_places is not None


def _unordered_match(
    actual_rows: list[list[Any]],
    expected_rows: list[list[Any]],
    policy: ComparisonPolicy,
    key_indexes: set[int],
) -> bool:
    """误差比较不可排序配对，按行找一一对应。"""
    remaining = list(expected_rows)

    def match_from(index: int) -> bool:
        if index == len(actual_rows):
            return True
        for position, expected_row in enumerate(remaining):
            if not _rows_equal(actual_rows[index], expected_row, policy, key_indexes):
                continue
            remaining.pop(position)
            if match_from(index + 1):
                return True
            remaining.insert(position, expected_row)
        return False

    return match_from(0)


def compare_results(
    actual_columns: Sequence[str],
    actual_rows: Sequence[Sequence[Any]],
    expected_columns: Sequence[str],
    expected_rows: Sequence[Sequence[Any]],
    policy: ComparisonPolicy | None = None,
) -> Comparison:
    """列名对齐后比较两张结果表。"""
    policy = policy or ComparisonPolicy()
    aligned = _align_rows(actual_columns, actual_rows, expected_columns)
    key_indexes = None if aligned is None else _key_indexes(expected_columns, policy)
    if aligned is None or key_indexes is None or len(aligned) != len(expected_rows):
        return Comparison(matched=False)
    expected = [list(row) for row in expected_rows]
    if not policy.ordered:
        if _tolerance_active(policy):
            matched = _unordered_match(aligned, expected, policy, key_indexes)
            return Comparison(matched=matched)
        aligned = sorted(aligned, key=lambda row: tuple(_sort_token(value) for value in row))
        expected = sorted(expected, key=lambda row: tuple(_sort_token(value) for value in row))
    for actual, expected_row in zip(aligned, expected):
        if not _rows_equal(actual, expected_row, policy, key_indexes):
            return Comparison(matched=False)
    return Comparison(matched=True)
