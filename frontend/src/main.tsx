import { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Button, Card, Input, Layout, Typography } from 'antd'
import { Feedback, ResultView, type Rating, type Result } from './ResultView'
import 'antd/dist/reset.css'
import './style.css'

type Message = { id: string, role: string, content: string, result: Result | null, feedback?: Rating | null }
type ConversationSummary = { id: string, title: string }
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
      let terminalStatus: string | undefined
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
          if (kind === 'done') { terminalStatus = data.status; if (data.status === 'completed') setStatus('查询完成') }
        }
        if (done) break
      }
      if (!terminalStatus) throw new Error('响应流中断')
      if (terminalStatus !== 'completed') return
      if (activeId) {
        const detail = await fetch(`/api/conversations/${encodeURIComponent(activeId)}`)
        if (detail.ok) { setMessages((await detail.json()).messages); setAnswer(''); setResult(undefined) }
        await loadHistory()
      }
      setQuestion('')
    } catch { setStatus('连接中断或服务不可用，请重试') }
    finally { setBusy(false) }
  }
  return <Layout className="shell"><Layout.Sider className="sidebar" width={240}><div className="brand">Data Agent</div><div className="space">销售分析</div><div className="side-detail">Sales Space</div><Button block disabled={busy} onClick={newConversation}>新建对话</Button><div className="history-title">最近对话</div>{history.map(item => <button className="history-item" key={item.id} disabled={busy} onClick={() => openConversation(item.id)}>{item.title}</button>)}</Layout.Sider><Layout.Content className="content"><header><Typography.Title level={3}>销售分析工作台</Typography.Title><Typography.Text type="secondary">以自然语言探索销售数据</Typography.Text></header><main>{messages.map(message => <div key={message.id}><Card title={message.role === "user" ? "你" : "分析"}>{message.content}</Card>{message.role === 'assistant' && <Feedback messageId={message.id} rating={message.feedback} onChange={rating => setMessages(previous => previous.map(item => item.id === message.id ? { ...item, feedback: rating } : item))} />}{message.result && <ResultView data={message.result} />}</div>)}<Card className="intro"><Typography.Title level={4}>今天想了解什么？</Typography.Title><p>例如：查询今年各城市的销售额</p></Card><div className="ask"><Input.TextArea value={question} onChange={e => setQuestion(e.target.value)} onPressEnter={e => { if (!e.shiftKey) { e.preventDefault(); submit() } }} placeholder="输入销售问题" autoSize={{ minRows: 2, maxRows: 5 }}/><Button type="primary" loading={busy} onClick={submit}>发送</Button></div>{status && <p className="status">{status}</p>}{answer && <Card title="分析">{answer}</Card>}{result && <ResultView data={result} />}</main></Layout.Content></Layout>
}
createRoot(document.getElementById('root')!).render(<App />)
