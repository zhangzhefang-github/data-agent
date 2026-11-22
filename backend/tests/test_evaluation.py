from app.evaluation.runner import load_orders_dataset, run_deterministic, score_orders_case


def test_golden_dataset_uses_fixed_data_and_passes_all_cases():
    report = run_deterministic()
    assert report['mode'] == 'deterministic'
    assert len(report['cases']) >= 10
    assert all(case['status'] == 'passed' for case in report['cases'])
    assert all(case['sql'] and case['actual_rows'] == case['expected_rows'] for case in report['cases'])


def test_wrong_expected_result_is_reported_as_database_execution_failure(tmp_path, monkeypatch):
    import json
    from app.evaluation import runner

    dataset = runner.load_dataset()
    dataset['cases'] = [dataset['cases'][0]]
    dataset['cases'][0]['expected_rows'] = [['北京', -1]]
    dataset_path = tmp_path / 'golden.json'
    dataset_path.write_text(json.dumps(dataset, ensure_ascii=False))
    monkeypatch.setattr(runner, 'DATASET', dataset_path)

    case = runner.run_deterministic()['cases'][0]
    assert case['status'] == 'failed'
    assert case['failure_stage'] == 'database_execution'
    assert case['execution_status'] == 'passed'
    assert case['actual_rows'] != case['expected_rows']


def test_live_without_api_key_is_explicitly_skipped(monkeypatch):
    from app.evaluation.runner import run_live

    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    cases = run_live()['cases']
    assert len(cases) >= 10
    assert all(case['status'] == 'skipped' for case in cases)


def test_live_evaluation_uses_fixture_with_external_ads_configured(monkeypatch):
    from app import main
    from app.evaluation import runner

    case = runner.load_dataset()['cases'][0]
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    monkeypatch.setattr(main, 'DATABASE_URL', 'postgresql://example.invalid/enterprise')
    monkeypatch.setattr(main, 'EXTERNAL_SQLITE_PATH', '/enterprise/sales.db')

    def fake_send(client, question, conversation_id=None):
        assert main.DATABASE_URL is None
        assert main.EXTERNAL_SQLITE_PATH is None
        assert main.DATABASE.name == 'sales.db'
        return {'error': True}

    monkeypatch.setattr(runner, 'send', fake_send)
    report = runner.run_live(case['id'])
    assert report['cases'][0]['status'] == 'failed'
    assert main.DATABASE_URL == 'postgresql://example.invalid/enterprise'


def test_orders_dataset_scores_rows_aggregates_rejection_and_followup():
    cases = {case['id']: case for case in load_orders_dataset()['cases']}
    assert set(cases) >= {
        'window_totals', 'by_status', 'payment_amounts', 'by_customer', 'customer_orders',
        'top_customer', 'previous_period', 'query_scope', 'reject_month', 'reject_nps',
        'reject_average', 'reject_staging', 'reject_payment_join',
    }

    totals = score_orders_case(cases['window_totals'], {'result': {
        'columns': ['order_amount', 'order_rows'],
        'rows': [[1672, 99]],
        'sql': 'SELECT 1',
    }})
    assert totals['status'] == 'passed'

    status = score_orders_case(cases['by_status'], {'result': {
        'columns': ['order_amount', 'status', 'order_rows'],
        'rows': [
            [49.0, '已退货', 4],
            [1103.0, '已完成', 67],
            [38.0, '退货中', 2],
            [198.0, '已发货', 13],
            [284.0, '已下单', 13],
        ],
        'sql': 'SELECT status',
    }})
    assert status['status'] == 'passed'

    customers = score_orders_case(cases['by_customer'], {'result': {
        'columns': ['customer_id', 'order_rows', 'order_amount'],
        'rows': [[1, 50, 800.0], [2, 49, 872.0]],
        'sql': 'SELECT customer_id',
    }})
    assert customers['status'] == 'failed'
    assert customers['failure_stage'] == 'result'

    by_customer = cases['by_customer']
    assert by_customer['columns'] == ['customer_id', 'order_rows', 'order_amount']
    assert by_customer['row_count'] == 62
    assert by_customer['sums'] == {'order_rows': 99, 'order_amount': 1672.0}
    assert len(by_customer['rows']) == 62
    assert sum(row[1] for row in by_customer['rows']) == 99
    assert sum(row[2] for row in by_customer['rows']) == 1672.0

    # 行数和合计相同，但客户与金额不是快照里的对应关系。
    mismatched_customers = score_orders_case(by_customer, {'result': {
        'columns': ['order_amount', 'customer_id', 'order_rows'],
        'rows': [[1.0, index, 1] for index in range(61)] + [[1611.0, 61, 38]],
        'sql': 'SELECT customer_id',
    }})
    assert mismatched_customers['status'] == 'failed'
    assert mismatched_customers['failure_stage'] == 'result'

    matched_customers = score_orders_case(by_customer, {'result': {
        'columns': ['order_amount', 'customer_id', 'order_rows'],
        'rows': [[row[2], row[0], row[1]] for row in by_customer['rows']],
        'sql': 'SELECT customer_id',
    }})
    assert matched_customers['status'] == 'passed'

    bare_error = score_orders_case(cases['reject_average'], {'error': True, 'done': 'failed'})
    assert bare_error['evaluation_status'] == 'ERROR'
    assert bare_error['execution_status'] == 'model_error'
    refused = score_orders_case(cases['reject_average'], {
        'text': '平均订单金额尚未发布，不能返回结果',
        'done': 'completed',
    })
    assert refused['execution_status'] == 'refused'
    assert refused['evaluation_status'] == 'PASS'
    assert refused['status'] == 'passed'
    leaked = score_orders_case(cases['reject_payment_join'], {'result': {
        'columns': ['order_amount'], 'rows': [[1672.0]], 'sql': 'SELECT SUM(amount)',
    }})
    assert leaked['failure_stage'] == 'rejection'
    assert leaked['execution_status'] == 'success'
    assert leaked['evaluation_status'] == 'FAIL'
    assert leaked['leaked'] == 1

    followup = score_orders_case(cases['previous_period'], {'result': {
        'columns': ['order_rows', 'order_amount'],
        'rows': [],
        'sql': "WHERE order_date BETWEEN DATE '2017-01-01' AND DATE '2017-04-09'",
    }})
    assert followup['status'] == 'passed'
    shifted = score_orders_case(cases['previous_period'], {'result': {
        'columns': ['order_rows', 'order_amount'],
        'rows': [],
        'sql': "WHERE order_date BETWEEN DATE '2018-01-01' AND DATE '2018-04-09'",
    }})
    assert shifted['failure_stage'] == 'prior_period'

    scope = score_orders_case(cases['query_scope'], {'text': '可查询的数据范围'}, '可查询的数据范围')
    assert scope['status'] == 'passed'
    assert score_orders_case(cases['query_scope'], {'text': '改写后的范围'}, '可查询的数据范围')['failure_stage'] == 'result'


def test_orders_evaluation_skips_without_duckdb_or_model_key(monkeypatch):
    from app.evaluation import runner

    monkeypatch.delenv('SALES_DUCKDB_PATH', raising=False)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    skipped = runner.run_orders('window_totals')['cases'][0]
    assert skipped['status'] == 'skipped'
    assert skipped['reason'] == 'SALES_DUCKDB_PATH 未设置'

    monkeypatch.setenv('SALES_DUCKDB_PATH', '/tmp/jaffle_shop.duckdb')
    skipped_report = runner.run_orders('window_totals')
    skipped_model = skipped_report['cases'][0]
    assert skipped_model['status'] == 'skipped'
    assert skipped_model['evaluation_status'] == 'SKIPPED'
    assert skipped_model['reason'] == 'OPENAI_API_KEY 未设置'
    assert skipped_report['summary']['PASS'] == 0


def test_orders_evaluation_uses_the_order_duckdb_not_the_sales_fixture(monkeypatch):
    from app import main
    from app.evaluation import runner

    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    monkeypatch.setenv('SALES_DUCKDB_PATH', '/data/jaffle_shop.duckdb')
    monkeypatch.setattr(main, 'DATABASE_URL', 'postgresql://example.invalid/enterprise')

    def fake_send(client, question, conversation_id=None):
        assert main.CATALOG['id'] == 'orders'
        assert main.DUCKDB_PATH == '/data/jaffle_shop.duckdb'
        assert main.DATABASE_URL is None
        return {'error': True, 'done': 'failed'}

    monkeypatch.setattr(runner, 'send', fake_send)
    report = runner.run_orders('window_totals')
    assert report['mode'] == 'orders'
    assert report['cases'][0]['evaluation_status'] == 'ERROR'
    assert report['cases'][0]['execution_status'] == 'model_error'
    assert report['cases'][0]['failure_stage'] is None
    assert report['summary']['PASS'] == 0
    assert main.DATABASE_URL == 'postgresql://example.invalid/enterprise'


def test_orders_timeout_and_empty_process_output_are_not_correct_refusals(monkeypatch):
    import subprocess

    from app.evaluation import runner

    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    monkeypatch.setenv('SALES_DUCKDB_PATH', '/tmp/jaffle_shop.duckdb')

    def time_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd='runner', timeout=120)

    monkeypatch.setattr(runner.subprocess, 'run', time_out)
    timed_out = runner.run_orders()
    rejected = next(case for case in timed_out['cases'] if case['id'] == 'reject_average')
    business = next(case for case in timed_out['cases'] if case['id'] == 'window_totals')
    assert rejected['execution_status'] == 'timeout'
    assert rejected['evaluation_status'] == 'ERROR'
    assert business['execution_status'] == 'timeout'
    assert business['evaluation_status'] == 'ERROR'
    assert timed_out['summary']['PASS'] == 0
    assert timed_out['summary']['ERROR'] == len(timed_out['cases'])

    def no_output(*args, **kwargs):
        return subprocess.CompletedProcess(args, 2, stdout='', stderr='')

    monkeypatch.setattr(runner.subprocess, 'run', no_output)
    empty = runner.run_orders()
    missing = next(case for case in empty['cases'] if case['id'] == 'reject_staging')
    assert missing['execution_status'] == 'invalid_output'
    assert missing['evaluation_status'] == 'ERROR'
    assert empty['summary']['PASS'] == 0
    assert empty['summary']['ERROR'] == len(empty['cases'])


def test_orders_scope_question_returns_the_authorization_text(monkeypatch, tmp_path):
    from app.evaluation import runner

    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setenv('SALES_DUCKDB_PATH', str(tmp_path / 'unused.duckdb'))
    report = runner.run_orders('query_scope')
    assert report['cases'][0]['status'] == 'passed'
