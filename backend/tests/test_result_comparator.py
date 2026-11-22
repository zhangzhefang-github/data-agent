from datetime import date, datetime, time
from decimal import Decimal

from app.evaluation.result_comparator import ComparisonPolicy, compare_results


def test_different_column_sets_do_not_match():
    comparison = compare_results(
        ['customer_id', 'order_amount', 'order_rows'],
        [[1, 100, 2]],
        ['customer_id', 'order_amount'],
        [[1, 100]],
        ComparisonPolicy(),
    )
    assert comparison.matched is False


def test_different_full_row_does_not_match():
    comparison = compare_results(
        ['customer_id', 'order_amount'],
        [[1, 100]],
        ['customer_id', 'order_amount'],
        [[1, 250]],
        ComparisonPolicy(),
    )
    assert comparison.matched is False


def test_same_values_match_after_column_names_are_aligned():
    comparison = compare_results(
        ['order_amount', 'customer_id'],
        [[100, 1]],
        ['customer_id', 'order_amount'],
        [[1, 100]],
        ComparisonPolicy(),
    )
    assert comparison.matched is True


def test_same_rows_match_in_any_order_when_order_is_not_required():
    comparison = compare_results(
        ['customer_id', 'order_amount'],
        [[2, 250], [1, 100]],
        ['customer_id', 'order_amount'],
        [[1, 100], [2, 250]],
        ComparisonPolicy(ordered=False),
    )
    assert comparison.matched is True


def test_different_order_does_not_match_when_order_is_required():
    comparison = compare_results(
        ['customer_id', 'order_amount'],
        [[2, 250], [1, 100]],
        ['customer_id', 'order_amount'],
        [[1, 100], [2, 250]],
        ComparisonPolicy(ordered=True),
    )
    assert comparison.matched is False


def test_duplicate_rows_are_not_collapsed_into_one_row():
    comparison = compare_results(
        ['customer_id', 'order_amount'],
        [[1, 100]],
        ['customer_id', 'order_amount'],
        [[1, 100], [1, 100]],
        ComparisonPolicy(duplicate_keys=('customer_id',)),
    )
    assert comparison.matched is False


def test_swapped_customer_amounts_do_not_match_when_count_and_total_match():
    columns = ['customer_id', 'order_amount']
    expected_rows = [[1, 100], [2, 250]]
    actual_rows = [[1, 250], [2, 100]]
    comparison = compare_results(
        columns,
        actual_rows,
        columns,
        expected_rows,
        ComparisonPolicy(dimension_keys=('customer_id',)),
    )
    assert comparison.matched is False


def test_paired_customer_amounts_match_in_any_order():
    columns = ['customer_id', 'order_amount']
    comparison = compare_results(
        columns,
        [[2, Decimal('250')], [1, Decimal('100')]],
        columns,
        [[1, Decimal('100')], [2, Decimal('250')]],
        ComparisonPolicy(dimension_keys=('customer_id',), ordered=False),
    )
    assert comparison.matched is True


def test_full_row_does_not_match_when_only_the_status_differs():
    columns = ['customer_id', 'status', 'order_amount']
    comparison = compare_results(
        columns,
        [[1, '已下单', Decimal('100')]],
        columns,
        [[1, '已完成', Decimal('100')]],
        ComparisonPolicy(dimension_keys=('customer_id',)),
    )
    assert comparison.matched is False


def test_duplicate_customer_amounts_do_not_match_when_the_total_matches():
    columns = ['customer_id', 'order_amount']
    comparison = compare_results(
        columns,
        [[1, Decimal('70')], [1, Decimal('70')]],
        columns,
        [[1, Decimal('100')], [1, Decimal('40')]],
        ComparisonPolicy(duplicate_keys=('customer_id',)),
    )
    assert comparison.matched is False


def test_null_matches_null():
    comparison = compare_results(
        ['customer_id', 'order_amount'],
        [[None, 100], [2, None]],
        ['customer_id', 'order_amount'],
        [[2, None], [None, 100]],
        ComparisonPolicy(),
    )
    assert comparison.matched is True


def test_null_does_not_match_zero():
    comparison = compare_results(
        ['order_amount'],
        [[None]],
        ['order_amount'],
        [[0]],
        ComparisonPolicy(),
    )
    assert comparison.matched is False


def test_null_does_not_match_empty_string():
    comparison = compare_results(
        ['customer_id'],
        [[None]],
        ['customer_id'],
        [['']],
        ComparisonPolicy(),
    )
    assert comparison.matched is False


def test_amounts_within_absolute_tolerance_match():
    comparison = compare_results(
        ['order_amount'],
        [[Decimal('100.4')]],
        ['order_amount'],
        [[Decimal('100.0')]],
        ComparisonPolicy(absolute_tolerance=Decimal('0.5')),
    )
    assert comparison.matched is True


def test_amounts_outside_absolute_tolerance_do_not_match():
    comparison = compare_results(
        ['order_amount'],
        [[Decimal('100.6')]],
        ['order_amount'],
        [[Decimal('100.0')]],
        ComparisonPolicy(absolute_tolerance=Decimal('0.5')),
    )
    assert comparison.matched is False


def test_amounts_within_relative_tolerance_match():
    comparison = compare_results(
        ['order_amount'],
        [[Decimal('101')]],
        ['order_amount'],
        [[Decimal('100')]],
        ComparisonPolicy(relative_tolerance=Decimal('0.01')),
    )
    assert comparison.matched is True


def test_amounts_outside_relative_tolerance_do_not_match():
    comparison = compare_results(
        ['order_amount'],
        [[Decimal('102')]],
        ['order_amount'],
        [[Decimal('100')]],
        ComparisonPolicy(relative_tolerance=Decimal('0.01')),
    )
    assert comparison.matched is False


def test_date_string_matches_date_value_on_the_same_day():
    comparison = compare_results(
        ['order_date'],
        [['2018-04-09']],
        ['order_date'],
        [[date(2018, 4, 9)]],
        ComparisonPolicy(),
    )
    assert comparison.matched is True


def test_date_string_does_not_match_a_different_day():
    comparison = compare_results(
        ['order_date'],
        [['2018-04-09']],
        ['order_date'],
        [[date(2018, 4, 10)]],
        ComparisonPolicy(),
    )
    assert comparison.matched is False


def test_same_days_match_in_any_order_when_order_is_not_required():
    comparison = compare_results(
        ['order_date'],
        [['2018-04-10'], ['2018-04-09']],
        ['order_date'],
        [[date(2018, 4, 9)], [date(2018, 4, 10)]],
        ComparisonPolicy(ordered=False),
    )
    assert comparison.matched is True


def test_unordered_comparison_accepts_a_date_and_text_in_one_column():
    comparison = compare_results(
        ['order_date'],
        [[date(2018, 4, 9)], ['未下单']],
        ['order_date'],
        [['未下单'], ['2018-04-09']],
        ComparisonPolicy(ordered=False),
    )
    assert comparison.matched is True


def test_clock_time_string_matches_time_value():
    comparison = compare_results(
        ['paid_at'],
        [['08:30:00']],
        ['paid_at'],
        [[time(8, 30)]],
        ComparisonPolicy(),
    )
    assert comparison.matched is True


def test_different_clock_times_do_not_match():
    comparison = compare_results(
        ['paid_at'],
        [['08:30:00']],
        ['paid_at'],
        [[time(8, 31)]],
        ComparisonPolicy(),
    )
    assert comparison.matched is False


def test_datetime_string_matches_datetime_value():
    comparison = compare_results(
        ['order_date'],
        [['2018-04-09T08:30:00']],
        ['order_date'],
        [[datetime(2018, 4, 9, 8, 30)]],
        ComparisonPolicy(),
    )
    assert comparison.matched is True


def test_datetimes_do_not_match_when_the_clock_time_differs():
    comparison = compare_results(
        ['order_date'],
        [[datetime(2018, 4, 9, 8, 30)]],
        ['order_date'],
        [[datetime(2018, 4, 9, 9, 0)]],
        ComparisonPolicy(),
    )
    assert comparison.matched is False


def test_decimals_match_at_the_given_scale():
    comparison = compare_results(
        ['order_amount'],
        [[Decimal('10.114')]],
        ['order_amount'],
        [[Decimal('10.113')]],
        ComparisonPolicy(decimal_places=2),
    )
    assert comparison.matched is True


def test_decimals_do_not_match_when_the_scale_differs():
    comparison = compare_results(
        ['order_amount'],
        [[Decimal('10.114')]],
        ['order_amount'],
        [[Decimal('10.116')]],
        ComparisonPolicy(decimal_places=2),
    )
    assert comparison.matched is False


def test_dimension_key_stays_exact_when_amounts_use_tolerance():
    comparison = compare_results(
        ['customer_id', 'order_amount'],
        [[1, Decimal('100')], [2, Decimal('100')]],
        ['customer_id', 'order_amount'],
        [[1, Decimal('100')], [6, Decimal('100')]],
        ComparisonPolicy(absolute_tolerance=Decimal('5'), dimension_keys=('customer_id',)),
    )
    assert comparison.matched is False


def test_duplicate_key_stays_exact_when_amounts_use_tolerance():
    comparison = compare_results(
        ['customer_id', 'order_amount'],
        [[1, Decimal('100')], [2, Decimal('100')]],
        ['customer_id', 'order_amount'],
        [[1, Decimal('100')], [6, Decimal('100')]],
        ComparisonPolicy(absolute_tolerance=Decimal('5'), duplicate_keys=('customer_id',)),
    )
    assert comparison.matched is False


def test_unordered_rows_match_within_tolerance_when_value_order_differs():
    columns = ['customer_id', 'order_rows', 'order_amount']
    comparison = compare_results(
        columns,
        [[1, 0, Decimal('0')], [1, 0, Decimal('1')]],
        columns,
        [[1, 0, Decimal('2')], [1, 1, Decimal('0')]],
        ComparisonPolicy(absolute_tolerance=Decimal('1'), duplicate_keys=('customer_id',)),
    )
    assert comparison.matched is True
