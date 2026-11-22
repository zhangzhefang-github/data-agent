"""把一次订单问数的观察判成执行状态和评测结果。"""

EXECUTION_STATUSES = (
    'success',
    'refused',
    'clarification_required',
    'model_error',
    'timeout',
    'database_error',
    'validator_error',
    'invalid_output',
)
EVALUATION_STATUSES = ('PASS', 'FAIL', 'ERROR', 'SKIPPED')

_ERROR_EXECUTIONS = {'model_error', 'timeout', 'database_error', 'validator_error', 'invalid_output'}
_GENERIC_MODEL_ERROR = '查询失败，请检查模型连接和服务配置'
_VALIDATOR_MESSAGES = {
    'SQL 修复后仍无法执行',
    '查询未返回已验证的结果',
    'SQL 无法绑定字段或转换数据类型',
    'DuckDB SQL 语法错误',
    '已授权上下文补充次数已用尽',
    '查询已结束',
}
_CLARIFICATION_MARKERS = ('请先提供', '需要澄清', '请补充')
_DATABASE_MARKERS = ('数据库', 'DuckDB', 'duckdb', 'sqlite', 'Catalog Error', 'IO Error')


def judge(observation, expected):
    """给定一次观察和用例预期，返回执行状态和评测结果。

    缺模型密钥时没有执行状态，评测结果为跳过。执行失败的评测结果是错误，
    不能当成正确拒绝，也不计入通过。
    """
    if observation.get('skipped') or observation.get('missing_api_key'):
        return _verdict(None, 'SKIPPED', failure_stage='configuration')
    execution_status = _execution_status(observation, expected)
    if execution_status in _ERROR_EXECUTIONS:
        return _verdict(execution_status, 'ERROR')
    if execution_status == 'refused':
        if expected.get('expect') == 'rejected':
            return _verdict('refused', 'PASS')
        return _verdict('refused', 'FAIL', failure_stage='rejection')
    if execution_status == 'clarification_required':
        if expected.get('expect') == 'clarification':
            return _verdict('clarification_required', 'PASS')
        return _verdict('clarification_required', 'FAIL', failure_stage='clarification')
    return _score_success(observation, expected)


def _verdict(execution_status, evaluation_status, leaked=0, failure_stage=None):
    return {
        'execution_status': execution_status,
        'evaluation_status': evaluation_status,
        'leaked': leaked,
        'failure_stage': failure_stage,
    }


def _execution_status(observation, expected):
    code, message = _error_parts(observation.get('error'))
    text = (observation.get('text') or '').strip()
    if observation.get('timeout') or code == 'query_timeout' or (message and '超时' in message):
        return 'timeout'
    if observation.get('no_model_result'):
        return 'invalid_output'
    if code == 'database_error' or _contains_any(message, _DATABASE_MARKERS):
        return 'database_error'
    if code == 'validator_error' or message in _VALIDATOR_MESSAGES:
        return 'validator_error'
    if code == 'model_error':
        return 'model_error'
    result = observation.get('result')
    if isinstance(result, dict) and (result.get('rows') or not observation.get('error')):
        return 'success'
    if _contains_any(text, _CLARIFICATION_MARKERS):
        return 'clarification_required'
    if expected.get('expect') == 'scope' and not observation.get('error'):
        return 'success'
    if _refusal_explanation(text, message):
        return 'refused'
    if observation.get('error') or message:
        return 'model_error'
    return 'invalid_output'


def _error_parts(error):
    if isinstance(error, dict):
        message = error.get('message')
        message = message.strip() if isinstance(message, str) else ''
        return error.get('code'), message or None
    if isinstance(error, str):
        message = error.strip()
        return None, message or None
    return None, None


def _contains_any(text, markers):
    return bool(text) and any(marker in text for marker in markers)


def _refusal_explanation(text, message):
    for candidate in (text, message):
        if not candidate or candidate == _GENERIC_MODEL_ERROR or candidate in _VALIDATOR_MESSAGES:
            continue
        if '超时' in candidate:
            continue
        return True
    return False


def _score_success(observation, expected):
    expect = expected.get('expect')
    result = observation.get('result')
    if expect == 'rejected':
        rows = [] if not isinstance(result, dict) else (result.get('rows') or [])
        return _verdict('success', 'FAIL', leaked=1 if rows else 0, failure_stage='rejection')
    if expect == 'clarification':
        return _verdict('success', 'FAIL', failure_stage='clarification')
    if expect == 'scope':
        actual = (observation.get('text') or '').strip()
        wanted = (expected.get('scope_text') or '').strip()
        if actual != wanted:
            return _verdict('success', 'FAIL', failure_stage='result')
        return _verdict('success', 'PASS')
    if not isinstance(result, dict):
        return _verdict('invalid_output', 'ERROR')
    if expect == 'followup':
        sql = result.get('sql') or ''
        rows_match = _rows_equal(result.get('rows'), expected.get('rows'))
        dates_match = all(token in sql for token in expected.get('sql_contains') or [])
        if not dates_match or not rows_match:
            return _verdict('success', 'FAIL', failure_stage='prior_period')
        return _verdict('success', 'PASS')
    if expect == 'aggregates':
        projected = _project_rows(result, expected['columns'])
        if projected is None or len(projected) != expected['row_count']:
            return _verdict('success', 'FAIL', failure_stage='result')
        index = {name: position for position, name in enumerate(expected['columns'])}
        for name, expected_sum in expected['sums'].items():
            try:
                total = sum(row[index[name]] for row in projected)
            except TypeError:
                return _verdict('success', 'FAIL', failure_stage='result')
            if not _same_value(total, expected_sum):
                return _verdict('success', 'FAIL', failure_stage='result')
        return _verdict('success', 'PASS')
    projected = _project_rows(result, expected['columns'])
    if projected is None or not _rows_equal(projected, expected.get('rows')):
        return _verdict('success', 'FAIL', failure_stage='result')
    return _verdict('success', 'PASS')


def _same_value(actual, expected):
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual == expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return float(actual) == float(expected)
    return actual == expected


def _canonical_row(row):
    return tuple(_canonical_value(value) for value in row)


def _canonical_value(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    number = float(value)
    return int(number) if number.is_integer() else number


def _rows_equal(actual, expected):
    if actual is None or expected is None or len(actual) != len(expected):
        return False
    return sorted(map(_canonical_row, actual)) == sorted(map(_canonical_row, expected))


def _project_rows(result, columns):
    actual_columns = result.get('columns') or []
    if set(actual_columns) != set(columns):
        return None
    index = {name: position for position, name in enumerate(actual_columns)}
    try:
        return [[row[index[name]] for name in columns] for row in result.get('rows') or []]
    except (IndexError, KeyError, TypeError):
        return None
