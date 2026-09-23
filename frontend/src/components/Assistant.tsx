import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ChatCircleDots, CircleNotch, PaperPlaneTilt, ShieldCheck } from '@phosphor-icons/react'
import { api, type AssistantMessage, type AssistantStatus } from '../api/client'
import { Modal } from './ui'

type Message = AssistantMessage & { mode?: 'openai' | 'local' }
const suggestions = [
  'Как запустить прогноз по моим Excel?',
  'Как проверить количество к закупке?',
  'Что означает точность прогноза?',
]

export default function Assistant({
  calculationId,
  item,
}: {
  calculationId?: string
  item: { id: string; sku: string } | null
}) {
  const [open, setOpen] = useState(false)
  const [status, setStatus] = useState<AssistantStatus | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [draft, setDraft] = useState('')
  const [share, setShare] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const messagesContainer = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (open)
      void api
        .assistantStatus()
        .then(setStatus)
        .catch((cause) => setError(cause.message))
  }, [open])
  useEffect(() => {
    if (open && messagesContainer.current) {
      messagesContainer.current.scrollTop = messagesContainer.current.scrollHeight
    }
  }, [messages, busy, open])
  useEffect(() => {
    setShare(false)
    setMessages([])
    setError('')
  }, [calculationId])
  useEffect(() => {
    if (item) {
      setOpen(true)
      setShare(false)
      setMessages([])
      setDraft('Объясни рекомендацию для выбранного товара.')
    }
  }, [item])

  async function send(event?: FormEvent<HTMLFormElement>, question?: string) {
    event?.preventDefault()
    const message = (question || draft).trim()
    if (!message || busy) return
    const history = messages.slice(-10).map(({ role, content }) => ({ role, content }))
    setMessages((previous) => [...previous, { role: 'user', content: message }])
    setDraft('')
    setBusy(true)
    setError('')
    try {
      const result = await api.assistantChat({
        message,
        history,
        share_context: share,
        ...(share && calculationId
          ? { calculation_id: calculationId, ...(item ? { item_id: item.id } : {}) }
          : {}),
      })
      setMessages((previous) => [
        ...previous,
        { role: 'assistant', content: result.answer, mode: result.mode },
      ])
    } catch (cause) {
      setError((cause as Error).message)
      setMessages((previous) => previous.slice(0, -1))
      setDraft(message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <button
        className="assistant-launcher button"
        aria-label="Открыть помощника"
        onClick={() => setOpen(true)}
      >
        <ChatCircleDots size={22} />
        <span>Помощник</span>
      </button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Помощник Tirek"
        description="Поможет разобраться в источниках, прогнозе и решениях по закупке."
      >
        <div className="assistant-mode">
          <span className="status-dot online" />
          <strong>
            {status?.mode === 'openai'
              ? 'ИИ-помощник'
              : status
                ? 'Локальная справка'
                : 'Проверяем подключение…'}
          </strong>
          {status?.model && <span>{status.model}</span>}
        </div>
        <p className="field-hint">{status?.message || 'Проверяем доступный режим помощника.'}</p>
        <div
          className="assistant-messages"
          ref={messagesContainer}
          role="log"
          aria-label="Диалог с помощником"
          aria-live="polite"
        >
          {!messages.length && (
            <div className="assistant-welcome">
              <ChatCircleDots size={32} />
              <h3>С чего начнём?</h3>
              <p>Можно обсудить порядок работы или добавить контекст открытого расчёта.</p>
              <div className="assistant-suggestions">
                {suggestions.map((question) => (
                  <button
                    className="button"
                    key={question}
                    disabled={busy || !status}
                    onClick={() => void send(undefined, question)}
                  >
                    {question}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((message, index) => (
            <div className={`assistant-message assistant-message-${message.role}`} key={index}>
              <strong>
                {message.role === 'user'
                  ? 'Вы'
                  : message.mode === 'openai'
                    ? 'ИИ-помощник'
                    : 'Справка Tirek'}
              </strong>
              <p>{message.content}</p>
            </div>
          ))}
          {busy && (
            <div className="assistant-thinking" role="status">
              <CircleNotch size={17} className="spin" />
              Готовим ответ…
            </div>
          )}
        </div>
        {calculationId && (
          <label className="checkbox-label assistant-consent">
            <input
              type="checkbox"
              checked={share}
              disabled={busy}
              onChange={(event) => {
                setShare(event.target.checked)
                setMessages([])
              }}
            />
            <span>
              Использовать текущий расчёт{item ? ` и товар ${item.sku}` : ''} в ответе
              {status?.mode === 'openai' &&
                '. Сводка и данные выбранного товара будут отправлены ИИ-провайдеру.'}
            </span>
          </label>
        )}
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        <form className="assistant-input" onSubmit={(event) => void send(event)}>
          <label className="sr-only" htmlFor="assistant-message">
            Ваш вопрос
          </label>
          <textarea
            id="assistant-message"
            value={draft}
            maxLength={3000}
            rows={2}
            disabled={busy}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Например, почему нужен запас на 28 дней?"
          />
          <button
            className="button button-primary"
            type="submit"
            aria-label="Отправить вопрос"
            disabled={busy || !draft.trim() || !status}
          >
            <PaperPlaneTilt size={20} />
          </button>
        </form>
        <p className="assistant-disclaimer">
          <ShieldCheck size={16} />
          Помощник не меняет количества и не утверждает заказы.
        </p>
      </Modal>
    </>
  )
}
