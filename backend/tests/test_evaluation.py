from app.evaluation.runner import run_deterministic


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
