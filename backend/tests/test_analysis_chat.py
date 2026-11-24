import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app import main
from app.analysis import orchestrator, planner
from app.analysis.models import AnalysisInterpretation, AnalysisPlanDraft
from app.analysis.report import ReportValidation


GOLDEN = main.ROOT / 'backend' / 'app' / 'evaluation' / 'analysis_golden.json'
QUESTION = '请问销售额是否有异常？'


class AnalysisClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 24, 12, tzinfo=tz)


def seed_synthetic_sales(path: Path):
    dataset = json.loads(GOLDEN.read_text(encoding='utf-8'))
    with sqlite3.connect(path) as connection:
        connection.execute('''CREATE TABLE sales_summary (
            stat_date TEXT, city TEXT, store_id TEXT, product TEXT, sales_amount REAL, order_count INTEGER
        )''')
        connection.executemany('INSERT INTO sales_summary VALUES (?, ?, ?, ?, ?, ?)', dataset['rows'])


async def mock_planner_response(agent, prompt, **kwargs):
    payload = json.loads(prompt)
    if agent.output_type is AnalysisInterpretation:
        return SimpleNamespace(final_output=AnalysisInterpretation(focus=None))
    time_range = payload['requested_time_range']
    if time_range is None and '今年' in payload['question']:
        time_range = {'start': f"{payload['business_date'][:4]}-01-01", 'end': payload['business_date']}
    metrics = ['sales_amount', 'order_count', 'average_order_value']
    actions = [
        ('profile', 'profile_data_range', '数据范围检查', [], [], []),
        ('daily', 'daily_trend', '每日趋势', metrics[:2], ['stat_date'], ['profile']),
        ('stores', 'dimension_breakdown', '门店分布', metrics, ['store'], ['profile']),
        ('cities', 'dimension_breakdown', '城市分布', metrics, ['city'], ['profile']),
        ('products', 'dimension_breakdown', '产品分布', metrics, ['product'], ['profile']),
        ('drill', 'store_product_drilldown', '异常门店产品下钻', metrics, ['product'], ['stores']),
    ]
    if payload['question'] == '查询今年各城市销售额':
        actions = [action for action in actions if action[0] in {'profile', 'cities'}]
    return SimpleNamespace(final_output=AnalysisPlanDraft.model_validate({
        'analysis_id': payload['analysis_id'], 'goal': '检查销售额的统计异常候选',
        'scope': {'space_id': payload['space_id'], 'time_range': time_range,
                  'filters': payload.get('requested_filters', [])},
        'hypotheses': [], 'stop_conditions': ['达到探索预算'],
        'tasks': [
            {'task_id': task_id, 'task_type': task_type, 'title': title, 'objective': title,
             'metric_ids': metric_ids, 'dimension_ids': dimensions, 'depends_on': parents,
             'rationale': '验证数据可用性或分布差异'}
            for task_id, task_type, title, metric_ids, dimensions, parents in actions
        ],
    }))


@pytest.fixture
def sales_app(monkeypatch, tmp_path):
    path = tmp_path / 'synthetic_sales.db'
    seed_synthetic_sales(path)
    monkeypatch.setattr(main, 'DATABASE', path)
    monkeypatch.setattr(main, 'EXTERNAL_SQLITE_PATH', str(path))
    monkeypatch.setattr(main, 'DATABASE_URL', None)
    monkeypatch.setattr(main, 'DUCKDB_PATH', None)
    monkeypatch.setattr(main, 'CATALOG', main.load_catalog(main.ROOT, 'sales'))
    monkeypatch.setattr(main, 'CONVERSATIONS', tmp_path / 'conversations.db')
    monkeypatch.setattr(main, 'datetime', AnalysisClock)
    monkeypatch.setattr(planner.Runner, 'run', mock_planner_response)


def _read_event(block):
    kind, data = block.strip().split('\n', maxsplit=1)
    return kind.removeprefix('event: '), json.loads(data.removeprefix('data: '))


def _read_events(response):
    assert response.status_code == 200
    return [_read_event(block) for block in response.text.split('\n\n') if block]


def test_auto_analysis_persists_report_tasks_and_every_query(sales_app):
    conversation_id = main.conversations.create(main.CONVERSATIONS, '先前问数')
    previous_query = {'metrics': ['sales_amount'], 'dimensions': ['city']}
    previous_result = {'columns': ['city', 'sales_amount'], 'rows': [['北京', 10]]}
    main.conversations.add_message(main.CONVERSATIONS, conversation_id, 'assistant', '上次结果',
                                   result=previous_result, query=previous_query)

    with TestClient(main.app) as client:
        events = _read_events(client.post('/api/chat/stream', json={
            'message': QUESTION, 'conversation_id': conversation_id,
        }))
    analysis = next(data for kind, data in events if kind == 'analysis')
    assert events[0] == ('conversation', {'conversation_id': conversation_id})
    assert events[-1][1]['outcome']['status'] == 'success'
    assert analysis['status'] == 'completed'
    assert analysis['plan']['scope']['time_range'] == {'start': '2026-01-01', 'end': '2026-11-24'}
    assert analysis['report_validation']['status'] == 'passed'
    assert analysis['query_count'] == len(analysis['evidence']) == 6
    assert all(item['sql'].startswith('SELECT') and item['rows'] for item in analysis['evidence'])
    assert {item['task_id'] for item in analysis['evidence']} == {'profile', 'daily', 'stores', 'cities', 'products', 'drill'}
    assert {item['store_id'] for item in analysis['evidence'] if item['store_id']} == {'SYN-06'}
    assert {item['label'] for item in analysis['findings'] if item['kind'] == 'statistical_anomaly'} >= {'SYN-06', '2026-11-08'}
    assert 'rationale' not in json.dumps(events)
    assert not any(kind == 'result' for kind, _ in events)
    progress = [data for kind, data in events if kind == 'analysis_progress']
    assert [item['kind'] for item in progress[:2]] == ['planning_started', 'plan']
    assert progress[-2:] == [{'kind': 'report', 'status': 'RUNNING'}, {'kind': 'report', 'status': 'COMPLETED'}]
    assert [item['task']['task_id'] for item in progress if item['kind'] == 'task_started'] == [
        'profile', 'daily', 'stores', 'cities', 'products', 'drill',
    ]

    with TestClient(main.app) as reopened:
        detail = reopened.get(f'/api/conversations/{conversation_id}').json()
    assert detail['last_query'] == previous_query
    assert detail['messages'][0]['result'] == previous_result
    assert detail['messages'][0]['analysis'] is None
    assert detail['messages'][-1]['analysis'] == analysis
    assert detail['messages'][-1]['content'] == analysis['report']['text']
    assert detail['messages'][-1]['result'] is None


@pytest.mark.parametrize(('question', 'mode', 'analysis'), [
    ('查询今年各城市的销售额', None, False),
    ('分析今年销售额', 'auto', False),
    ('查询今年各城市的销售额', 'analysis', True),
    (QUESTION, 'query', False),
    ('为什么销售额下滑了？', 'auto', True),
    ('请检查销售额是否出现异常？', 'auto', True),
])
def test_chat_mode_routing(question, mode, analysis, sales_app, monkeypatch):
    class Reply:
        async def stream_events(self):
            yield SimpleNamespace(type='raw_response_event', data=SimpleNamespace(type='response.output_text.delta', delta='普通问数回复'))

    monkeypatch.setattr(main.Runner, 'run_streamed', lambda *args, **kwargs: Reply())
    request = {'message': question}
    if mode:
        request['mode'] = mode
    with TestClient(main.app) as client:
        events = _read_events(client.post('/api/chat/stream', json=request))
    assert any(kind == 'analysis' for kind, _ in events) == analysis
    assert any(data.get('delta') == '普通问数回复' for kind, data in events if kind == 'text') != analysis
    assert events[-1][1]['outcome']['status'] == 'success'


def test_city_sales_analysis_derives_dependency_list_from_tasks(sales_app):
    with TestClient(main.app) as client:
        events = _read_events(client.post('/api/chat/stream', json={
            'mode': 'analysis', 'message': '查询今年各城市销售额',
        }))

    analysis = next(data for kind, data in events if kind == 'analysis')
    assert analysis['status'] == 'completed', analysis['reason']
    assert analysis['plan']['dependencies'] == [{'task_id': 'cities', 'depends_on': 'profile'}]
    assert analysis['report_validation']['status'] == 'passed'
    city_evidence = analysis['evidence'][1]
    assert city_evidence['task_id'] == 'cities'
    sales_column = city_evidence['columns'].index('sales_amount')
    assert {row[0]: row[sales_column] for row in city_evidence['rows']} == {
        '广州': 5160, '上海': 3264, '北京': 3008,
    }
    assert events[-1][1]['outcome']['status'] == 'success'


def test_filtered_analysis_keeps_source_coverage_and_filters_business_queries(sales_app):
    with TestClient(main.app) as client:
        events = _read_events(client.post('/api/chat/stream', json={
            'mode': 'analysis', 'message': '2026-11-08 当天北京的茶产品销售额是否有异常？',
        }))
    result = next(data for kind, data in events if kind == 'analysis')
    assert result['status'] == 'completed'
    profile, *business_evidence = result['evidence']
    assert profile['task_type'] == 'profile_data_range'
    assert profile['time_range'] is None
    assert profile['filters'] == profile['parameters'] == []
    assert profile['rows'] == [['2026-10-23', '2026-11-08', 192]]

    assert len(business_evidence) >= 2
    for evidence in business_evidence:
        assert evidence['time_range'] == {'start': '2026-11-08', 'end': '2026-11-08'}
        assert {(item['dimension_id'], item['value']) for item in evidence['filters']} == {
            ('city', '北京'), ('product', '茶'),
        }
        assert evidence['parameters'][:2] == ['2026-11-08', '2026-11-08']
        assert {'北京', '茶'} <= set(evidence['parameters'][2:])

    daily = next(item for item in business_evidence if item['task_type'] == 'daily_trend')
    assert daily['rows'] == [['2026-11-08', 112.0, 12]]


def test_order_space_refuses_new_analysis_and_clears_pending_clarification(sales_app, monkeypatch):
    monkeypatch.setattr(main, 'CATALOG', main.load_catalog(main.ROOT, 'orders'))
    assert not main._analysis_requested(main.ChatRequest(message=QUESTION, mode='auto'))
    conversation_id = main.conversations.create(main.CONVERSATIONS, '订单问题')
    pending = {'question': '查询客户姓名以及订单金额', 'kind': 'customer_name'}
    main.conversations.set_pending(main.CONVERSATIONS, conversation_id, pending)

    with TestClient(main.app) as client:
        events = _read_events(client.post('/api/chat/stream', json={
            'message': QUESTION, 'mode': 'analysis', 'conversation_id': conversation_id,
        }))
        detail = client.get(f'/api/conversations/{conversation_id}').json()
    assert events[-1][1]['outcome']['status'] == 'refused'
    assert '暂不支持探索分析' in detail['messages'][-1]['content']
    assert detail['pending_clarification'] is None
    assert all(kind not in {'analysis', 'analysis_progress', 'result'} for kind, _ in events)


def test_rejected_analysis_preserves_history_and_other_session(sales_app, monkeypatch):
    monkeypatch.setattr(main, 'CATALOG', main.load_catalog(main.ROOT, 'orders'))
    first = main.conversations.create(main.CONVERSATIONS, '订单')
    other = main.conversations.create(main.CONVERSATIONS, '其他订单')
    pending = {'question': '订单金额最高的客户', 'kind': 'ranking'}
    for conversation in (first, other):
        main.conversations.add_message(main.CONVERSATIONS, conversation, 'user', pending['question'])
        main.conversations.set_pending(main.CONVERSATIONS, conversation, pending)
    with TestClient(main.app) as client:
        client.post('/api/chat/stream', json={'message': QUESTION, 'mode': 'analysis', 'conversation_id': first})
        detail = client.get(f'/api/conversations/{first}').json()
        assert detail['pending_clarification'] is None
        assert detail['messages'][0]['content'] == pending['question']
        assert client.get(f'/api/conversations/{other}').json()['pending_clarification'] == pending


def test_possible_clarification_reply_keeps_pending_after_analysis_refusal(sales_app, monkeypatch):
    monkeypatch.setattr(main, 'CATALOG', main.load_catalog(main.ROOT, 'orders'))
    conversation = main.conversations.create(main.CONVERSATIONS, '订单')
    pending = {'question': '订单金额最高的客户', 'kind': 'ranking'}
    main.conversations.set_pending(main.CONVERSATIONS, conversation, pending)
    with TestClient(main.app) as client:
        client.post('/api/chat/stream', json={'message': '按金额', 'mode': 'analysis', 'conversation_id': conversation})
        assert client.get(f'/api/conversations/{conversation}').json()['pending_clarification'] == pending


def test_analysis_progress_is_streamed_while_sql_is_running(sales_app, monkeypatch):
    entered = Event()
    release = Event()
    execute_query = orchestrator.execute_task_query

    def gate_profile(query, **kwargs):
        if kwargs['task'].task_id == 'profile':
            entered.set()
            assert release.wait(timeout=5)
        return execute_query(query, **kwargs)

    monkeypatch.setattr(orchestrator, 'execute_task_query', gate_profile)

    async def stream():
        response = await main.chat(main.ChatRequest(message=QUESTION))
        chunks = response.body_iterator
        events = []
        try:
            while not any(data.get('kind') == 'task_started' for _, data in events):
                events.append(_read_event(await asyncio.wait_for(anext(chunks), 5)))
            assert events[-1][1]['task']['task_id'] == 'profile'
            assert events[-1][1]['task']['status'] == 'RUNNING'
            assert await asyncio.to_thread(entered.wait, 5)
            next_chunk = asyncio.create_task(anext(chunks))
            done, _ = await asyncio.wait({next_chunk}, timeout=0.02)
            assert not done
            release.set()
            events.append(_read_event(await asyncio.wait_for(next_chunk, 5)))
            assert events[-1][1]['kind'] == 'task_completed'
            events.extend([_read_event(chunk) async for chunk in chunks])
        finally:
            release.set()
            await chunks.aclose()
        return events

    events = asyncio.run(stream())
    assert any(data.get('kind') == 'drilldown' and data['task']['store_id'] == 'SYN-06' for _, data in events)
    assert any(kind == 'analysis' and len(data['evidence']) == 6 for kind, data in events)


def test_failed_report_validation_hides_unverified_findings_and_keeps_evidence(sales_app, monkeypatch):
    monkeypatch.setattr(orchestrator, 'validate_report', lambda *args, **kwargs: ReportValidation(
        status='failed', errors=['验证失败'],
    ))
    with TestClient(main.app) as client:
        events = _read_events(client.post('/api/chat/stream', json={'message': QUESTION}))
        conversation_id = events[0][1]['conversation_id']
        restored = client.get(f'/api/conversations/{conversation_id}').json()['messages'][-1]
    analysis = next(data for kind, data in events if kind == 'analysis')
    assert analysis['findings'] == []
    assert analysis['report'] is None
    assert analysis['report_validation']['status'] == 'failed'
    assert len(analysis['evidence']) == 6
    assert events[-1][1]['status'] == 'failed'
    assert restored['analysis'] == analysis
    assert '校验' in restored['content'] or '验证' in restored['content']


def test_old_database_migrates_when_opened_concurrently(tmp_path):
    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as connection:
        connection.executescript('''
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, last_query TEXT
            );
            CREATE TABLE messages (
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL,
                content TEXT NOT NULL, result TEXT, created_at TEXT NOT NULL
            );
            INSERT INTO conversations VALUES ('existing', '历史', '2026-01-01', '{"metrics":["sales_amount"]}');
            INSERT INTO messages VALUES ('msg', 'existing', 'assistant', '现有结果', '{"rows":[[12]]}', '2026-01-01');
        ''')

    ready = Barrier(4)

    def reopen():
        ready.wait(timeout=5)
        return main.conversations.get(path, 'existing')

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(reopen) for _ in range(4)]
        for future in futures:
            restored = future.result(timeout=10)
            assert restored['last_query'] == {'metrics': ['sales_amount']}
            assert restored['messages'][0]['result'] == {'rows': [[12]]}
            assert restored['messages'][0]['analysis'] is None
