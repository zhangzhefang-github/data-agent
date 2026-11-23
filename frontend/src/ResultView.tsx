import { useEffect, useRef, useState } from 'react'
import { Button, Card, Segmented, Statistic, Table } from 'antd'
import * as echarts from 'echarts'
import { highlightSql } from './sqlHighlight'

export type Result = { columns: string[], rows: (string | number | null)[][], sql: string, source: string, elapsed_ms: number }
export type Rating = 'correct' | 'incorrect'
const labels: Record<string, string> = { city: '城市', store: '门店', product: '产品', stat_date: '业务日期', sales_amount: '销售额（元）', order_count: '订单量', average_order_value: '客单价（元）', order_date: '订单日期', status: '订单状态', customer_id: '客户标识', order_amount: '订单金额', order_rows: '订单笔数', credit_card_amount: '信用卡金额', coupon_amount: '优惠券金额', bank_transfer_amount: '银行转账金额', gift_card_amount: '礼品卡金额', payment_method: '支付方式', payment_rows: '支付笔数', payment_amount: '支付金额' }
const metrics = ['sales_amount', 'order_count', 'average_order_value', 'order_amount', 'order_rows', 'credit_card_amount', 'coupon_amount', 'bank_transfer_amount', 'gift_card_amount', 'payment_rows', 'payment_amount']

type ChartKind = '柱状图' | '折线图' | '饼图'

function Chart({ data }: { data: Result }) {
  const [kind, setKind] = useState<ChartKind>('柱状图')
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
  return <Card title="查询结果">
    <p>{data.source} · {data.elapsed_ms} ms</p>
    {data.rows.length === 0 ? <p>该范围内无数据</p> : <>
      {data.rows.length === 1 && data.columns.map((column, index) => metrics.includes(column) ? <Statistic key={column} title={labels[column] ?? column} value={data.rows[0][index] ?? '—'} /> : null)}
      <Chart data={data} />
      <Table pagination={false} rowKey="key" dataSource={data.rows.map((row, index) => ({ key: index, ...Object.fromEntries(row.map((value, i) => [data.columns[i], value])) }))} columns={data.columns.map(name => ({ title: labels[name] ?? name, dataIndex: name, key: name, render: (value: unknown) => value === null ? '—' : String(value) }))} />
    </>}
    <details><summary>查看 SQL</summary><SqlCode sql={data.sql} /></details>
  </Card>
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
