import { useMemo, useState } from 'react'
import {
  ArrowDown,
  ArrowUpRight,
  MagnifyingGlass,
  SlidersHorizontal,
  CheckCircle,
  WarningCircle,
} from '@phosphor-icons/react'
import { useSearchParams } from 'react-router-dom'
import { Link } from 'react-router-dom'
import type { Item, Recommendations } from '../api/client'
import { Empty, number, Urgency } from './ui'

export default function RecommendationTable({
  data,
  onOpen,
  selected,
  onSelect,
  compact = false,
}: {
  data: Recommendations
  onOpen: (id: string) => void
  selected: string[]
  onSelect: (ids: string[]) => void
  compact?: boolean
}) {
  const [params, setParams] = useSearchParams()
  const [category, setCategory] = useState('all')
  const [supplier, setSupplier] = useState('all')
  const [sortDescending, setSortDescending] = useState(false)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const query = params.get('q') || ''
  const status = params.get('status') || 'all'
  const canSelect = (item: Item) =>
    item.final_quantity !== null && item.decision_status !== 'needs_data'
  const filtered = useMemo(() => {
    const items = data.items.filter(
      (item) =>
        (!query || `${item.sku} ${item.name}`.toLowerCase().includes(query.toLowerCase())) &&
        (category === 'all' || item.category_raw === category) &&
        (supplier === 'all' || item.supplier_id === supplier) &&
        (status === 'all' ||
          (status === 'buy' && (item.final_quantity ?? 0) > 0) ||
          (status === 'review' && ['needs_data', 'needs_review'].includes(item.decision_status)) ||
          (status === 'no_order' && item.final_quantity === 0) ||
          (status === 'critical' && item.urgency === 'critical')),
    )
    if (sortDescending) items.sort((a, b) => (b.final_quantity ?? -1) - (a.final_quantity ?? -1))
    return compact ? items.slice(0, 5) : items
  }, [data, query, category, supplier, status, sortDescending, compact])
  const selectable = filtered.filter(canSelect)
  const allChecked =
    selectable.length > 0 && selectable.every((item) => selected.includes(item.item_id))
  function setParam(key: string, value: string) {
    const next = new URLSearchParams(params)
    value ? next.set(key, value) : next.delete(key)
    setParams(next)
  }
  const toggleAll = () =>
    onSelect(
      allChecked
        ? selected.filter((id) => !selectable.some((item) => item.item_id === id))
        : [...new Set([...selected, ...selectable.map((item) => item.item_id)])],
    )
  return (
    <section className="panel table-panel">
      <div className="panel-heading">
        <div className="heading-with-count">
          <h2>{compact ? 'Рекомендации к закупке' : 'Все рекомендации'}</h2>
          <span className="count-bubble">{data.items.length}</span>
        </div>
        {compact ? (
          <Link className="text-button" to="/recommendations">
            Все позиции <ArrowUpRight size={16} />
          </Link>
        ) : (
          <span className="muted small">
            {selected.length ? `Выбрано: ${selected.length}` : 'Выберите позиции для утверждения'}
          </span>
        )}
      </div>
      {!compact && (
        <>
          <div className="table-tabs" aria-label="Фильтр рекомендаций">
            {[
              ['all', 'Все позиции', data.items.length],
              ['buy', 'К закупке', data.items.filter((i) => (i.final_quantity ?? 0) > 0).length],
              [
                'review',
                'Нужна проверка',
                data.summary.needs_data_count + data.summary.needs_review_count,
              ],
              ['no_order', 'Без закупки', data.summary.no_order_count],
            ].map(([key, label, count]) => (
              <button
                key={key}
                className={status === key ? 'active' : ''}
                onClick={() => setParam('status', String(key))}
              >
                {label}
                <span>{count}</span>
              </button>
            ))}
          </div>
          <div className="table-toolbar">
            <label className="search-field">
              <MagnifyingGlass size={18} />
              <input
                aria-label="Поиск по артикулу или названию"
                placeholder="Найти товар или артикул…"
                value={query}
                onChange={(e) => setParam('q', e.target.value)}
              />
              <kbd>/</kbd>
            </label>
            <button
              className={`button ${filtersOpen ? 'button-tinted' : ''}`}
              aria-expanded={filtersOpen}
              onClick={() => setFiltersOpen(!filtersOpen)}
            >
              <SlidersHorizontal size={17} />
              Фильтры{category !== 'all' && <span className="filter-dot" />}
            </button>
            <select
              aria-label="Поставщик"
              value={supplier}
              onChange={(e) => setSupplier(e.target.value)}
            >
              <option value="all">Все поставщики</option>
              {[
                ...new Map(
                  data.items.map((item) => [item.supplier_id, item.supplier_name]),
                ).entries(),
              ].map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </select>
          </div>
          {filtersOpen && (
            <div className="filter-row">
              <label>
                Категория
                <select value={category} onChange={(e) => setCategory(e.target.value)}>
                  <option value="all">Все категории</option>
                  {[
                    ...new Set(
                      data.items
                        .map((i) => i.category_raw)
                        .filter((cat): cat is string => cat !== null),
                    ),
                  ].map((cat) => (
                    <option key={cat}>{cat}</option>
                  ))}
                </select>
              </label>
              <label>
                Срочность
                <select
                  value={status === 'critical' ? 'critical' : 'all'}
                  onChange={(e) => setParam('status', e.target.value)}
                >
                  <option value="all">Любая срочность</option>
                  <option value="critical">Только критичные</option>
                </select>
              </label>
              <button
                className="text-button"
                onClick={() => {
                  setCategory('all')
                  setSupplier('all')
                  setParams({})
                }}
              >
                Сбросить
              </button>
            </div>
          )}
        </>
      )}
      {filtered.length === 0 ? (
        <Empty title="Подходящих позиций нет">
          <p>Попробуйте изменить поиск или сбросить фильтры.</p>
          <button
            className="button"
            onClick={() => {
              setCategory('all')
              setSupplier('all')
              setParams({})
            }}
          >
            Сбросить фильтры
          </button>
        </Empty>
      ) : (
        <div className="table-scroll">
          <table className="recommendation-table">
            <thead>
              <tr>
                <th className="checkbox-cell">
                  <input
                    type="checkbox"
                    aria-label="Выбрать все видимые позиции"
                    checked={allChecked}
                    onChange={toggleAll}
                    disabled={!selectable.length}
                  />
                </th>
                <th>Товар / артикул</th>
                <th className="numeric">Остаток</th>
                <th className="numeric optional-col">В пути</th>
                <th className="numeric">
                  Прогноз <span>28 дн.</span>
                </th>
                <th className="numeric">
                  <button
                    className="sort-button"
                    onClick={() => setSortDescending(!sortDescending)}
                  >
                    К закупке <ArrowDown size={12} />
                  </button>
                </th>
                <th>Срочность</th>
                <th className="status-cell">Проверка</th>
                <th>
                  <span className="sr-only">Подробнее</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {[...new Set(filtered.map((i) => i.supplier_id))].map((supplier) => (
                <TableGroup
                  key={supplier}
                  items={filtered.filter((i) => i.supplier_id === supplier)}
                  selected={selected}
                  onSelect={onSelect}
                  onOpen={onOpen}
                  canSelect={canSelect}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="table-footer">
        <span>
          Показано {filtered.length} из {data.items.length} позиций
        </span>
        <span>
          Единицы указаны для каждого товара <span className="footer-dot">·</span> Данные на{' '}
          {new Date(data.meta.data_as_of).toLocaleDateString('ru-RU')}
        </span>
      </div>
    </section>
  )
}

function TableGroup({
  items,
  selected,
  onSelect,
  onOpen,
  canSelect,
}: {
  items: Item[]
  selected: string[]
  onSelect: (ids: string[]) => void
  onOpen: (id: string) => void
  canSelect: (i: Item) => boolean
}) {
  return (
    <>
      <tr className="supplier-row">
        <td colSpan={9}>
          <span className="supplier-monogram">{items[0].supplier_name.slice(0, 1)}</span>
          {items[0].supplier_name}
          <span>{items.length} позиций</span>
        </td>
      </tr>
      {items.map((item) => (
        <tr key={item.item_id} className={selected.includes(item.item_id) ? 'selected-row' : ''}>
          <td className="checkbox-cell">
            <input
              type="checkbox"
              aria-label={`Выбрать ${item.sku}`}
              checked={selected.includes(item.item_id)}
              disabled={!canSelect(item)}
              onChange={(e) =>
                onSelect(
                  e.target.checked
                    ? [...selected, item.item_id]
                    : selected.filter((id) => id !== item.item_id),
                )
              }
            />
          </td>
          <td className="product-cell">
            <button onClick={() => onOpen(item.item_id)}>
              <strong>{item.name.split(',')[0]}</strong>
              <span>
                {item.sku}
                <i />
                {item.category_raw}
              </span>
            </button>
          </td>
          <td className={`numeric ${item.urgency === 'critical' ? 'danger-text' : ''}`}>
            {number(item.free_stock)} <small>{item.unit}</small>
          </td>
          <td className="numeric optional-col">
            {item.inbound_within_horizon ? (
              <span className="inbound-number">+{number(item.inbound_within_horizon)}</span>
            ) : (
              <span className="muted">{number(item.inbound_within_horizon)}</span>
            )}
          </td>
          <td className="numeric">
            {number(item.forecast?.p50 ?? item.forecast?.mean)} <small>{item.unit}</small>
          </td>
          <td className="numeric">
            <span
              className={`quantity-pill ${item.final_quantity === null ? 'quantity-unknown' : item.final_quantity === 0 ? 'quantity-zero' : ''}`}
            >
              {number(item.final_quantity)}
              {item.final_quantity !== null && <small> {item.unit}</small>}
            </span>
            {item.override_reason && (
              <span className="edited-dot" title="Количество скорректировано" />
            )}
          </td>
          <td>
            <Urgency value={item.urgency} />
          </td>
          <td className="status-cell">
            {item.decision_status === 'needs_data' || item.decision_status === 'needs_review' ? (
              <button
                className="review-link"
                onClick={() => onOpen(item.item_id)}
                title={item.reason}
              >
                <WarningCircle size={18} />
                <span>Проверить</span>
              </button>
            ) : (
              <span className="check-status">
                <CheckCircle size={19} />
                <span>Готово</span>
              </span>
            )}
          </td>
          <td>
            <button
              className="icon-button row-arrow"
              aria-label={`Открыть ${item.sku}`}
              onClick={() => onOpen(item.item_id)}
            >
              <ArrowUpRight size={18} />
            </button>
          </td>
        </tr>
      ))}
    </>
  )
}
