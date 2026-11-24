import type { ReactNode } from 'react'
import { Table, type TableColumnsType } from 'antd'
import type { AnalysisEvidence, AnalysisResult } from './ResultView'

const citySalesRequest = /(?:各(?:个)?|每个|所有|按|分)(?:城市|地区)(?:的|分组|统计|查询|查看|汇总|今年|本年|本月|上月|\s)*(?:净?销售额|销售金额)|(?:净?销售额|销售金额)\s*(?:按|分)(?:城市|地区)|(?:城市|地区)(?:净?销售额|销售金额)(?:排名|排行)/
const otherBreakdown = /趋势|同比|环比|对比|比较|下钻|每天|每日|按日|每月|按月|各月|各门店|按门店|各产品|按产品/
const currency = new Intl.NumberFormat('zh-CN', {
  style: 'currency', currency: 'CNY', minimumFractionDigits: 0, maximumFractionDigits: 2,
})
const integer = new Intl.NumberFormat('zh-CN')
const filterLabels: Record<string, string> = { city: '城市', store: '门店', product: '产品' }

export function isSalesDiagnosis(question: string) {
  return /原因|为什么|为何|怎么会|异常|反常|离群|波动|偏高|偏低|\b(?:anomal(?:y|ies)|outliers?|why)\b/i.test(question)
}

function sameSource(left: AnalysisEvidence, right: AnalysisEvidence) {
  return left.source === right.source && left.dataset_id === right.dataset_id && left.snapshot_id === right.snapshot_id
}

function sameScope(left: AnalysisEvidence, right: AnalysisEvidence) {
  const filterKey = (item: AnalysisEvidence) => JSON.stringify((item.filters ?? [])
    .map(filter => JSON.stringify([filter.dimension_id, filter.value])).sort())
  return sameSource(left, right)
    && left.time_range?.start === right.time_range?.start && left.time_range?.end === right.time_range?.end
    && filterKey(left) === filterKey(right)
}

export function citySalesAnswer(question: string, data: AnalysisResult) {
  if (!data.report || data.report_validation?.status !== 'passed'
    || !citySalesRequest.test(question) || isSalesDiagnosis(question) || otherBreakdown.test(question)) return null

  const evidence = data.evidence.find(item => item.task_type === 'dimension_breakdown'
    && item.columns[0] === 'city' && item.columns.includes('sales_amount'))
  if (!evidence?.time_range || evidence.possible_truncation
    || data.findings.some(item => item.kind === 'data_quality' && item.evidence_id === evidence.evidence_id)) return null

  const summary = data.findings.find(item => item.kind === 'metric_summary'
    && item.evidence_id === evidence.evidence_id && item.metric_id === 'sales_amount' && item.dimension_id === 'city')
  if (summary?.kind !== 'metric_summary' || summary.status === 'partial_evidence' || summary.total === null
    || summary.sample_size !== evidence.rows.length) return null

  const daily = data.evidence.find(item => item.task_type === 'daily_trend' && !item.possible_truncation
    && sameScope(evidence, item))
  const coverage = data.findings.filter(item => item.kind === 'calendar_coverage')
    .find(item => item.evidence_id === daily?.evidence_id)
  const sourceProfile = data.evidence.find(item => item.task_type === 'profile_data_range' && sameSource(evidence, item))
  const profile = data.findings.filter(item => item.kind === 'data_profile')
    .find(item => item.evidence_id === sourceProfile?.evidence_id)

  const salesIndex = evidence.columns.indexOf('sales_amount')
  const ordersIndex = evidence.columns.indexOf('order_count')
  const averageIndex = evidence.columns.indexOf('average_order_value')
  const rows = evidence.rows.map(row => ({
    city: String(row[0]), sales: Number(row[salesIndex]),
    orders: typeof row[ordersIndex] === 'number' ? row[ordersIndex] : null,
    average: typeof row[averageIndex] === 'number' ? row[averageIndex] : null,
  })).sort((left, right) => right.sales - left.sales)
  return { evidence, span: evidence.time_range, total: summary.total, coverage, profile, rows,
    hasOrders: ordersIndex >= 0, hasAverage: averageIndex >= 0 }
}

type CityAnswer = NonNullable<ReturnType<typeof citySalesAnswer>>
type CityRow = CityAnswer['rows'][number]

export function CitySalesAnswer({ answer, cite }: { answer: CityAnswer, cite: (id: string) => ReactNode }) {
  const { evidence, span, total, coverage, profile, rows, hasOrders, hasAverage } = answer
  const columns: TableColumnsType<CityRow> = [
    { title: '城市', dataIndex: 'city' },
    { title: '净销售额', dataIndex: 'sales', align: 'right', render: value => currency.format(value) },
  ]
  if (hasOrders) columns.push({ title: '订单量', dataIndex: 'orders', align: 'right',
    render: value => value === null ? '—' : integer.format(value) })
  if (hasAverage) columns.push({ title: '客单价', dataIndex: 'average', align: 'right',
    render: value => value === null ? '—' : currency.format(value) })

  const filteredBy = evidence.filters?.map(filter => `${filterLabels[filter.dimension_id] ?? filter.dimension_id} ${filter.value}`).join('、')
  const showCoverage = coverage && (coverage.first_business_date !== span.start
    || coverage.last_business_date !== span.end || coverage.missing_dates.length > 0)
  const observedSpan = coverage && (coverage.first_business_date === coverage.last_business_date
    ? coverage.first_business_date : `${coverage.first_business_date} 至 ${coverage.last_business_date}`)
  const topCity = rows.length === 1 || (rows.length > 1 && rows[0].sales > rows[1].sales) ? rows[0].city : null

  return <div className="city-sales-answer">
    <p className="analysis-scope">分析范围 {span.start === span.end ? span.start : `${span.start} 至 ${span.end}`} · {evidence.source}</p>
    {filteredBy && <p className="analysis-scope">筛选条件 {filteredBy}</p>}
    {showCoverage && <p className="analysis-coverage">
      本次查询仅覆盖 <strong>{coverage.observed_days} 个业务日</strong>（{observedSpan}）。金额按已覆盖数据汇总。{cite(coverage.evidence_id)}
    </p>}
    {!coverage && rows.length > 0 && <p className="analysis-coverage">
      本次查询的业务日期覆盖未经按日核对。{profile?.first_business_date && profile.last_business_date && <>
        数据源的业务日期为 {profile.first_business_date} 至 {profile.last_business_date}。{cite(profile.evidence_id)}
      </>}
    </p>}
    {rows.length > 0 ? <>
      <p className="analysis-summary">净销售额合计 <strong>{currency.format(total)}</strong>{topCity && rows.length > 1 ? `，${topCity}最高` : ''}。{cite(evidence.evidence_id)}</p>
      <Table<CityRow> caption="各城市净销售额" size="small" rowKey="city" columns={columns} dataSource={rows}
        pagination={{ pageSize: 10, hideOnSinglePage: true, showSizeChanger: false }} />
    </> : <p className="analysis-summary">所选范围内没有城市销售记录。{cite(evidence.evidence_id)}</p>}
  </div>
}
