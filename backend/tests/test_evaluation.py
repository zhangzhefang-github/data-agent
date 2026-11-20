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
