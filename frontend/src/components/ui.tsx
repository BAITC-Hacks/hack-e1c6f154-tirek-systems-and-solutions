import type { ReactNode } from 'react'
import { CircleNotch, WarningCircle, ArrowClockwise, X } from '@phosphor-icons/react'
import * as Dialog from '@radix-ui/react-dialog'
import type { Item } from '../api/client'

export const number = (value: number | null | undefined) =>
  value == null ? '—' : new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 }).format(value)
export const money = (value: number | null | undefined) =>
  value == null ? 'Нет цены' : `${number(value)} ₸`
export const date = (value: string, short = false) =>
  new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric',
    month: short ? 'short' : 'long',
    ...(short ? {} : { year: 'numeric' }),
  }).format(new Date(value.length === 10 ? value + 'T12:00:00' : value))
export const urgencyLabels = {
  critical: 'Критично',
  high: 'Высокая',
  normal: 'Обычная',
  low: 'Низкая',
  unknown: 'Нет данных',
}
export const statusLabels = {
  ready: 'Готово к закупке',
  no_order: 'Запаса достаточно',
  needs_data: 'Нет данных',
  needs_review: 'Нужна проверка',
}

export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: string }) {
  return (
    <span className={`badge badge-${tone}`}>
      <span className="status-dot" />
      {children}
    </span>
  )
}
export function Urgency({ value }: { value: Item['urgency'] }) {
  return <Badge tone={value}>{urgencyLabels[value]}</Badge>
}
export function Loading({ text = 'Загружаем данные…' }: { text?: string }) {
  return (
    <div className="loading" role="status">
      <CircleNotch size={23} className="spin" />
      {text}
    </div>
  )
}
export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="error-state" role="alert">
      <WarningCircle size={28} />
      <h3>Не удалось загрузить данные</h3>
      <p>{message}</p>
      {retry && (
        <button className="button" onClick={retry}>
          <ArrowClockwise size={17} />
          Повторить
        </button>
      )}
    </div>
  )
}
export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty-state">
      <div className="empty-mark">
        <span />
        <span />
        <span />
      </div>
      <h3>{title}</h3>
      <div>{children}</div>
    </div>
  )
}
export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  wide = false,
}: {
  open: boolean
  onClose: () => void
  title: string
  description: string
  children: ReactNode
  wide?: boolean
}) {
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(value) => {
        if (!value) onClose()
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className={`dialog ${wide ? 'dialog-wide' : ''}`}>
          <div className="dialog-heading">
            <div>
              <Dialog.Title>{title}</Dialog.Title>
              <Dialog.Description>{description}</Dialog.Description>
            </div>
            <Dialog.Close className="icon-button" aria-label="Закрыть">
              <X size={21} />
            </Dialog.Close>
          </div>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
