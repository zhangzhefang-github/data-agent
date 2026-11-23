"""Pre-query decisions for the orders space and replies to pending questions."""
import re


def decide(question: str, resolved: set[str] | None = None):
    resolved = resolved or set()
    q = question.lower()
    if re.search(r'\bnps\b|净推荐值|净推荐分', q):
        return 'refused', '订单主题空间未发布 NPS，无法查询。', None
    if re.search(r'\b(?:stg|raw|internal)_[a-z0-9_]+\b|内部表|原始表', q):
        return 'refused', '内部表未授权，无法查询。', None
    if ('支付' in q and '订单' in q and re.search(r'直接.{0,8}(?:连接|关联|join)|(?:连接|关联|join).{0,8}直接', q)
            and re.search(r'订单金额', q)):
        return 'refused', '不能直接连接支付明细后汇总订单金额；支付明细需先按订单预汇总。', None
    if 'name' not in resolved and re.search(r'客户.{0,4}姓名|姓名|客户名字', q):
        return 'clarification_required', '客户姓名未授权。请确认改用客户标识，或仅查询订单金额。', 'name'
    if 'count' not in resolved and re.search(r'客户数|多少(?:个|位)?客户|客户(?:有|共|一共|总共)?多少', q):
        return 'clarification_required', '客户数尚未发布。是否改为按客户标识分组查询订单？', 'count'
    if 'success' not in resolved and '成功' in q and '订单' in q:
        return 'clarification_required', '“成功”未定义，请选择订单状态：已下单、已发货、已完成、退货中或已退货。', 'success'
    if 'amount' not in resolved and '订单金额' in q and '支付金额' in q and re.search(r'相加|加在一起|加起来|之和|合计|总和', q):
        return 'clarification_required', '订单金额和支付金额不能相加，请选择订单金额或支付金额。', 'amount'
    metrics = [bool(re.search(r'订单金额|按金额', q)), bool(re.search(r'订单笔数|订单数|按笔数', q))]
    if 'ranking' not in resolved and sum(metrics) != 1 and re.search(r'最高.{0,5}客户|客户.{0,8}(?:最高|排名第一)', q):
        return 'clarification_required', '请确认客户排名按订单金额还是订单笔数？', 'ranking'
    return None


def resume(pending: dict, reply: str):
    q = reply.strip()
    kind = pending['kind']
    if len(q) > 25 or re.search(r'另(?:外|一个|一)|新问题|重新问', q):
        return None
    choice = None
    if kind == 'model':
        options = pending.get('options', [])
        normalized = q.rstrip('。！!')
        if normalized in options:
            choice = normalized
    elif kind == 'ranking':
        if re.fullmatch(r'(?:按|按照|根据)?(?:订单)?金额(?:排名|排序)?[。！!]?', q): choice = '按订单金额排名'
        elif re.fullmatch(r'(?:按|按照|根据)?(?:订单)?(?:笔数|订单数|数量)(?:排名|排序)?[。！!]?', q): choice = '按订单笔数排名'
    elif kind == 'name':
        if re.fullmatch(r'(?:只|仅)(?:查|看)?(?:订单)?金额[。！!]?', q): choice = '不查客户姓名，只查订单金额'
        elif re.fullmatch(r'(?:改用|按|只查|查看)?(?:客户标识|客户id|客户编号)[。！!]?', q, re.I): choice = '不查客户姓名，改用客户标识'
    elif kind == 'count':
        if re.fullmatch(r'(?:改用|按|只查|查看)?(?:客户标识|客户id|客户编号)[。！!]?', q, re.I): choice = '改为按客户标识分组查询订单'
    elif kind == 'amount':
        if re.fullmatch(r'(?:按|只查|查看)?订单金额[。！!]?', q): choice = '只查订单金额，不查支付金额，不相加'
        elif re.fullmatch(r'(?:按|只查|查看)?支付金额[。！!]?', q): choice = '只查支付金额，不查订单金额，不相加'
    elif kind == 'success':
        for status in ('已下单', '已发货', '已完成', '退货中', '已退货'):
            if re.fullmatch(r'(?:按|选择|只看)?' + status + r'[。！!]?', q):
                choice = f'订单状态为{status}'
                break
    if choice is None:
        return None
    return pending['question'] + '\n用户确认：' + choice, set(pending.get('resolved', [])) | {kind}
