import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Button, Card, Input, Layout, Modal, Typography } from 'antd'
import { AnswerBody, Feedback, ResultView, type Rating, type Result } from './ResultView'
import 'antd/dist/reset.css'
import './style.css'

type Step = { id: string, title: string, state: 'active' | 'done' | 'error' }
type Outcome = { status: string, reason: string }

function replyText(content: string, outcome?: Outcome | null) {
  if (content.trim()) return content
  return outcome?.reason ?? ''
}
type Message = { id: string, role: string, content: string, result: Result | null, process?: Step[] | null, feedback?: Rating | null, outcome?: Outcome | null }
type ConversationSummary = { id: string, title: string }
function upsertStep(previous: Step[], step: Step): Step[] {
  const index = previous.findIndex(item => item.id === step.id)
  if (index < 0) return [...previous, step]
  const next = previous.slice()
  next[index] = step
  return next
}
function QueryProcess({ steps }: { steps: Step[] }) {
  const [open, setOpen] = useState(true)
  const live = steps.some(step => step.state === 'active')
  return <section className="process">
    <button className="process-toggle" type="button" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      {live ? '正在分析数据' : '分析过程'}
    </button>
    {open && <ul className="process-list">
      {steps.map(step => <li className={step.state} key={step.id}><span className="process-mark" />{step.title}</li>)}
    </ul>}
  </section>
}
function App() {
  const [conversationId, setConversationId] = useState<string>()
  const [history, setHistory] = useState<ConversationSummary[]>([])
  const [messages, setMessages] = useState<Message[]>([])
  const [spaceName, setSpaceName] = useState('销售分析')
  const [spaceId, setSpaceId] = useState('sales')
  const [spaceDetail, setSpaceDetail] = useState('以自然语言探索销售数据')
  async function loadHistory() {
    const response = await fetch('/api/conversations')
    if (response.ok) setHistory(await response.json())
  }
  useEffect(() => {
    loadHistory().catch(() => setStatus('无法加载会话'))
    fetch('/api/spaces').then(async response => {
      if (!response.ok) return
      const spaces = await response.json()
      if (spaces[0]?.name) setSpaceName(spaces[0].name)
      if (spaces[0]?.id) setSpaceId(spaces[0].id)
      if (spaces[0]?.description) setSpaceDetail(spaces[0].description)
    }).catch(() => setStatus('无法加载会话'))
  }, [])
  async function openConversation(id: string) {
    try {
      const response = await fetch(`/api/conversations/${encodeURIComponent(id)}`)
      if (!response.ok) throw new Error()
      const conversation = await response.json()
      setConversationId(id); setMessages(conversation.messages); setAnswer(''); setResult(undefined); setOutcome(undefined); setSteps([]); setStatus('')
    } catch { setStatus('无法加载会话') }
  }
  function newConversation() {
    setConversationId(undefined); setMessages([]); setAnswer(''); setResult(undefined); setOutcome(undefined); setSteps([]); setStatus(''); setQuestion('')
  }
  function confirmClear(id: string) {
    setClearError('')
    setPendingClearId(id)
  }
  async function clearConversation() {
    if (!pendingClearId || clearing) return
    const id = pendingClearId
    setClearing(true)
    setClearError('')
    try {
      const response = await fetch(`/api/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' })
      if (!response.ok && response.status !== 404) {
        const message = response.status === 409 ? '该会话正在查询，请稍后再试' : '无法清除会话'
        setClearError(message)
        setStatus(message)
        return
      }
      setHistory(previous => previous.filter(item => item.id !== id))
      if (conversationId === id) newConversation()
      setPendingClearId(undefined)
    } finally {
      setClearing(false)
    }
  }
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState('')
  const [result, setResult] = useState<Result>()
  const [outcome, setOutcome] = useState<Outcome>()
  const [status, setStatus] = useState('')
  const [steps, setSteps] = useState<Step[]>([])
  const [busy, setBusy] = useState(false)
  const [pendingClearId, setPendingClearId] = useState<string>()
  const [clearing, setClearing] = useState(false)
  const [clearError, setClearError] = useState('')
  const transcriptRef = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const transcript = transcriptRef.current
    if (transcript) transcript.scrollTop = transcript.scrollHeight
  }, [messages, answer, result, status, steps, conversationId])
  async function submit() {
    if (!question.trim() || busy) return
    setBusy(true); setAnswer(''); setResult(undefined); setOutcome(undefined); setStatus('')
    setSteps([{ id: 'understand', title: '理解问题', state: 'active' }])
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
          if (kind === 'progress') setSteps(previous => upsertStep(previous, data))
          if (kind === 'text') setAnswer(previous => previous + data.delta)
          if (kind === 'result') setResult(data)
          if (kind === 'error') setStatus(data.message)
          if (kind === 'done') { terminalStatus = data.status; setOutcome(data.outcome); setStatus(data.outcome?.status === 'success' ? '查询完成' : '') }
        }
        if (done) break
      }
      if (!terminalStatus) throw new Error('响应流中断')
      if (activeId) {
        const detail = await fetch(`/api/conversations/${encodeURIComponent(activeId)}`)
        if (detail.ok) { setMessages((await detail.json()).messages); setAnswer(''); setResult(undefined); setOutcome(undefined); setSteps([]) }
        await loadHistory()
      }
      setQuestion('')
    } catch {
      setAnswer(''); setResult(undefined); setOutcome({ status: 'model_error', reason: '连接中断或服务不可用，请重试' }); setStatus('')
      setSteps(previous => previous.map(step => step.state === 'active' ? { ...step, state: 'error' } : step))
    }
    finally { setBusy(false) }
  }
  return <Layout className="shell">
    <Layout.Sider className="sidebar" width={260}>
      <div className="brand">Data Agent</div>
      <div className="space">{spaceName}</div>
      <div className="side-detail">{spaceId}</div>
      <Button block disabled={busy} onClick={newConversation}>新建对话</Button>
      <div className="history-title">最近对话</div>
      {history.map(item => <div className={item.id === conversationId ? 'history-row active' : 'history-row'} key={item.id}>
        <button className="history-item" disabled={busy} onClick={() => openConversation(item.id)}>{item.title}</button>
        <button className="history-delete" type="button" aria-label="清除会话" disabled={busy} onClick={() => confirmClear(item.id)}>
          <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path fill="currentColor" d="M6.2 2.2h3.6l.4.8H13v1.2H3V3h2.8l.4-.8zM4.2 5.2h7.6l-.5 8.1a1 1 0 0 1-1 .9H5.7a1 1 0 0 1-1-.9l-.5-8.1z" /></svg>
        </button>
      </div>)}
    </Layout.Sider>
    <Layout.Content className="content">
      <header>
        <div className="heading">
          <Typography.Title level={3}>{spaceName}</Typography.Title>
          <Typography.Text type="secondary">{spaceDetail}</Typography.Text>
        </div>
        {conversationId && <Button danger disabled={busy} onClick={() => confirmClear(conversationId)}>清除会话</Button>}
      </header>
      <div className="transcript" ref={transcriptRef}>
        {messages.map(message => <div className="turn" key={message.id}>
          {message.role === 'user' ? <div className="user-turn">{message.content}</div> : <>
            {message.process && message.process.length > 0 && <QueryProcess steps={message.process} />}
            <Card className="answer-card" title="说明">
              <AnswerBody text={replyText(message.content, message.outcome)} />
              <Feedback messageId={message.id} rating={message.feedback} onChange={rating => setMessages(previous => previous.map(item => item.id === message.id ? { ...item, feedback: rating } : item))} />
            </Card>
          </>}
          {message.result && <ResultView data={message.result} />}
        </div>)}
        {messages.length === 0 && !answer && <Card className="intro"><Typography.Title level={4}>今天想了解什么？</Typography.Title><p>{spaceId === 'orders' ? '例如：查询各订单状态的订单金额' : '例如：查询今年各城市的销售额'}</p></Card>}
        {steps.length > 0 && <QueryProcess steps={steps} />}
        {status && <p className="status">{status}</p>}
        {(answer || outcome?.reason) && <Card className="answer-card" title="说明"><AnswerBody text={replyText(answer, outcome)} /></Card>}
        {result && (!outcome || outcome.status === 'success') && <ResultView data={result} />}
      </div>
      <div className="composer">
        <div className="ask">
          <Input.TextArea value={question} onChange={e => setQuestion(e.target.value)} onPressEnter={e => { if (!e.shiftKey) { e.preventDefault(); submit() } }} placeholder="输入销售问题" autoSize={{ minRows: 2, maxRows: 5 }} />
          <Button type="primary" loading={busy} onClick={submit}>发送</Button>
        </div>
      </div>
    </Layout.Content>
    <Modal
      open={pendingClearId !== undefined}
      title="清除这条会话？"
      okText="确认清除"
      cancelText="取消"
      okButtonProps={{ danger: true, loading: clearing }}
      cancelButtonProps={{ disabled: clearing }}
      onOk={clearConversation}
      onCancel={() => { if (!clearing) setPendingClearId(undefined) }}
    >
      清除后无法恢复，这条会话及其消息会从最近对话中移除。
      {clearError && <p className="status">{clearError}</p>}
    </Modal>
  </Layout>
}
createRoot(document.getElementById('root')!).render(<App />)
