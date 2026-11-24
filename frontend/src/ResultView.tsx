import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { Button, Card, Segmented, Statistic, Table } from 'antd'
import * as echarts from 'echarts'
import { CitySalesAnswer, citySalesAnswer, isSalesDiagnosis } from './CitySalesAnswer'
import { highlightSql } from './sqlHighlight'

export type Result = { columns: string[], rows: (string | number | null)[][], sql: string, source: string, elapsed_ms: number }
export type Rating = 'correct' | 'incorrect'
export type AnalysisTaskStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'SKIPPED'
export type AnalysisTask = { task_id: string, title: string, task_type: string, status: AnalysisTaskStatus, reason?: string | null, store_id?: string | null }
export type AnalysisPlan = { goal: string, scope: { time_range: { start: string, end: string } | null } }
export type AnalysisEvidence = Result & {
  evidence_id: string, task_id: string, task_type: string, row_count: number,
  store_id?: string | null,
  parameters: (string | number)[], possible_truncation: boolean,
  dataset_id: string, snapshot_id: string,
  time_range?: { start: string, end: string } | null,
  filters?: { dimension_id: string, value: string }[],
}
type AnalysisFinding = { evidence_id: string } & (
  | { kind: 'statistical_anomaly', dimension_id: string, metric_id: string, label: string, value: number, median: number, direction: 'high' | 'low' }
  | { kind: 'metric_summary', metric_id: string, dimension_id: string, status: 'sufficient' | 'insufficient_sample' | 'partial_evidence', sample_size: number, total: number | null }
  | { kind: 'calendar_coverage', observed_days: number, first_business_date: string, last_business_date: string, missing_dates: string[] }
  | { kind: 'data_profile', first_business_date: string | null, last_business_date: string | null }
  | { kind: 'monthly_trend' | 'business_pattern_difference' | 'data_quality' | 'partial_evidence' }
)
type AnalysisSection = { key: string, title: string, text: string, evidence_ids: string[] }
export type AnalysisResult = {
  mode: 'analysis', status: 'completed' | 'partial' | 'failed', plan: AnalysisPlan | null, tasks: AnalysisTask[],
  findings: AnalysisFinding[], evidence: AnalysisEvidence[], report: { text: string, sections: AnalysisSection[] } | null,
  report_validation: { status: 'passed' | 'failed' } | null, incomplete: string[], reason: string | null,
}
const analysisLabels: Record<string, string> = { first_business_date: '最早业务日期', last_business_date: '最晚业务日期', summary_row_count: '汇总记录数' }
const labels: Record<string, string> = { city: '城市', store: '门店', product: '产品', stat_date: '业务日期', sales_amount: '销售额（元）', order_count: '订单量', average_order_value: '客单价（元）', order_date: '订单日期', status: '订单状态', customer_id: '客户标识', order_amount: '订单金额', order_rows: '订单笔数', credit_card_amount: '信用卡金额', coupon_amount: '优惠券金额', bank_transfer_amount: '银行转账金额', gift_card_amount: '礼品卡金额', payment_method: '支付方式', payment_rows: '支付笔数', payment_amount: '支付金额' }
const metrics = ['sales_amount', 'order_count', 'average_order_value', 'order_amount', 'order_rows', 'credit_card_amount', 'coupon_amount', 'bank_transfer_amount', 'gift_card_amount', 'payment_rows', 'payment_amount']

type ChartKind = '柱状图' | '折线图' | '饼图'

function Chart({ data, initialKind = '柱状图' }: { data: Result, initialKind?: ChartKind }) {
  const [kind, setKind] = useState<ChartKind>(initialKind)
  const container = useRef<HTMLDivElement>(null)
  const metricIndex = data.columns.includes('payment_amount')
    ? data.columns.indexOf('payment_amount')
    : data.columns.findIndex(name => metrics.includes(name))
  const dimensions = data.columns.map((name, index) => ({ name, index })).filter(column => !metrics.includes(column.name))
  const points = data.rows.flatMap(row => {
    const value = row[metricIndex]
    if (typeof value !== 'number' || !Number.isFinite(value)) return []
    return [{ name: dimensions.map(dimension => `${labels[dimension.name] ?? dimension.name}：${row[dimension.index] ?? '空'}`).join(' / '), value }]
  })
  const canRender = dimensions.length > 0 && points.length > 0 && (kind !== '饼图' || points.every(point => point.value >= 0) && points.some(point => point.value > 0))

  useEffect(() => {
    if (!container.current || !canRender) return
    const chart = echarts.init(container.current)
    chart.setOption(kind === '饼图' ? {
      tooltip: { trigger: 'item' },
      series: [{ type: 'pie', radius: '65%', data: points }],
    } : {
      tooltip: { trigger: 'axis' },
      grid: { left: 65, right: 25, bottom: 80, top: 25 },
      xAxis: { type: 'category', data: points.map(point => point.name), axisLabel: { rotate: 25 } },
      yAxis: { type: 'value' },
      series: [{ type: kind === '柱状图' ? 'bar' : 'line', data: points.map(point => point.value) }],
    })
    const resize = () => chart.resize()
    window.addEventListener('resize', resize)
    return () => { window.removeEventListener('resize', resize); chart.dispose() }
  }, [data, kind, canRender])

  if (dimensions.length === 0 || metricIndex < 0 || data.rows.length === 0) return null
  return <section aria-label="数据图表"><Segmented<ChartKind> value={kind} options={['柱状图', '折线图', '饼图']} onChange={setKind} />
    {canRender ? <div ref={container} role="img" aria-label={`${kind}，${labels[data.columns[metricIndex]] ?? data.columns[metricIndex]}`} style={{ height: 320 }} /> : <p>当前结果无法用{kind}展示</p>}
  </section>
}

function SqlCode({ sql }: { sql: string }) {
  return <pre className="sql">{highlightSql(sql).map((token, index) =>
    <span className={token.kind === 'plain' ? undefined : `sql-${token.kind}`} key={index}>{token.text}</span>
  )}</pre>
}

export function ResultView({ data }: { data: Result }) {
  return <Card title="查询结果"><QueryResult data={data} /></Card>
}

function QueryResult({ data, evidence }: { data: Result, evidence?: AnalysisEvidence }) {
  return <>
    <p>{data.source} · {data.elapsed_ms} ms</p>
    {data.rows.length === 0 ? <p>该范围内无数据</p> : <>
      {data.rows.length === 1 && data.columns.map((column, index) => metrics.includes(column) ? <Statistic key={column} title={labels[column] ?? column} value={data.rows[0][index] ?? '—'} /> : null)}
      <Chart data={data} initialKind={evidence?.task_type === 'daily_trend' ? '折线图' : undefined} />
      <Table pagination={evidence ? { pageSize: 20, hideOnSinglePage: true, showSizeChanger: false } : false} rowKey="key" dataSource={data.rows.map((row, index) => ({ key: index, ...Object.fromEntries(row.map((value, i) => [data.columns[i], value])) }))} columns={data.columns.map(name => ({ title: labels[name] ?? analysisLabels[name] ?? name, dataIndex: name, key: name, render: (value: unknown) => value === null ? '—' : String(value) }))} />
    </>}
    <details><summary>查看 SQL</summary><SqlCode sql={data.sql} />
      {!!evidence?.parameters.length && <p>参数（按绑定顺序） {JSON.stringify(evidence.parameters)}</p>}
    </details>
  </>
}

function EvidencePanel({ evidence, title, id }: { evidence: AnalysisEvidence, title: string, id: string }) {
  const [open, setOpen] = useState(false)
  return <details id={id} className="evidence-panel" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>{title} · {evidence.row_count} 行</summary>
    {open && <div className="evidence-body">
      <p className="evidence-metadata">{evidence.dataset_id} · 语义上下文快照 {evidence.snapshot_id}</p>
      {evidence.possible_truncation && <p>已达到本次查询的结果行数上限。</p>}
      <QueryResult data={evidence} evidence={evidence} />
    </div>}
  </details>
}

export function AnalysisResultView({ data, question, process }: { data: AnalysisResult, question: string, process?: ReactNode }) {
  const prefix = useId()
  const evidenceList = useRef<HTMLDetailsElement>(null)
  const evidenceIndexes = new Map(data.evidence.map((item, index) => [item.evidence_id, index]))
  const taskTitles = new Map(data.tasks.map(task => [task.task_id, task.title]))
  const primary = citySalesAnswer(question, data)
  const report = data.report_validation?.status === 'passed' ? data.report : null
  const anomalies = report ? data.findings.filter(item => item.kind === 'statistical_anomaly') : []
  function evidenceLink(evidenceId: string) {
    const index = evidenceIndexes.get(evidenceId)
    if (index === undefined) return null
    const id = `${prefix}-evidence-${index}`
    return <a key={evidenceId} className="analysis-citation" href={`#${id}`} onClick={event => {
      const details = document.getElementById(id)
      if (!(details instanceof HTMLDetailsElement)) return
      event.preventDefault()
      if (evidenceList.current) evidenceList.current.open = true
      details.open = true
      details.querySelector('summary')?.focus({ preventScroll: true })
      details.scrollIntoView({ block: 'nearest' })
    }}>证据 {index + 1}</a>
  }
  const sections = report?.sections.filter(section => section.key !== 'evidence') ?? []
  const diagnostic = isSalesDiagnosis(question)
    ? sections.filter(section => section.key === 'anomalies' || section.key === 'performance') : []
  const reportDetails = sections.filter(section => !diagnostic.includes(section))
  function renderSection(section: AnalysisSection) {
    return <section className="analysis-section" key={section.key}>
      <h3>{section.title}</h3>
      <AnswerBody text={section.text} />
      {section.evidence_ids.length > 0 && <div className="evidence-references">{section.evidence_ids.map(evidenceLink)}</div>}
    </section>
  }

  return <div className="analysis-result">
    <Card className="answer-card" title={primary ? '各城市销售结果' : '分析结论'}>
      {data.status !== 'completed' && data.reason && <AnswerBody text={data.reason} />}
      {primary && <CitySalesAnswer answer={primary} cite={evidenceLink} />}
      {diagnostic.map(renderSection)}
      {reportDetails.length > 0 && (primary || diagnostic.length > 0 ?
        <details className="analysis-disclosure">
          <summary>查看分析报告</summary>
          {reportDetails.map(renderSection)}
        </details>
        : reportDetails.map(renderSection))}
      {data.incomplete.length > 0 && <details>
        <summary>未完成事项</summary>
        <ul>{data.incomplete.map((item, index) => <li key={index}>{item}</li>)}</ul>
      </details>}
      {data.report_validation?.status === 'passed' && <p className="evidence-metadata">报告与证据已核对</p>}
      {data.report_validation?.status === 'failed' && <p>报告校验未通过。可查看已完成任务的查询证据。</p>}
    </Card>
    {anomalies.length > 0 && <Card title="统计异常候选">
      <Table rowKey="key" pagination={{ pageSize: 10, hideOnSinglePage: true, showSizeChanger: false }}
        dataSource={anomalies.map((item, index) => ({ ...item, key: index }))} columns={[
          { title: '分组', render: (_, item) => {
            const source = data.evidence.find(evidence => evidence.evidence_id === item.evidence_id)
            const scope = source?.store_id ? `门店 ${source.store_id} · ` : ''
            return `${scope}${labels[item.dimension_id] ?? item.dimension_id} · ${item.label}`
          } },
          { title: '指标', render: (_, item) => labels[item.metric_id] ?? item.metric_id },
          { title: '观测值', dataIndex: 'value', render: (value: number) => value.toLocaleString('zh-CN') },
          { title: '中位数', dataIndex: 'median', render: (value: number) => value.toLocaleString('zh-CN') },
          { title: '方向', render: (_, item) => item.direction === 'high' ? '偏高' : '偏低' },
          { title: '来源', render: (_, item) => evidenceLink(item.evidence_id) },
        ]} />
    </Card>}
    {process}
    {data.evidence.length > 0 && <details className="analysis-evidence-list" ref={evidenceList}>
      <summary>查询证据 · {data.evidence.length} 项</summary>
      {data.evidence.map((item, index) => <EvidencePanel evidence={item} key={item.evidence_id}
        id={`${prefix}-evidence-${index}`} title={`证据 ${index + 1} · ${taskTitles.get(item.task_id) ?? item.dataset_id}`} />)}
    </details>}
  </div>
}

function pieces(text: string) {
  const chunks = text.split(/(\([^)]+\)|（[^）]+）)/)
  return chunks.map((chunk, index) => {
    const code = chunk.startsWith('(') && chunk.endsWith(')') ? chunk.slice(1, -1)
      : chunk.startsWith('（') && chunk.endsWith('）') ? chunk.slice(1, -1) : ''
    return code ? <span className="code-token" key={index}>{code}</span> : <span key={index}>{chunk}</span>
  })
}

export function AnswerBody({ text }: { text: string }) {
  const lines = text.split(/\n+/).map(line => line.trim()).filter(Boolean)
  const itemLines = lines.filter(line => /^[-•]\s+/.test(line) || line.startsWith('- '))
  if (itemLines.length >= 2) {
    const first = lines.findIndex(line => /^[-•]\s+/.test(line))
    const last = lines.reduce((index, line, current) => /^[-•]\s+/.test(line) ? current : index, -1)
    const intro = lines.slice(0, first).join('')
    const items = lines.slice(first, last + 1).map(line => line.replace(/^[-•]\s+/, ''))
    const closing = lines.slice(last + 1).join('')
    return <div className="answer">
      {intro && <p>{pieces(intro.replace(/[：:]\s*$/, ''))}</p>}
      <ul>{items.map(item => <li key={item}>{pieces(item.replace(/。$/, ''))}</li>)}</ul>
      {closing && <p className="answer-note">{pieces(closing)}</p>}
    </div>
  }
  return <div className="answer">{lines.map(paragraph => <p key={paragraph}>{pieces(paragraph)}</p>)}</div>
}

export function Feedback({ messageId, rating, onChange }: { messageId: string, rating?: Rating | null, onChange: (rating: Rating) => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function submit(value: Rating) {
    setBusy(true); setError('')
    try {
      const response = await fetch('/api/feedback', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message_id: messageId, rating: value }) })
      if (!response.ok) throw new Error()
      onChange(value)
    } catch { setError('反馈保存失败，请重试') }
    finally { setBusy(false) }
  }
  return <div className="feedback" aria-label="回答反馈">
    <span>回答是否正确？</span>
    <Button size="small" disabled={busy} type={rating === 'correct' ? 'primary' : 'default'} onClick={() => submit('correct')}>正确</Button>
    <Button size="small" disabled={busy} type={rating === 'incorrect' ? 'primary' : 'default'} onClick={() => submit('incorrect')}>错误</Button>
    {error && <span role="alert">{error}</span>}
  </div>
}
