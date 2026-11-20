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

from app.query import Query, execute_query

DATASET = Path(__file__).with_name('golden.json')
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
        elif event == 'error':
            result['error'] = True
    return result


def main():
    parser = argparse.ArgumentParser(description='Run the Sales Space Golden Dataset.')
    parser.add_argument('--live', action='store_true', help='调用真实模型评测自然语言解析')
    parser.add_argument('--case', help=argparse.SUPPRESS)
    args = parser.parse_args()
    report = run_live(args.case) if args.live else run_deterministic()
    counts = {status: sum(case['status'] == status for case in report['cases']) for status in ('passed', 'failed', 'skipped')}
    report['summary'] = counts
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if counts['failed'] else 0


if __name__ == '__main__':
    sys.exit(main())
