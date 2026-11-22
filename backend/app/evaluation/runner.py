"""Run reproducible semantic-query and optional live-agent evaluations."""
from contextlib import closing
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from app.evaluation.judgement import judge
from app.query import Query, execute_query

DATASET = Path(__file__).with_name('golden.json')
ORDERS_DATASET = Path(__file__).with_name('orders_golden.json')
FIXTURE = [
    ('2025-01-01', '北京', 'BJ01', '商品A', 80, 1),
    ('2025-12-31', '北京', 'BJ01', '商品A', 100, 1),
    ('2026-01-01', '北京', 'BJ02', '商品A', 100, 1),
    ('2026-06-01', '北京', 'BJ01', '商品A', 300, 3),
    ('2026-02-01', '上海', 'SH01', '商品B', 200, 4),
    ('2026-03-01', '广州', 'GZ01', '商品A', 0, 0),
    ('2026-06-02', '北京', 'BJ01', '商品A', 1000, 1),
]


def load_dataset():
    return json.loads(DATASET.read_text(encoding='utf-8'))


def load_orders_dataset():
    return json.loads(ORDERS_DATASET.read_text(encoding='utf-8'))


def create_fixture(path):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE sales_summary (stat_date TEXT, city TEXT, store_id TEXT, product TEXT, sales_amount INTEGER, order_count INTEGER)')
        db.executemany('INSERT INTO sales_summary VALUES (?, ?, ?, ?, ?, ?)', FIXTURE)


def compare_plan(actual, expected, clock):
    if actual is None:
        return 'semantic_resolution'
    try:
        actual_plan = Query.model_validate(actual)
        expected_plan = Query.model_validate(expected)
    except Exception:
        return 'query_ir'
    if (set(actual_plan.metrics) != set(expected_plan.metrics)
            or set(actual_plan.dimensions) != set(expected_plan.dimensions)):
        return 'semantic_resolution'
    # An explicit default date range is equivalent to an omitted one.
    default_range = {'start': clock.replace(month=1, day=1).isoformat(), 'end': clock.isoformat()}
    actual_data = actual_plan.model_dump(mode='json')
    expected_data = expected_plan.model_dump(mode='json')
    if expected_data['order_by'] is None:
        actual_data['order_by'] = None
    for item in (actual_data, expected_data):
        if item['time_range'] == default_range:
            item['time_range'] = None
        item['filters'] = sorted(item['filters'], key=lambda value: (value['field'], value['operator'], value['value']))
    if actual_data != expected_data:
        return 'query_ir'
    return None


def evaluate_case(case, database, clock, plan=None):
    report = {'id': case['id'], 'question': case['question'], 'standard_logic': case['standard_logic'], 'expected_plan': case['expected_plan'],
              'actual_plan': plan if plan is not None else case['expected_plan'], 'expected_rows': case['expected_rows'],
              'actual_rows': None, 'sql': None, 'execution_status': 'not_run', 'status': 'failed', 'failure_stage': None}
    stage = compare_plan(report['actual_plan'], case['expected_plan'], clock)
    if stage:
        report['failure_stage'] = stage
        return report
    try:
        result = execute_query(database, Query.model_validate(report['actual_plan']), clock)
    except (ValueError, KeyError) as exc:
        report.update(failure_stage='sql_generation', error=type(exc).__name__)
        return report
    except sqlite3.Error as exc:
        report.update(failure_stage='database_execution', execution_status='failed', error=type(exc).__name__)
        return report
    report.update(sql=result['sql'], actual_rows=result['rows'], execution_status='passed')
    if not rows_match(case, result['rows']):
        report['failure_stage'] = 'database_execution'
        return report
    report['status'] = 'passed'
    return report


def rows_match(case, rows):
    expected = case['expected_rows']
    if case['expected_plan'].get('order_by') is None:
        key = lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True)
        return sorted(rows, key=key) == sorted(expected, key=key)
    return rows == expected


def run_deterministic():
    dataset = load_dataset()
    clock = date.fromisoformat(dataset['clock'])
    with tempfile.TemporaryDirectory(prefix='sales-evaluation-') as directory:
        database = Path(directory) / 'sales.db'
        create_fixture(database)
        cases = [evaluate_case(case, database, clock) for case in dataset['cases']]
    return {'mode': 'deterministic', 'clock': dataset['clock'], 'cases': cases}


def run_live(case_id=None):
    dataset = load_dataset()
    if case_id is not None:
        dataset['cases'] = [case for case in dataset['cases'] if case['id'] == case_id]
        if not dataset['cases']:
            raise ValueError('未知评测用例')
    if not os.getenv('OPENAI_API_KEY'):
        return {'mode': 'live', 'clock': dataset['clock'], 'cases': [
            {'id': case['id'], 'status': 'skipped', 'failure_stage': 'configuration', 'reason': 'OPENAI_API_KEY 未设置'}
            for case in dataset['cases']
        ]}
    if case_id is None:
        reports = []
        for case in dataset['cases']:
            try:
                process = subprocess.run(
                    [sys.executable, '-m', 'app.evaluation.runner', '--live', '--case', case['id']],
                    capture_output=True, text=True, timeout=60,
                )
                if process.returncode not in (0, 1):
                    raise ValueError('评测进程异常退出')
                report = json.loads(process.stdout)['cases'][0]
            except subprocess.TimeoutExpired:
                report = {'id': case['id'], 'status': 'failed', 'failure_stage': 'semantic_resolution', 'reason': '模型请求超过60秒'}
            except (ValueError, KeyError, IndexError):
                report = {'id': case['id'], 'status': 'failed', 'failure_stage': 'semantic_resolution', 'reason': '评测进程未返回有效结果'}
            reports.append(report)
            print(json.dumps(report, ensure_ascii=False), file=sys.stderr, flush=True)
        return {'mode': 'live', 'clock': dataset['clock'], 'cases': reports}

    from fastapi.testclient import TestClient
    from app import main

    clock = date.fromisoformat(dataset['clock'])

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(clock.year, clock.month, clock.day, 12, tzinfo=tz)

    with tempfile.TemporaryDirectory(prefix='sales-live-evaluation-') as directory:
        database = Path(directory) / 'sales.db'
        create_fixture(database)
        conversations = Path(directory) / 'conversations.db'
        reports = []
        with patch.object(main, 'DATABASE', database), patch.object(main, 'DATABASE_URL', None), patch.object(main, 'EXTERNAL_SQLITE_PATH', None), patch.object(main, 'CONVERSATIONS', conversations), patch.object(main, 'datetime', FixedDatetime):
            with TestClient(main.app) as client:
                for case in dataset['cases']:
                    if 'previous_question' in case:
                        initial = send(client, case['previous_question'])
                        conversation_id = initial.get('conversation_id')
                        if not conversation_id or not initial.get('result'):
                            reports.append({'id': case['id'], 'status': 'failed', 'failure_stage': 'semantic_resolution', 'reason': '前一轮查询失败'})
                            continue
                        response = send(client, case['question'], conversation_id)
                    else:
                        response = send(client, case['question'])
                    conversation_id = response.get('conversation_id')
                    if response.get('error') or not conversation_id:
                        reports.append({'id': case['id'], 'status': 'failed', 'failure_stage': 'semantic_resolution', 'reason': '聊天接口未返回查询结果'})
                        continue
                    detail = client.get(f'/api/conversations/{conversation_id}').json()
                    actual_plan = detail.get('last_query')
                    if actual_plan is None or not response.get('result'):
                        reports.append({'id': case['id'], 'status': 'failed', 'failure_stage': 'semantic_resolution', 'reason': '模型未执行查询'})
                        continue
                    report = evaluate_case(case, database, clock, actual_plan)
                    # Compare the exact result returned by the live tool, not just its replay.
                    report['actual_rows'] = response['result']['rows']
                    report['sql'] = response['result']['sql']
                    if report['status'] == 'passed' and not rows_match(case, report['actual_rows']):
                        report.update(status='failed', failure_stage='database_execution')
                    reports.append(report)
    return {'mode': 'live', 'clock': dataset['clock'], 'cases': reports}


_LEGACY_STATUS = {'PASS': 'passed', 'FAIL': 'failed', 'ERROR': 'error', 'SKIPPED': 'skipped'}


def orders_summary(cases):
    return {name: sum(case.get('evaluation_status') == name for case in cases) for name in ('PASS', 'FAIL', 'ERROR', 'SKIPPED')}


def score_orders_case(case, response, scope_text=None):
    """按执行状态和评测结果给订单用例打分。不要求 SQL 原文一致。"""
    expected = case if scope_text is None else {**case, 'scope_text': scope_text}
    report = _order_report(case, _observation_from_response(response), response=response, expected=expected)
    if case['expect'] == 'scope' and report['evaluation_status'] == 'FAIL':
        report['actual_text'] = response.get('text')
    return report


def _observation_from_response(response):
    return {
        'result': response.get('result'),
        'text': response.get('text'),
        'error': response.get('error'),
        'done': response.get('done'),
    }


def _order_report(case, observation, reason=None, response=None, expected=None):
    verdict = judge(observation, case if expected is None else expected)
    result = observation.get('result') if response is None else response.get('result')
    report = {
        'id': case['id'],
        'question': case['question'],
        'standard_logic': case.get('standard_logic'),
        'sql': None if result is None else result.get('sql'),
        'actual_columns': None if result is None else result.get('columns'),
        'actual_rows': None if result is None else result.get('rows'),
        'execution_status': verdict['execution_status'],
        'evaluation_status': verdict['evaluation_status'],
        'status': _LEGACY_STATUS[verdict['evaluation_status']],
        'failure_stage': verdict.get('failure_stage'),
        'leaked': verdict.get('leaked', 0),
    }
    if reason:
        report['reason'] = reason
    return report


def _orders_result(cases):
    return {'mode': 'orders', 'cases': cases, 'summary': orders_summary(cases)}


def _skipped_order_case(case, reason):
    verdict = judge({'skipped': True, 'missing_api_key': reason == 'OPENAI_API_KEY 未设置'}, case)
    return {
        'id': case['id'],
        'question': case['question'],
        'status': 'skipped',
        'evaluation_status': verdict['evaluation_status'],
        'execution_status': verdict['execution_status'],
        'failure_stage': 'configuration',
        'reason': reason,
    }


def run_orders(case_id=None):
    """评测订单 Direct SQL。模型题调用真实接口；销售 Golden Dataset 不在这里运行。"""
    dataset = load_orders_dataset()
    cases = dataset['cases']
    if case_id is not None:
        cases = [case for case in cases if case['id'] == case_id]
        if not cases:
            raise ValueError('未知评测用例')
    if case_id is None:
        reports = []
        for case in cases:
            reason = _orders_skip_reason(case)
            if reason:
                reports.append(_skipped_order_case(case, reason))
                print(json.dumps(reports[-1], ensure_ascii=False), file=sys.stderr, flush=True)
                continue
            try:
                process = subprocess.run(
                    [sys.executable, '-m', 'app.evaluation.runner', '--orders', '--case', case['id']],
                    capture_output=True, text=True, timeout=120,
                )
                if process.returncode not in (0, 1):
                    raise ValueError('评测进程异常退出')
                report = json.loads(process.stdout)['cases'][0]
            except subprocess.TimeoutExpired:
                report = _order_report(case, {'timeout': True}, reason='模型请求超过120秒')
            except (ValueError, KeyError, IndexError, json.JSONDecodeError):
                report = _order_report(case, {'no_model_result': True}, reason='评测进程未返回有效结果')
            reports.append(report)
            print(json.dumps(report, ensure_ascii=False), file=sys.stderr, flush=True)
        return _orders_result(reports)

    reason = _orders_skip_reason(cases[0])
    if reason:
        return _orders_result([_skipped_order_case(cases[0], reason)])
    return _orders_result([_execute_orders_case(cases[0])])


def _orders_skip_reason(case):
    if not os.getenv('SALES_DUCKDB_PATH'):
        return 'SALES_DUCKDB_PATH 未设置'
    if case['expect'] != 'scope' and not os.getenv('OPENAI_API_KEY'):
        return 'OPENAI_API_KEY 未设置'
    return None


def _execute_orders_case(case):
    from fastapi.testclient import TestClient

    from app import main
    from app.query import load_catalog
    from app.query_scope import render_query_scope
    from app.semantic.snapshot import build_orders_snapshot

    scope_text = render_query_scope(build_orders_snapshot(main.ROOT).authorization)
    with tempfile.TemporaryDirectory(prefix='orders-live-evaluation-') as directory:
        conversations = Path(directory) / 'conversations.db'
        with patch.object(main, 'CATALOG', load_catalog(main.ROOT, 'orders')), patch.object(main, 'DUCKDB_PATH', os.environ['SALES_DUCKDB_PATH']), patch.object(main, 'DATABASE_URL', None), patch.object(main, 'CONVERSATIONS', conversations):
            with TestClient(main.app) as client:
                if 'previous_question' in case:
                    initial = send(client, case['previous_question'])
                    if not initial.get('conversation_id') or not initial.get('result'):
                        observation = _observation_from_response(initial)
                        if not initial.get('error') and not initial.get('text') and not initial.get('result'):
                            observation['no_model_result'] = True
                        return _order_report(case, observation, reason='前一轮查询失败')
                    response = send(client, case['question'], initial['conversation_id'])
                else:
                    response = send(client, case['question'])
    return score_orders_case(case, response, scope_text)


def send(client, question, conversation_id=None):
    response = client.post('/api/chat/stream', json={'message': question, 'conversation_id': conversation_id})
    result = {}
    if response.status_code != 200:
        return {'error': True}
    for block in response.text.split('\n\n'):
        event = None
        payload = None
        for line in block.splitlines():
            if line.startswith('event: '):
                event = line[7:]
            elif line.startswith('data: '):
                payload = json.loads(line[6:])
        if event == 'conversation':
            result['conversation_id'] = payload['conversation_id']
        elif event == 'result':
            result['result'] = payload
        elif event == 'text':
            result['text'] = result.get('text', '') + payload.get('delta', '')
        elif event == 'done':
            result['done'] = payload.get('status')
        elif event == 'error':
            result['error'] = payload if isinstance(payload, dict) else True
    return result


def main():
    parser = argparse.ArgumentParser(description='Run a golden dataset.')
    parser.add_argument('--live', action='store_true', help='调用真实模型评测销售主题空间的自然语言解析')
    parser.add_argument('--orders', action='store_true', help='评测订单主题空间的 Direct SQL')
    parser.add_argument('--platform', choices=('deterministic', 'model', 'live'), help='用一种模式运行订单平台数据集，不改变 --orders')
    parser.add_argument('--split', choices=('dev', 'validation', 'holdout'), help='只运行订单平台数据集的一个分组')
    parser.add_argument('--case', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.platform and args.orders:
        parser.error('不能同时选择旧订单评测和订单平台模式')
    if args.split and not args.platform:
        parser.error('--split 只用于订单平台模式')
    if args.platform == 'model':
        parser.error('model 模式需要在进程内传入 Runner，命令行请使用 --platform live')
    if args.platform:
        from app.evaluation.platform_run import run_orders_platform
        report = run_orders_platform(args.platform, split=args.split)
    elif args.orders:
        report = run_orders(args.case)
    elif args.live:
        report = run_live(args.case)
    else:
        report = run_deterministic()
    if report.get('mode') == 'orders' or (report.get('cases') and 'evaluation_status' in report['cases'][0]):
        counts = orders_summary(report['cases'])
        exit_code = 1 if counts['FAIL'] or counts['ERROR'] else 0
    else:
        counts = {status: sum(case['status'] == status for case in report['cases']) for status in ('passed', 'failed', 'skipped')}
        exit_code = 1 if counts['failed'] else 0
    report['summary'] = counts
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
