import { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Button, Card, Input, Layout, Table, Typography } from 'antd'
import 'antd/dist/reset.css'
import './style.css'

type Result = { columns: string[], rows: (string | number | null)[][], sql: string, source: string, elapsed_ms: number }
type Message = { id: string, role: string, content: string, result: Result | null }
type ConversationSummary = { id: string, title: string }
const labels: Record<string, string> = { city: '城市', store: '门店', product: '产品', stat_date: '业务日期', sales_amount: '销售额（元）', order_count: '订单量', average_order_value: '客单价（元）' }
function App() {
  const [conversationId, setConversationId] = useState<string>()
  const [history, setHistory] = useState<ConversationSummary[]>([])
  const [messages, setMessages] = useState<Message[]>([])
  async function loadHistory() {
    const response = await fetch('/api/conversations')
    if (response.ok) setHistory(await response.json())
  }
  useEffect(() => { loadHistory().catch(() => setStatus('无法加载会话')) }, [])
  async function openConversation(id: string) {
    try {
      const response = await fetch(`/api/conversations/${encodeURIComponent(id)}`)
      if (!response.ok) throw new Error()
      const conversation = await response.json()
      setConversationId(id); setMessages(conversation.messages); setAnswer(''); setResult(undefined); setStatus('')
    } catch { setStatus('无法加载会话') }
  }
  function newConversation() {
    setConversationId(undefined); setMessages([]); setAnswer(''); setResult(undefined); setStatus(''); setQuestion('')
  }
  function showResult(data: Result) {
    return <Card title="查询结果"><p>{data.rows.length === 0 ? '该范围内无数据' : `${data.source} · ${data.elapsed_ms} ms`}</p><Table pagination={false} rowKey="key" dataSource={data.rows.map((row, index) => ({ key: index, ...Object.fromEntries(row.map((v, i) => [data.columns[i], v])) }))} columns={data.columns.map(name => ({ title: labels[name] ?? name, dataIndex: name, key: name }))}/><details><summary>查看 SQL</summary><pre>{data.sql}</pre></details></Card>
  }
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState('')
  const [result, setResult] = useState<Result>()
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit() {
    if (!question.trim() || busy) return
    setBusy(true); setAnswer(''); setResult(undefined); setStatus('查询中')
    setMessages(previous => [...previous, { id: crypto.randomUUID(), role: 'user', content: question, result: null }])
    let activeId = conversationId
    try {
      const response = await fetch('/api/chat/stream', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: question, conversation_id: conversationId }) })
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
          if (kind === 'conversation') { activeId = data.conversation_id; setConversationId(activeId) }
          if (kind === 'text') setAnswer(previous => previous + data.delta)
          if (kind === 'result') setResult(data)
          if (kind === 'error') setStatus(data.message)
          if (kind === 'done' && data.status === 'completed') setStatus('查询完成')
        }
        if (done) break
      }
      if (activeId) {
        const detail = await fetch(`/api/conversations/${encodeURIComponent(activeId)}`)
        if (detail.ok) { setMessages((await detail.json()).messages); setAnswer(''); setResult(undefined) }
        await loadHistory()
      }
      setQuestion('')
    } catch { setStatus('连接失败，请检查服务是否启动') }
    finally { setBusy(false) }
  }
  return <Layout className="shell"><Layout.Sider className="sidebar" width={240}><div className="brand">Data Agent</div><div className="space">销售分析</div><div className="side-detail">Sales Space</div><Button block disabled={busy} onClick={newConversation}>新建对话</Button><div className="history-title">最近对话</div>{history.map(item => <button className="history-item" key={item.id} disabled={busy} onClick={() => openConversation(item.id)}>{item.title}</button>)}</Layout.Sider><Layout.Content className="content"><header><Typography.Title level={3}>销售分析工作台</Typography.Title><Typography.Text type="secondary">以自然语言探索销售数据</Typography.Text></header><main>{messages.map(message => <div key={message.id}><Card title={message.role === "user" ? "你" : "分析"}>{message.content}</Card>{message.result && showResult(message.result)}</div>)}<Card className="intro"><Typography.Title level={4}>今天想了解什么？</Typography.Title><p>例如：查询今年各城市的销售额</p></Card><div className="ask"><Input.TextArea value={question} onChange={e => setQuestion(e.target.value)} onPressEnter={e => { if (!e.shiftKey) { e.preventDefault(); submit() } }} placeholder="输入销售问题" autoSize={{ minRows: 2, maxRows: 5 }}/><Button type="primary" loading={busy} onClick={submit}>发送</Button></div>{status && <p className="status">{status}</p>}{answer && <Card title="分析">{answer}</Card>}{result && <Card title="查询结果"><p>{result.rows.length === 0 ? '该范围内无数据' : `${result.source} · ${result.elapsed_ms} ms`}</p><Table pagination={false} rowKey={(_, i) => String(i)} dataSource={result.rows.map((row, index) => ({ key: index, ...Object.fromEntries(row.map((v, i) => [result.columns[i], v])) }))} columns={result.columns.map(name => ({ title: labels[name] ?? name, dataIndex: name, key: name }))}/><details><summary>查看 SQL</summary><pre>{result.sql}</pre></details></Card>}</main></Layout.Content></Layout>
}
createRoot(document.getElementById('root')!).render(<App />)
