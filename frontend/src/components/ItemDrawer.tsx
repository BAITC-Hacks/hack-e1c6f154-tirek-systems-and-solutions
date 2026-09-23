import { useEffect, useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { ArrowRight, Check, CircleNotch, Info, Package, Sparkle, X } from '@phosphor-icons/react'
import { api, ApiError, type ItemDetail } from '../api/client'
import { Badge, date, ErrorState, Loading, money, number, Urgency } from './ui'
import DemandChart from './DemandChart'

export default function ItemDrawer({
  calculationId,
  itemId,
  onClose,
  onSaved,
  aiAvailable,
  sourceKind,
  onReviewed,
}: {
  calculationId: string
  itemId: string | null
  onClose: () => void
  onSaved: () => Promise<void>
  aiAvailable: boolean
  sourceKind?: string
  onReviewed: () => Promise<void>
}) {
  const [detail, setDetail] = useState<ItemDetail | null>(null)
  const [error, setError] = useState('')
  const [formError, setFormError] = useState('')
  const [quantity, setQuantity] = useState('')
  const [reason, setReason] = useState('')
  const [saving, setSaving] = useState(false)
  const [tab, setTab] = useState('overview')
  const [reload, setReload] = useState(0)
  const [reviewing, setReviewing] = useState(false)
  const [reviewError, setReviewError] = useState('')
  useEffect(() => {
    if (!itemId) return
    const controller = new AbortController()
    setDetail(null)
    setError('')
    setFormError('')
    setReviewError('')
    setReason('')
    setTab('overview')
    api
      .detail(calculationId, itemId, controller.signal)
      .then((value) => {
        setDetail(value)
        setQuantity(value.item.final_quantity == null ? '' : String(value.item.final_quantity))
      })
      .catch((e) => {
        if (e.name !== 'AbortError') setError(e.message)
      })
    return () => controller.abort()
  }, [calculationId, itemId, reload])

  async function review() {
    if (!detail || !itemId || reviewing) return
    setReviewing(true)
    setReviewError('')
    try {
      setDetail(await api.review(calculationId, itemId, detail.meta.revision))
      await onReviewed()
    } catch (error) {
      setReviewError((error as Error).message)
      if (error instanceof ApiError && error.code === 'STALE_REVISION')
        setReload((value) => value + 1)
    } finally {
      setReviewing(false)
    }
  }

  async function save(exclude = false) {
    if (!detail || !itemId) return
    if (!reason.trim()) {
      setFormError('Укажите причину корректировки.')
      return
    }
    if (!exclude && (quantity.trim() === '' || !Number.isFinite(Number(quantity)))) {
      setFormError('Введите корректное количество.')
      return
    }
    setSaving(true)
    setFormError('')
    try {
      const updated = await api.override(calculationId, itemId, {
        expected_revision: detail.meta.revision,
        final_quantity: exclude ? null : Number(quantity),
        reason,
      })
      setDetail(updated)
      await onSaved()
      onClose()
    } catch (e) {
      setFormError((e as Error).message)
      if (e instanceof ApiError && e.code === 'STALE_REVISION') {
        try {
          setDetail(await api.detail(calculationId, itemId))
          await onSaved()
        } catch {
          /* Keep the conflict visible. */
        }
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog.Root
      open={!!itemId}
      onOpenChange={(open) => {
        if (!open && !saving && !reviewing) onClose()
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="item-drawer">
          <div className="drawer-top">
            <div className="drawer-kicker">
              <Package size={18} />
              Карточка товара
            </div>
            <Dialog.Close
              className="icon-button"
              disabled={saving || reviewing}
              aria-label="Закрыть карточку"
            >
              <X size={22} />
            </Dialog.Close>
          </div>
          <Dialog.Title className="drawer-title">
            {detail?.item.name.split(',')[0] || 'Рекомендация по товару'}
          </Dialog.Title>
          <Dialog.Description className="drawer-description">
            {detail
              ? `${detail.item.sku} · ${detail.item.supplier_name}`
              : 'Данные и основания для решения о закупке'}
          </Dialog.Description>
          {error ? (
            <ErrorState message={error} retry={() => setReload((value) => value + 1)} />
          ) : !detail ? (
            <Loading />
          ) : (
            <>
              <div className="drawer-tags">
                <Urgency value={detail.item.urgency} />
                <Badge tone={sourceKind === 'synthetic' ? 'scenario' : 'normal'}>
                  {sourceKind === 'synthetic'
                    ? 'Синтетический пример'
                    : detail.item.forecast?.method === 'ml'
                      ? 'ML-прогноз'
                      : 'Расчёт по источникам'}
                </Badge>
              </div>
              <div className="drawer-tabs" role="tablist" aria-label="Разделы карточки">
                {[
                  ['overview', 'Обзор'],
                  ['evidence', 'Обоснование'],
                  ['edit', 'Корректировка'],
                ].map(([id, label]) => (
                  <button
                    key={id}
                    role="tab"
                    aria-selected={tab === id}
                    onClick={() => setTab(id)}
                    className={tab === id ? 'active' : ''}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div className="drawer-scroll" role="tabpanel">
                {tab === 'overview' && (
                  <>
                    <div className="recommendation-callout">
                      <div>
                        <span>
                          {detail.item.recommended_quantity == null
                            ? 'Прогноз спроса на 28 дней'
                            : 'Рекомендуем к закупке'}
                        </span>
                        <strong>
                          {number(detail.item.recommended_quantity ?? detail.item.forecast?.mean)}{' '}
                          <small>{detail.item.unit}</small>
                        </strong>
                      </div>
                      <ArrowRight size={28} />
                      <div>
                        <span>Текущий остаток</span>
                        <strong>
                          {number(detail.item.free_stock)} <small>{detail.item.unit}</small>
                        </strong>
                      </div>
                    </div>
                    <h3>История и прогноз спроса</h3>
                    <DemandChart detail={detail} />
                    <div className="fact-grid">
                      <div>
                        <span>В пути на горизонт</span>
                        <strong>
                          {number(detail.item.inbound_within_horizon)} {detail.item.unit}
                        </strong>
                      </div>
                      <div>
                        <span>Минимальная партия</span>
                        <strong>
                          {number(detail.item.min_order_qty)} {detail.item.unit}
                        </strong>
                      </div>
                      <div>
                        <span>Кратность поставки</span>
                        <strong>
                          {number(detail.item.order_multiple)} {detail.item.unit}
                        </strong>
                      </div>
                      <div>
                        <span>Стоимость заказа</span>
                        <strong>{money(detail.item.order_cost_kzt)}</strong>
                      </div>
                    </div>
                    {detail.inbound.length > 0 && (
                      <>
                        <h3>Ожидаемые поступления</h3>
                        {detail.inbound.map((inbound) => (
                          <div className="source-line" key={inbound.order_id}>
                            <Package size={18} />
                            <span>
                              {number(inbound.quantity)} {detail.item.unit}
                            </span>
                            <span className="muted">{date(inbound.expected_at)}</span>
                          </div>
                        ))}
                      </>
                    )}
                    <h3>Почему такая рекомендация</h3>
                    <p className="reason-copy">{detail.item.reason}</p>
                    {detail.item.override_reason && (
                      <div className="notice">
                        <Check size={18} />
                        <span>
                          Количество изменено на {number(detail.item.final_quantity)}.{' '}
                          {detail.item.override_reason}
                        </span>
                      </div>
                    )}
                  </>
                )}
                {tab === 'evidence' && (
                  <>
                    <h3>Факты и источники</h3>
                    <p className="muted">
                      {sourceKind === 'synthetic'
                        ? 'У каждого параметра есть происхождение. В демо все значения синтетические.'
                        : 'Здесь показаны значения расчёта и ссылки на их источники.'}
                    </p>
                    {detail.item.evidence.map((e) => (
                      <div className="evidence-row" key={e.id}>
                        <div>
                          <strong>{e.label}</strong>
                          <small>{e.reference}</small>
                        </div>
                        <strong>
                          {String(e.value ?? '—')} {e.unit}
                        </strong>
                      </div>
                    ))}
                    <h3>Политика запаса</h3>
                    <div className="fact-grid">
                      <div>
                        <span>Основание</span>
                        <strong>
                          {detail.item.policy_basis === 'economic'
                            ? 'Экономический профиль'
                            : detail.item.policy_basis === 'service_policy'
                              ? 'Уровень обслуживания'
                              : 'Не задано'}
                        </strong>
                      </div>
                      <div>
                        <span>Целевой квантиль</span>
                        <strong>
                          {detail.item.forecast?.target_quantile == null
                            ? 'Не рассчитан'
                            : `${number(detail.item.forecast.target_quantile * 100)}%`}
                        </strong>
                      </div>
                      <div>
                        <span>Стоимость дефицита</span>
                        <strong>{money(detail.economic_profile?.underage_cost)}</strong>
                      </div>
                      <div>
                        <span>Стоимость излишка</span>
                        <strong>{money(detail.economic_profile?.overage_cost)}</strong>
                      </div>
                    </div>
                    <h3>Проверка ИИ</h3>
                    <button
                      className="button"
                      disabled={!aiAvailable || reviewing || saving}
                      onClick={() => void review()}
                    >
                      {reviewing ? (
                        <CircleNotch className="spin" size={18} />
                      ) : (
                        <Sparkle size={18} />
                      )}
                      {reviewing ? 'ИИ проверяет факты…' : 'Проверить с ИИ'}
                    </button>
                    {!aiAvailable && (
                      <p className="muted">
                        ИИ-провайдер не настроен. Подключение указано в настройках пространства.
                      </p>
                    )}
                    {reviewError && (
                      <p role="alert" className="inline-error">
                        {reviewError}
                      </p>
                    )}
                    <div className="notice">
                      <Sparkle size={22} />
                      <div>
                        <strong>
                          {detail.item.ai.status === 'reviewed'
                            ? {
                                supports: 'Рекомендация подтверждается',
                                needs_review: 'Нужна проверка',
                                recalculate: 'Нужен пересчёт',
                              }[detail.item.ai.verdict || 'needs_review']
                            : 'ИИ-проверка не выполнялась'}
                        </strong>
                        <p>{detail.item.ai.reasons.join(' ')}</p>
                        {detail.item.ai.provider_model && (
                          <small>Модель: {detail.item.ai.provider_model}</small>
                        )}
                        {detail.item.ai.suggested_action && (
                          <p>{detail.item.ai.suggested_action}</p>
                        )}
                      </div>
                    </div>
                    {detail.item.issues.map((issue) => (
                      <div className="notice warning" key={issue.code}>
                        <Info size={18} />
                        <span>{issue.message}</span>
                      </div>
                    ))}
                    <h3>Версии</h3>
                    <dl className="version-list">
                      <dt>Данные</dt>
                      <dd>{detail.meta.dataset_version}</dd>
                      <dt>Метод</dt>
                      <dd>
                        {detail.item.forecast?.method === 'contract_example'
                          ? 'Синтетический пример, без ML'
                          : detail.item.forecast?.method || 'Не рассчитан'}
                      </dd>
                      <dt>Правила</dt>
                      <dd>{detail.meta.policy_version}</dd>
                      <dt>Ревизия</dt>
                      <dd>{detail.meta.revision}</dd>
                    </dl>
                  </>
                )}
                {tab === 'edit' && (
                  <form
                    onSubmit={(e) => {
                      e.preventDefault()
                      void save()
                    }}
                  >
                    <h3>Ваше решение</h3>
                    <p className="muted">
                      Исходная рекомендация сохранится. Для любого изменения необходимо указать
                      причину.
                    </p>
                    <label className="field">
                      Количество, {detail.item.unit}
                      <input
                        type="number"
                        min="0"
                        step="1"
                        value={quantity}
                        onChange={(e) => setQuantity(e.target.value)}
                        disabled={detail.item.decision_status === 'needs_data'}
                      />
                    </label>
                    <label className="field">
                      Причина корректировки
                      <textarea
                        value={reason}
                        onChange={(e) => setReason(e.target.value)}
                        placeholder="Например, ожидается дополнительная поставка по проекту"
                        rows={4}
                        maxLength={2000}
                      />
                    </label>
                    {formError && (
                      <p className="inline-error" role="alert">
                        {formError}
                      </p>
                    )}
                    {detail.item.decision_status === 'needs_data' && (
                      <div className="notice warning">
                        Количество нельзя задать без критичных исходных данных. Позицию можно
                        исключить с указанием причины.
                      </div>
                    )}
                    <div className="form-actions">
                      <button
                        className="button button-primary"
                        disabled={saving || detail.item.decision_status === 'needs_data'}
                      >
                        {saving ? <CircleNotch className="spin" size={17} /> : <Check size={17} />}
                        Сохранить
                      </button>
                      <button
                        type="button"
                        className="button"
                        disabled={saving}
                        onClick={() => void save(true)}
                      >
                        Исключить позицию
                      </button>
                    </div>
                  </form>
                )}
              </div>
              {tab !== 'edit' && (
                <div className="drawer-footer">
                  <span>Ревизия {detail.meta.revision}</span>
                  <button className="button button-primary" onClick={() => setTab('edit')}>
                    Скорректировать <ArrowRight size={17} />
                  </button>
                </div>
              )}
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
