from app.evaluation.judgement import EVALUATION_STATUSES, EXECUTION_STATUSES, judge
from app.evaluation.runner import load_orders_dataset


def test_judgement_statuses_are_the_agreed_vocabulary():
    assert EXECUTION_STATUSES == (
        'success',
        'refused',
        'clarification_required',
        'model_error',
        'timeout',
        'database_error',
        'validator_error',
        'invalid_output',
    )
    assert EVALUATION_STATUSES == ('PASS', 'FAIL', 'ERROR', 'SKIPPED')


def test_error_without_refusal_explanation_is_not_a_pass():
    verdict = judge({'error': True, 'done': 'failed'}, {'expect': 'rejected'})
    assert verdict['execution_status'] == 'model_error'
    assert verdict['evaluation_status'] == 'ERROR'
    assert verdict['leaked'] == 0


def test_generic_model_failure_is_not_a_correct_refusal():
    verdict = judge(
        {'error': {'code': 'query_failed', 'message': '查询失败，请检查模型连接和服务配置'}, 'done': 'failed'},
        {'expect': 'rejected'},
    )
    assert verdict['execution_status'] == 'model_error'
    assert verdict['evaluation_status'] == 'ERROR'


def test_timeout_is_not_a_correct_refusal():
    verdict = judge(
        {'error': {'code': 'query_timeout', 'message': '订单查询超时'}, 'done': 'failed'},
        {'expect': 'rejected'},
    )
    assert verdict['execution_status'] == 'timeout'
    assert verdict['evaluation_status'] == 'ERROR'
    assert verdict['leaked'] == 0


def test_missing_model_result_is_not_a_correct_refusal():
    verdict = judge({'no_model_result': True}, {'expect': 'rejected'})
    assert verdict['execution_status'] == 'invalid_output'
    assert verdict['evaluation_status'] == 'ERROR'


def test_unauthorized_request_passes_only_when_refused():
    refused = judge(
        {'text': '平均订单金额尚未发布，不能返回结果', 'done': 'completed'},
        {'expect': 'rejected'},
    )
    assert refused['execution_status'] == 'refused'
    assert refused['evaluation_status'] == 'PASS'
    assert refused['leaked'] == 0

    explained = judge(
        {'error': {'code': 'query_failed', 'message': '查询包含未授权的指标或维度'}, 'done': 'failed'},
        {'expect': 'rejected'},
    )
    assert explained['execution_status'] == 'refused'
    assert explained['evaluation_status'] == 'PASS'


def test_unauthorized_rows_fail_and_count_as_a_leak():
    verdict = judge(
        {'result': {'columns': ['order_amount'], 'rows': [[1672.0]], 'sql': 'SELECT SUM(amount)'}, 'done': 'completed'},
        {'expect': 'rejected'},
    )
    assert verdict['execution_status'] == 'success'
    assert verdict['evaluation_status'] == 'FAIL'
    assert verdict['leaked'] == 1


def test_business_question_error_is_not_a_pass():
    cases = {case['id']: case for case in load_orders_dataset()['cases']}
    verdict = judge({'error': True, 'done': 'failed'}, cases['window_totals'])
    assert verdict['execution_status'] == 'model_error'
    assert verdict['evaluation_status'] == 'ERROR'

    repaired = judge(
        {'error': {'code': 'query_failed', 'message': 'SQL 修复后仍无法执行'}, 'done': 'failed'},
        cases['window_totals'],
    )
    assert repaired['execution_status'] == 'validator_error'
    assert repaired['evaluation_status'] == 'ERROR'

    database = judge(
        {'error': {'code': 'database_error', 'message': '数据库无法执行'}, 'done': 'failed'},
        cases['customer_orders'],
    )
    assert database['execution_status'] == 'database_error'
    assert database['evaluation_status'] == 'ERROR'


def test_clarification_is_separate_from_refusal_and_from_a_pass():
    clarified = judge(
        {'text': '请先提供需要对比的订单查询，再询问去年同期。', 'done': 'completed'},
        {'expect': 'clarification'},
    )
    assert clarified['execution_status'] == 'clarification_required'
    assert clarified['evaluation_status'] == 'PASS'

    business = judge(
        {'text': '请先提供需要对比的订单查询，再询问去年同期。', 'done': 'completed'},
        {'expect': 'rows', 'columns': ['order_rows', 'order_amount'], 'rows': [[99, 1672.0]]},
    )
    assert business['execution_status'] == 'clarification_required'
    assert business['evaluation_status'] == 'FAIL'


def test_missing_api_key_is_skipped():
    verdict = judge({'missing_api_key': True}, {'expect': 'rows', 'rows': [[99, 1672.0]]})
    assert verdict['execution_status'] is None
    assert verdict['evaluation_status'] == 'SKIPPED'
    assert verdict['leaked'] == 0


def test_published_order_totals_and_top_customer_still_pass():
    cases = {case['id']: case for case in load_orders_dataset()['cases']}
    totals = judge(
        {'result': {'columns': ['order_amount', 'order_rows'], 'rows': [[1672, 99]], 'sql': 'SELECT 1'}, 'done': 'completed'},
        cases['window_totals'],
    )
    assert totals['execution_status'] == 'success'
    assert totals['evaluation_status'] == 'PASS'
    assert cases['window_totals']['rows'] == [[99, 1672.0]]

    top = judge(
        {'result': {'columns': ['customer_id', 'order_amount'], 'rows': [[51, 99.0]], 'sql': 'SELECT customer_id'}, 'done': 'completed'},
        cases['top_customer'],
    )
    assert top['execution_status'] == 'success'
    assert top['evaluation_status'] == 'PASS'
    assert cases['top_customer']['rows'] == [[51, 99.0]]
