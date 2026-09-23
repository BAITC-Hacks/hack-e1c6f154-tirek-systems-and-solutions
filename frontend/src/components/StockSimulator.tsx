import { useState } from 'react'
import { ArrowRight, CircleNotch, Gauge, WarningCircle } from '@phosphor-icons/react'
import { api, ApiError, type StockState } from '../api/client'
import { Badge, number } from './ui'

const levelNames = {
  normal: 'Норма', warning: 'Внимание', high: 'Высокий риск', critical: 'Критично',
  stockout: 'Нет товара', unknown: 'Нет данных',
}
const scenarioWarehouse = 'scenario-ui'

export default function StockSimulator() {
  const [sku, setSku] = useState('SIM-001')
  const [reference, setReference] = useState('100')
  const [onHand, setOnHand] = useState('60')
  const [reserved, setReserved] = useState('0')
  const [state, setState] = useState<StockState | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function initialize() {
    setBusy(true)
    setError('')
    try {
      const payload = {
        sku, warehouse_id: scenarioWarehouse, unit: 'шт', mode: 'scenario' as const,
        on_hand: Number(onHand), reserved: Number(reserved), evaluated_at: new Date().toISOString(),
        policy: {
          reference_stock: Number(reference), reference_kind: 'manual', reference_id: `ui-${sku}`,
          source_kind: 'synthetic', policy_version: `ui-${crypto.randomUUID()}`,
          thresholds: { warning_pct: 40, high_pct: 30, critical_pct: 20, hysteresis_pp: 3 },
        },
      }
      setState(await api.initializeStock(payload))
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        try {
          const saved = await api.stockState(scenarioWarehouse, sku)
          if (saved.mode !== 'scenario' || saved.policy.source_kind !== 'synthetic') {
            throw new Error('Сохранённое состояние не относится к безопасной синтетической симуляции.')
          }
          setState(saved)
          setError('Сценарий уже существовал — показана сохранённая ревизия.')
        } catch (loadError) {
          setError((loadError as Error).message)
        }
      } else setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function apply() {
    if (!state) return
    setBusy(true)
    setError('')
    try {
      const result = await api.stockEvent({
        event_id: crypto.randomUUID(), sku: state.sku, warehouse_id: state.warehouse_id,
        unit: state.unit, expected_revision: state.revision, occurred_at: new Date().toISOString(),
        event_type: 'adjustment', on_hand: Number(onHand), reserved: Number(reserved),
        source_reference: 'ui-scenario-simulator',
      })
      setState(result.state)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel stock-simulator">
      <div className="panel-heading">
        <div>
          <h2>Симуляция уровней остатка</h2>
          <p className="muted">40% → внимание · 30% → высокий риск · 20% → критично</p>
        </div>
        <Gauge size={22} />
      </div>
      <div className="form-grid">
        <label className="field">SKU<input value={sku} onChange={(e) => setSku(e.target.value)} disabled={busy || !!state} /></label>
        <label className="field">Целевой запас<input type="number" min="0.01" value={reference} onChange={(e) => setReference(e.target.value)} disabled={busy || !!state} /></label>
        <label className="field">Остаток<input type="number" min="0" value={onHand} onChange={(e) => setOnHand(e.target.value)} disabled={busy} /></label>
        <label className="field">Резерв<input type="number" min="0" value={reserved} onChange={(e) => setReserved(e.target.value)} disabled={busy} /></label>
      </div>
      {state && (
        <div className="stock-result">
          <Badge tone={state.active_level}>{levelNames[state.active_level]}</Badge>
          <strong>{number(state.remaining_pct)}%</strong>
          <span>Свободно {number(state.free_stock)} {state.unit} · ревизия {state.revision}</span>
        </div>
      )}
      {error && <p className="inline-error"><WarningCircle size={16} /> {error}</p>}
      <div className="form-actions">
        {!state ? (
          <button className="button button-primary" onClick={() => void initialize()} disabled={busy || !sku || Number(reference) <= 0}>
            {busy ? <CircleNotch className="spin" size={17} /> : <Gauge size={17} />} Начать симуляцию
          </button>
        ) : (
          <button className="button button-primary" onClick={() => void apply()} disabled={busy}>
            {busy ? <CircleNotch className="spin" size={17} /> : <ArrowRight size={17} />} Применить снимок
          </button>
        )}
      </div>
      <p className="field-hint">Симуляция синтетическая. Она не меняет 1С и не отправляет заказ поставщику.</p>
    </section>
  )
}
