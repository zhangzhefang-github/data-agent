import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Button, Card, Input, Layout, Table, Typography } from 'antd'
import 'antd/dist/reset.css'
import './style.css'

type Result = { columns: string[], rows: (string | number | null)[][], sql: string, source: string, elapsed_ms: number }
function App() {
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState('')
  const [result, setResult] = useState<Result>()
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit() {
    if (!question.trim() || busy) return
    setBusy(true); setAnswer(''); setResult(undefined); setStatus('查询中')
    try {
      const response = await fetch('/api/chat/stream', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: question }) })
      if (!response.ok || !response.body) throw new Error('服务不可用')
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let pending = ''
      while (true) {
        const { value, done } = await reader.read()
        pending += decoder.decode(value, { stream: !done })
        const chunks = pending.split('\n\n')
        pending = chunks.pop() ?? ''
        for (const chunk of chunks) {
          const kind = chunk.split('\n').find(line => line.startsWith('event: '))?.slice(7)
          const raw = chunk.split('\n').find(line => line.startsWith('data: '))?.slice(6)
          if (!raw) continue
          const data = JSON.parse(raw)
          if (kind === 'text') setAnswer(previous => previous + data.delta)
          if (kind === 'result') setResult(data)
          if (kind === 'error') setStatus(data.message)
          if (kind === 'done' && data.status === 'completed') setStatus('查询完成')
        }
        if (done) break
      }
    } catch { setStatus('连接失败，请检查服务是否启动') }
    finally { setBusy(false) }
  }
  return <Layout className="shell"><Layout.Sider className="sidebar" width={240}><div className="brand">Data Agent</div><div className="space">销售分析</div><div className="side-detail">Sales Space</div></Layout.Sider><Layout.Content className="content"><header><Typography.Title level={3}>销售分析工作台</Typography.Title><Typography.Text type="secondary">以自然语言探索销售数据</Typography.Text></header><main><Card className="intro"><Typography.Title level={4}>今天想了解什么？</Typography.Title><p>例如：查询今年各城市的销售额</p></Card><div className="ask"><Input.TextArea value={question} onChange={e => setQuestion(e.target.value)} onPressEnter={e => { if (!e.shiftKey) { e.preventDefault(); submit() } }} placeholder="输入销售问题" autoSize={{ minRows: 2, maxRows: 5 }}/><Button type="primary" loading={busy} onClick={submit}>发送</Button></div>{status && <p className="status">{status}</p>}{answer && <Card title="分析">{answer}</Card>}{result && <Card title="查询结果"><p>{result.rows.length === 0 ? '该范围内无数据' : `${result.source} · ${result.elapsed_ms} ms`}</p><Table pagination={false} rowKey={(_, i) => String(i)} dataSource={result.rows.map((row, index) => ({ key: index, ...Object.fromEntries(row.map((v, i) => [result.columns[i], v])) }))} columns={result.columns.map(name => ({ title: name === 'city' ? '城市' : '销售额（元）', dataIndex: name, key: name }))}/><details><summary>查看 SQL</summary><pre>{result.sql}</pre></details></Card>}</main></Layout.Content></Layout>
}
createRoot(document.getElementById('root')!).render(<App />)
