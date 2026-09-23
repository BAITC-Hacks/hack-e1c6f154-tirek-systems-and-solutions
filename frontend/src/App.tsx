import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, NavLink, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import {
  ArrowClockwise,
  ArrowRight,
  ArrowUpRight,
  Bell,
  Buildings,
  CaretDown,
  ChartLineUp,
  Check,
  CheckCircle,
  CircleNotch,
  Database,
  DownloadSimple,
  GearSix,
  Info,
  List,
  MagnifyingGlass,
  Package,
  Plus,
  SealCheck,
  ShoppingCartSimple,
  SlidersHorizontal,
  Sparkle,
  SquaresFour,
  Stack,
  WarningCircle,
  X,
} from '@phosphor-icons/react'
import {
  api,
  ApiError,
  type Approval,
  type CalculationRequest,
  type Health,
  type ItemDetail,
  type Job,
  type Recommendations,
  type Workspace,
} from './api/client'
import { Badge, date, Empty, ErrorState, Loading, Modal, number } from './components/ui'
import DemandChart from './components/DemandChart'
import ItemDrawer from './components/ItemDrawer'
import RecommendationTable from './components/RecommendationTable'
import DataPage from './pages/DataPage'

const navigation = [
  { to: '/', label: 'Обзор', icon: SquaresFour },
  { to: '/recommendations', label: 'Рекомендации', icon: ShoppingCartSimple },
  { to: '/data', label: 'Источники данных', icon: Database },
  { to: '/approvals', label: 'История решений', icon: Stack },
]

export default function App() {
  const navigate = useNavigate()
  const location = useLocation()
  const [workspace, setWorkspace] = useState<Workspace | null>(null)
  const [data, setData] = useState<Recommendations | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [itemId, setItemId] = useState<string | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [calculationOpen, setCalculationOpen] = useState(false)
  const [calculationDataset, setCalculationDataset] = useState<string | null>(null)
  const [approvalOpen, setApprovalOpen] = useState(false)
  const [toast, setToast] = useState('')
  const [search, setSearch] = useState('')
  const currentId = useRef<string | null>(null)
  const searchRef = useRef<HTMLInputElement>(null)

  const refresh = useCallback(async () => {
    const [nextWorkspace, nextHealth] = await Promise.all([api.workspace(), api.health()])
    const id = currentId.current || nextWorkspace.latest_calculation_id
    const nextData = id ? await api.recommendations(id) : null
    currentId.current = id
    setWorkspace(nextWorkspace)
    setHealth(nextHealth)
    setData(nextData)
    setSelected((ids) =>
      ids.filter((id) =>
        nextData?.items.some((item) => item.item_id === id && item.final_quantity !== null),
      ),
    )
    setError('')
  }, [])

  useEffect(() => {
    void refresh()
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [refresh])
  useEffect(() => {
    setSidebarOpen(false)
  }, [location.pathname])
  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(''), 5000)
    return () => clearTimeout(timer)
  }, [toast])
  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      if (
        event.key === '/' &&
        !['INPUT', 'TEXTAREA', 'SELECT'].includes((event.target as HTMLElement).tagName)
      ) {
        event.preventDefault()
        searchRef.current?.focus()
      }
    }
    window.addEventListener('keydown', listener)
    return () => window.removeEventListener('keydown', listener)
  }, [])

  const title = navigation.find((nav) => nav.to === location.pathname)?.label || 'Настройки'
  const reviews = data ? data.summary.needs_data_count + data.summary.needs_review_count : 0
  const activeDataset = workspace?.datasets.find(
    (dataset) => dataset.dataset_id === data?.meta.dataset_id,
  )
  const download = async (id: string) => {
    try {
      await api.download(id)
      setToast('CSV скачан из утверждённого снимка.')
    } catch (e) {
      setToast((e as Error).message)
    }
  }
  const saved = async () => {
    await refresh()
    setToast('Корректировка сохранена. Ревизия расчёта обновлена.')
  }
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Перейти к содержимому
      </a>
      {sidebarOpen && (
        <button
          className="sidebar-scrim"
          aria-label="Закрыть меню"
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <aside className={`sidebar ${sidebarOpen ? 'sidebar-open' : ''}`}>
        <Link className="brand" to="/" aria-label="Tirek — обзор">
          <div className="brand-mark">
            <span />
            <span />
            <span />
          </div>
          <span>
            tirek<span className="brand-period">.</span>
          </span>
        </Link>
        <div className="workspace-switch">
          <span className="workspace-icon">
            <Buildings size={21} />
          </span>
          <div>
            <strong>Tirek workspace</strong>
            <span>Управление закупками</span>
          </div>
          <CaretDown size={14} />
        </div>
        <span className="nav-label">РАБОЧЕЕ ПРОСТРАНСТВО</span>
        <nav aria-label="Основная навигация">
          {navigation.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} end={to === '/'}>
              <Icon size={21} weight={location.pathname === to ? 'fill' : 'regular'} />
              <span>{label}</span>
              {to === '/recommendations' && data && (
                <span className="nav-count">{data.items.length}</span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="demo-sidebar">
            <span className="demo-sidebar-icon">
              <Sparkle size={21} />
            </span>
            <strong>От данных к решению</strong>
            <p>
              Прозрачные рекомендации.
              <br />
              Закупки под вашим контролем.
            </p>
            <Link to="/data">
              Посмотреть источники <ArrowUpRight size={15} />
            </Link>
          </div>
          <NavLink className="settings-link" to="/settings">
            <GearSix size={20} />
            Настройки
          </NavLink>
          <div className="sidebar-status">
            <span className={`status-dot ${health ? 'online' : ''}`} />
            {health ? 'Локальное рабочее пространство' : 'Подключение к серверу…'}
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumbs">
            <button
              className="icon-button mobile-menu"
              onClick={() => setSidebarOpen(true)}
              aria-label="Открыть меню"
            >
              <List size={23} />
            </button>
            <span className="breadcrumb-root">Рабочее пространство</span>
            <span className="breadcrumb-slash">/</span>
            <strong>{title}</strong>
          </div>
          <div className="topbar-actions">
            <form
              className="global-search"
              onSubmit={(e) => {
                e.preventDefault()
                navigate(`/recommendations?q=${encodeURIComponent(search)}`)
              }}
            >
              <MagnifyingGlass size={17} />
              <input
                ref={searchRef}
                aria-label="Поиск товаров"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Поиск товаров"
              />
              <kbd>/</kbd>
            </form>
            <button
              className="notification-button icon-button"
              aria-label={`Требуют внимания: ${reviews}`}
              onClick={() => navigate('/recommendations?status=review')}
            >
              <Bell size={21} />
              {reviews > 0 && <span />}
            </button>
            <span className="topbar-divider" />
            <Link
              to="/settings"
              className="user-avatar"
              aria-label="Настройки рабочего пространства"
            >
              АД
            </Link>
          </div>
        </header>
        <main id="main-content" className="main-content">
          {loading ? (
            <div className="initial-loading">
              <Loading text="Открываем рабочее пространство…" />
            </div>
          ) : error ? (
            <ErrorState
              message={error}
              retry={() => {
                setLoading(true)
                void refresh()
                  .catch((e) => setError(e.message))
                  .finally(() => setLoading(false))
              }}
            />
          ) : (
            workspace &&
            data && (
              <>
                <div className="scenario-bar">
                  <span>
                    <span className="scenario-indicator" />
                    <strong>
                      {data.meta.mode === 'scenario' ? 'Сценарный расчёт' : 'Операционный расчёт'}
                    </strong>
                    <span className="scenario-detail">
                      {activeDataset?.source_kind === 'synthetic'
                        ? 'Синтетические данные · демонстрация платформы'
                        : activeDataset?.source_kind === 'observed'
                          ? 'Данные загруженных источников'
                          : 'Смешанные источники'}
                    </span>
                  </span>
                  <Link to="/settings">
                    О режиме <ArrowUpRight size={14} />
                  </Link>
                </div>
                <Routes>
                  <Route
                    path="/"
                    element={
                      <Overview
                        data={data}
                        onOpen={setItemId}
                        onCalculate={() => setCalculationOpen(true)}
                        selected={selected}
                        onSelect={setSelected}
                      />
                    }
                  />
                  <Route
                    path="/recommendations"
                    element={
                      <>
                        <div className="page-heading">
                          <div>
                            <h1>Рекомендации по закупке</h1>
                            <p>
                              Проверьте предложения, скорректируйте количество и зафиксируйте
                              решение.
                            </p>
                          </div>
                          <div className="heading-actions">
                            <button className="button" onClick={() => setCalculationOpen(true)}>
                              <ArrowClockwise size={17} />
                              Пересчитать
                            </button>
                            <button
                              className="button button-primary"
                              disabled={!selected.length}
                              onClick={() => setApprovalOpen(true)}
                            >
                              <Check size={17} />
                              Утвердить{selected.length > 0 ? ` (${selected.length})` : ''}
                            </button>
                          </div>
                        </div>
                        <Metrics data={data} />
                        <div className="calculation-meta">
                          <span>
                            <span className="status-dot online" />
                            Данные на {date(data.meta.data_as_of)}
                          </span>
                          <span>Горизонт {data.meta.horizon_days} дней</span>
                          <span>Версия {data.meta.dataset_version}</span>
                          <span>
                            {data.meta.mode === 'scenario' ? 'Сценарий' : 'Операционный'} · ревизия{' '}
                            {data.meta.revision}
                          </span>
                          <span>Правила {data.meta.policy_version}</span>
                          <span>
                            Метод:{' '}
                            {[
                              ...new Set(
                                data.items.map((item) => item.forecast?.method).filter(Boolean),
                              ),
                            ].join(', ') || 'Нет позиций'}
                          </span>
                          <span>
                            Модель:{' '}
                            {[
                              ...new Set(
                                data.items.map((item) => item.forecast?.model_id).filter(Boolean),
                              ),
                            ].join(', ') || '—'}
                          </span>
                        </div>
                        <RecommendationTable
                          data={data}
                          onOpen={setItemId}
                          selected={selected}
                          onSelect={setSelected}
                        />
                        {selected.length > 0 && (
                          <div className="selection-bar">
                            <span>
                              <strong>{selected.length}</strong> позиций выбрано
                            </span>
                            <button className="text-button" onClick={() => setSelected([])}>
                              Снять выбор
                            </button>
                            <button
                              className="button button-primary"
                              onClick={() => setApprovalOpen(true)}
                            >
                              Проверить и утвердить <ArrowRight size={17} />
                            </button>
                          </div>
                        )}
                      </>
                    }
                  />
                  <Route
                    path="/data"
                    element={
                      <DataPage
                        workspace={workspace}
                        refresh={refresh}
                        onCalculate={(id) => {
                          setCalculationDataset(id)
                          setCalculationOpen(true)
                        }}
                      />
                    }
                  />
                  <Route
                    path="/approvals"
                    element={
                      <ApprovalHistory
                        approvals={workspace.approvals}
                        onDownload={download}
                        onOpenCalculation={async (id) => {
                          currentId.current = id
                          setSelected([])
                          try {
                            await refresh()
                            navigate('/recommendations')
                          } catch (e) {
                            setToast((e as Error).message)
                          }
                        }}
                      />
                    }
                  />
                  <Route
                    path="/settings"
                    element={<Settings health={health} workspace={workspace} />}
                  />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
                <footer className="page-footer">
                  <span>Tirek · Закупки с ясным обоснованием</span>
                  <span>
                    {[...new Set(data.items.map((item) => item.supplier_name))].join(' · ')}{' '}
                    <span className="footer-dot">·</span> Алматы
                  </span>
                </footer>
              </>
            )
          )}
          {!loading && !error && workspace && !data && (
            <Empty title="Пока нет расчётов">
              <p>Выберите набор данных, чтобы подготовить рекомендации.</p>
              <button className="button button-primary" onClick={() => setCalculationOpen(true)}>
                Создать расчёт
              </button>
            </Empty>
          )}
        </main>
      </div>
      {data && (
        <ItemDrawer
          calculationId={data.meta.calculation_id}
          itemId={itemId}
          onClose={() => setItemId(null)}
          onSaved={saved}
          aiAvailable={!!health?.llm_available}
          sourceKind={activeDataset?.source_kind}
          onReviewed={refresh}
        />
      )}
      {workspace && (
        <CalculationModal
          open={calculationOpen}
          close={() => setCalculationOpen(false)}
          workspace={workspace}
          initialDataset={calculationDataset || data?.meta.dataset_id || null}
          onComplete={async (id) => {
            currentId.current = id
            setSelected([])
            await refresh()
            setCalculationOpen(false)
            setCalculationDataset(null)
            navigate('/recommendations')
            setToast('Расчёт подготовлен. Рекомендации готовы к проверке.')
          }}
        />
      )}
      {data && (
        <ApprovalModal
          open={approvalOpen}
          close={() => setApprovalOpen(false)}
          data={data}
          selected={selected}
          refresh={refresh}
          onComplete={async (approval) => {
            await refresh()
            setApprovalOpen(false)
            setSelected([])
            navigate('/approvals')
            setToast('Решение утверждено. Неизменяемый снимок сохранён.')
            await download(approval.approval_id)
          }}
        />
      )}
      {toast && (
        <div className="toast" role="status">
          <CheckCircle size={20} />
          <span>{toast}</span>
          <button
            className="icon-button"
            aria-label="Скрыть сообщение"
            onClick={() => setToast('')}
          >
            <X size={16} />
          </button>
        </div>
      )}
    </div>
  )
}

function Metrics({ data }: { data: Recommendations }) {
  const forecastOnly = data.meta.issues.some((issue) => issue.code === 'FORECAST_ONLY')
  const metrics = [
    {
      label: forecastOnly ? 'Прогноз готов' : 'К закупке',
      value: String(
        data.items.filter((i) =>
          forecastOnly ? i.forecast?.mean != null : (i.final_quantity ?? 0) > 0,
        ).length,
      ),
      unit: 'позиций',
      note: 'На ближайшие 28 дней',
      icon: ShoppingCartSimple,
      tone: 'green',
    },
    {
      label: 'Критичный запас',
      value: forecastOnly ? '—' : String(data.items.filter((i) => i.urgency === 'critical').length),
      unit: 'позиции',
      note: forecastOnly ? 'Нужен актуальный остаток' : 'В первую очередь',
      icon: WarningCircle,
      tone: 'red',
    },
    {
      label: 'Требуют проверки',
      value: String(data.summary.needs_data_count + data.summary.needs_review_count),
      unit: 'позиции',
      note: 'Нужны данные или решение',
      icon: SlidersHorizontal,
      tone: 'amber',
    },
    {
      label: 'Оценка закупки',
      value: forecastOnly ? '—' : number(data.summary.known_order_cost_kzt),
      unit: '₸',
      note: forecastOnly
        ? 'Нет данных для оценки'
        : data.summary.order_cost_complete
          ? 'Все цены известны'
          : 'Частичная сумма · не все цены',
      icon: Package,
      tone: 'neutral',
    },
  ]
  return (
    <div className="metrics-strip">
      {metrics.map(({ label, value, unit, note, icon: Icon, tone }) => (
        <div className={`metric metric-${tone}`} key={label}>
          <div className="metric-label">
            {label}
            <Icon size={19} />
          </div>
          <div className="metric-value">
            {value}
            <span>{unit}</span>
          </div>
          <div className="metric-note">
            {tone !== 'neutral' && <span className="status-dot" />}
            {note}
          </div>
        </div>
      ))}
    </div>
  )
}

function Overview({
  data,
  onOpen,
  onCalculate,
  selected,
  onSelect,
}: {
  data: Recommendations
  onOpen: (id: string) => void
  onCalculate: () => void
  selected: string[]
  onSelect: (ids: string[]) => void
}) {
  const [chartId, setChartId] = useState(data.items[0]?.item_id || '')
  const [detail, setDetail] = useState<ItemDetail | null>(null)
  const [chartError, setChartError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    if (!chartId) return
    setDetail(null)
    setChartError('')
    api
      .detail(data.meta.calculation_id, chartId, controller.signal)
      .then(setDetail)
      .catch((e) => {
        if (e.name !== 'AbortError') setChartError(e.message)
      })
    return () => controller.abort()
  }, [data.meta.calculation_id, chartId])
  const exceptions = data.items
    .filter((item) => item.urgency === 'critical' || item.decision_status === 'needs_data')
    .slice(0, 3)
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="heading-greeting">Всё важное для следующей закупки</div>
          <h1>
            Запасы под контролем<span className="heading-dot">.</span>
          </h1>
          <p>Планируйте пополнение вовремя. Принимайте решения на основе данных.</p>
        </div>
        <button className="button button-primary" onClick={onCalculate}>
          <Plus size={18} />
          Новый расчёт
        </button>
      </div>
      <div className="overview-context">
        <span>
          <Buildings size={16} />
          Склад Алматы
          <CaretDown size={12} />
        </span>
        <span>{[...new Set(data.items.map((item) => item.supplier_name))].join(' · ')}</span>
        <span className="context-date">
          Данные на {date(data.meta.data_as_of)}
          <CheckCircle size={15} />
        </span>
      </div>
      <Metrics data={data} />
      {data.meta.issues.some((issue) => issue.code === 'FORECAST_ONLY') && (
        <div className="notice warning">
          <Info size={20} />
          <span>
            Показан ML-прогноз по загруженным продажам. Для расчёта заказа нужен актуальный
            свободный остаток.
          </span>
        </div>
      )}
      <div className="overview-grid">
        <section className="panel demand-panel">
          <div className="panel-heading">
            <div>
              <h2>Динамика спроса</h2>
              <p className="muted small">История продаж и горизонт следующей закупки</p>
            </div>
            <span className="subtle-label">28 дней / период</span>
          </div>
          <div className="chart-product">
            <span className="product-glyph">
              <ChartLineUp size={23} />
            </span>
            <div>
              <label htmlFor="chart-product">Товар для анализа</label>
              <select
                id="chart-product"
                value={chartId}
                onChange={(e) => setChartId(e.target.value)}
              >
                {data.items.map((item) => (
                  <option key={item.item_id} value={item.item_id}>
                    {item.sku} · {item.name.split(',')[0]}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {chartError ? (
            <ErrorState message={chartError} />
          ) : detail ? (
            <DemandChart detail={detail} />
          ) : (
            <Loading text="Загружаем историю спроса…" />
          )}
        </section>
        <section className="panel attention-panel">
          <div className="panel-heading">
            <div className="heading-with-count">
              <h2>В фокусе</h2>
              <span className="attention-count">{exceptions.length}</span>
            </div>
            <span className="attention-dot" />
          </div>
          <p className="attention-intro">Позиции, с которых стоит начать</p>
          <div className="attention-items">
            {exceptions.map((item) => (
              <button
                className="attention-item"
                key={item.item_id}
                onClick={() => onOpen(item.item_id)}
              >
                <span
                  className={`attention-icon ${item.decision_status === 'needs_data' ? 'amber' : ''}`}
                >
                  <WarningCircle size={19} />
                </span>
                <div>
                  <strong>
                    {item.decision_status === 'needs_data'
                      ? 'Не хватает данных'
                      : 'Критичный запас'}
                  </strong>
                  <span>
                    {item.sku} · {item.name.split(',')[0]}
                  </span>
                  <small>
                    {item.decision_status === 'needs_data'
                      ? 'Нужен актуальный остаток'
                      : `Остаток ${number(item.free_stock)} шт. · к закупке ${number(item.final_quantity)} шт.`}
                  </small>
                </div>
                <ArrowUpRight size={16} />
              </button>
            ))}
          </div>
          <Link className="attention-footer" to="/recommendations?status=review">
            Открыть очередь проверки
            <ArrowRight size={17} />
          </Link>
        </section>
      </div>
      <RecommendationTable
        data={data}
        onOpen={onOpen}
        selected={selected}
        onSelect={onSelect}
        compact
      />
      <div className="workflow-note">
        <SealCheck size={22} />
        <span>
          <strong>Последнее слово за вами.</strong> Любую рекомендацию можно проверить и
          скорректировать перед утверждением.
        </span>
        <Link to="/recommendations">
          К решениям <ArrowRight size={16} />
        </Link>
      </div>
    </>
  )
}

function CalculationModal({
  open,
  close,
  workspace,
  initialDataset,
  onComplete,
}: {
  open: boolean
  close: () => void
  workspace: Workspace
  initialDataset: string | null
  onComplete: (id: string) => Promise<void>
}) {
  const [dataset, setDataset] = useState('demo-systeme-v1')
  const [category, setCategory] = useState('')
  const [budget, setBudget] = useState('')
  const [asOf, setAsOf] = useState('2026-09-22')
  const [mode, setMode] = useState<CalculationRequest['mode']>('scenario')
  const [leadTime, setLeadTime] = useState(7)
  const [policies, setPolicies] = useState('[]')
  const [economics, setEconomics] = useState('[]')
  const [growth, setGrowth] = useState('[]')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [job, setJob] = useState<Job | null>(null)
  const controller = useRef<AbortController | null>(null)
  const operation = useRef<{ body: string; key: string } | null>(null)
  const source = workspace.datasets.find((value) => value.dataset_id === dataset)
  const isDemo = source?.source_kind === 'synthetic'
  const forecastOnly = source?.supplier_ids.includes('iek')
  const available = !!source?.calculation_allowed && (isDemo || workspace.capabilities.ml_connected)
  function selectDataset(id: string) {
    const next = workspace.datasets.find((value) => value.dataset_id === id)
    setDataset(id)
    setAsOf(next?.data_as_of || '')
    setCategory('')
    setMode('scenario')
    setLeadTime(7)
    setPolicies('[]')
    setEconomics('[]')
    setGrowth('[]')
    setError('')
  }
  useEffect(() => () => controller.current?.abort(), [])
  useEffect(() => {
    if (open) {
      setError('')
      setJob(null)
      if (initialDataset) selectDataset(initialDataset)
    }
  }, [open, initialDataset])
  async function run() {
    if (busy || !available) return
    if (!asOf || (budget !== '' && (!Number.isFinite(Number(budget)) || Number(budget) <= 0))) {
      setError('Укажите дату и положительный бюджет либо оставьте бюджет пустым.')
      return
    }
    let parsedPolicies: CalculationRequest['category_policies'] = []
    let parsedEconomics: CalculationRequest['economic_profiles'] = []
    let parsedGrowth: CalculationRequest['growth_adjustments'] = []
    if (!isDemo) {
      try {
        parsedPolicies = JSON.parse(policies)
        parsedEconomics = JSON.parse(economics)
        parsedGrowth = JSON.parse(growth)
        if (![parsedPolicies, parsedEconomics, parsedGrowth].every(Array.isArray)) throw new Error()
      } catch {
        setError('Политики, экономика и прирост должны быть JSON-массивами. Пустое значение: [].')
        return
      }
    }
    const payload: CalculationRequest = {
      dataset_id: dataset,
      as_of_date: asOf,
      warehouse_ids: ['almaty'],
      category_codes: category
        .split(',')
        .map((value) => value.trim())
        .filter(Boolean),
      horizon_days: 28,
      lead_time_days: isDemo ? 7 : leadTime,
      review_period_days: isDemo ? 21 : 28 - leadTime,
      mode: isDemo ? 'scenario' : mode,
      category_policies: parsedPolicies,
      economic_profiles: parsedEconomics,
      growth_adjustments: parsedGrowth,
      budget_kzt: budget ? Number(budget) : null,
      request_ai_review: false,
    }
    const body = JSON.stringify(payload)
    if (operation.current?.body !== body) operation.current = { body, key: crypto.randomUUID() }
    setBusy(true)
    setError('')
    controller.current = new AbortController()
    try {
      const initial = await api.calculate(payload, operation.current!.key)
      const result = await api.wait(initial, setJob, controller.current.signal)
      await onComplete(result.resource_id!)
      operation.current = null
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        setError((e as Error).message)
        if (e instanceof ApiError && e.status > 0) operation.current = null
      }
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal
      open={open}
      onClose={() => {
        if (!busy) close()
      }}
      title="Новый расчёт"
      description="Выберите исходные данные и рамки сценария."
    >
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void run()
        }}
      >
        <label className="field">
          Набор данных
          <select value={dataset} onChange={(e) => selectDataset(e.target.value)} disabled={busy}>
            {workspace.datasets.map((d) => (
              <option key={d.dataset_id} value={d.dataset_id} disabled={!d.calculation_allowed}>
                {d.source_kind === 'synthetic'
                  ? `Systeme Electric · демо, ${d.sku_count} позиций`
                  : `Загрузка ${d.dataset_version} · ${d.calculation_allowed ? `${d.sku_count} позиций` : 'требуется нормализация'}`}
              </option>
            ))}
          </select>
        </label>
        <div className="form-grid">
          <label className="field">
            Склад
            <select disabled>
              <option>Алматы · основной</option>
            </select>
          </label>
          <label className="field">
            Категория
            {isDemo ? (
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                disabled={busy}
              >
                <option value="">Все категории</option>
                {[
                  'Автоматы',
                  'Электроустановка',
                  'Дифференциальная защита',
                  'Управление',
                  'Корпуса',
                ].map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            ) : (
              <input
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                disabled={busy || forecastOnly}
                placeholder={
                  forecastOnly
                    ? 'Для IEK категории не подтверждены'
                    : 'Коды через запятую; пусто — все'
                }
              />
            )}
          </label>
          <label className="field">
            Дата расчёта
            <input
              type="date"
              value={asOf}
              min={source?.data_as_of}
              onChange={(e) => setAsOf(e.target.value)}
              disabled={busy}
              required
            />
          </label>
          <label className="field">
            Горизонт
            <select disabled>
              <option>28 дней</option>
            </select>
          </label>
        </div>
        <div className="horizon-breakdown">
          <span>
            Поставка <strong>{isDemo ? 7 : leadTime} дней</strong>
          </span>
          <Plus size={14} />
          <span>
            Пересмотр <strong>{isDemo ? 21 : 28 - leadTime} дней</strong>
          </span>
          <ArrowRight size={15} />
          <strong>28 дней</strong>
        </div>
        {!isDemo && (
          <>
            <div className="form-grid">
              <label className="field">
                Режим расчёта
                <select
                  value={mode}
                  onChange={(e) => setMode(e.target.value as CalculationRequest['mode'])}
                  disabled={busy}
                >
                  <option value="scenario">Сценарный</option>
                  <option value="operational" disabled={source?.source_kind !== 'observed'}>
                    Операционный
                  </option>
                </select>
              </label>
              <label className="field">
                Срок поставки, дней
                <input
                  type="number"
                  min="0"
                  max="27"
                  step="1"
                  required
                  value={leadTime}
                  onChange={(e) => setLeadTime(Number(e.target.value))}
                  disabled={busy}
                />
              </label>
            </div>
            {!forecastOnly && (
              <>
                <label className="field">
                  Политики категорий · JSON
                  <textarea
                    rows={4}
                    value={policies}
                    onChange={(e) => setPolicies(e.target.value)}
                    disabled={busy}
                    spellCheck={false}
                  />
                  <span className="field-hint">
                    Укажите исходный код категории, квантиль и основание. Без политики или экономики
                    количество останется неизвестным.
                  </span>
                </label>
                <details className="context-details">
                  <summary>Формат политики и дополнительные параметры</summary>
                  <p className="field-hint">
                    Пример формата; замените код, значения и основание своими:
                  </p>
                  <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
                    {JSON.stringify(
                      [
                        {
                          category_raw: 'КОД',
                          target_quantile: 0.9,
                          minimum_target_quantile: null,
                          source_kind: 'manual',
                          rationale: 'Основание выбранной политики',
                        },
                      ],
                      null,
                      2,
                    )}
                  </pre>
                  <label className="field">
                    Экономические профили · JSON
                    <textarea
                      rows={4}
                      value={economics}
                      onChange={(e) => setEconomics(e.target.value)}
                      disabled={busy}
                      spellCheck={false}
                    />
                    <span className="field-hint">
                      Для каждого SKU: underage_cost, overage_cost, unit_cost, currency (KZT),
                      horizon_days (28), source_kind и rationale.
                    </span>
                  </label>
                  <label className="field">
                    Прогноз прироста · JSON
                    <textarea
                      rows={4}
                      value={growth}
                      onChange={(e) => setGrowth(e.target.value)}
                      disabled={busy}
                      spellCheck={false}
                    />
                    <span className="field-hint">
                      Для каждого SKU: rate, valid_from, valid_to, source_kind и rationale.
                      Необязательно; без прироста оставьте [].
                    </span>
                  </label>
                </details>
              </>
            )}
          </>
        )}
        <label className="field">
          Бюджет закупки, ₸ <span className="field-optional">необязательно</span>
          <input
            type="number"
            min="0.01"
            step="0.01"
            placeholder="Без ограничения"
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
            disabled={busy}
          />
          <span className="field-hint">Если бюджет задан, позиции без цены нельзя утвердить.</span>
        </label>
        <div className="notice">
          <Info size={19} />
          <span>
            {isDemo
              ? 'Сценарный режим: используются готовые синтетические профили. Это демонстрация платформы, не результат ML-прогноза.'
              : forecastOnly
                ? 'Будет построен ML-прогноз IEK на 28 дней по загруженным продажам. Количество закупки недоступно без актуального свободного остатка.'
                : 'Расчёт использует загруженные источники и выбранный прогнозный метод. Недостаточные данные и неизвестные условия поставки будут отмечены в рекомендациях.'}
          </span>
        </div>
        {!available && (
          <p className="inline-error" role="alert">
            {workspace.capabilities.ml_error ||
              'Набор ещё не готов к расчёту. Проверьте отчёт импорта.'}
          </p>
        )}
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        {busy && (
          <div className="job-progress">
            <div>
              <span>{job?.stage || 'Создаём задачу…'}</span>
              <span>{job?.progress_pct ?? 0}%</span>
            </div>
            <progress value={job?.progress_pct ?? 0} max="100" />
          </div>
        )}
        <div className="dialog-actions">
          <button type="button" className="button" onClick={close} disabled={busy}>
            Отмена
          </button>
          <button className="button button-primary" disabled={busy || !available}>
            {busy ? <CircleNotch size={18} className="spin" /> : <ChartLineUp size={18} />}
            Подготовить рекомендации
          </button>
        </div>
      </form>
    </Modal>
  )
}

function ApprovalModal({
  open,
  close,
  data,
  selected,
  refresh,
  onComplete,
}: {
  open: boolean
  close: () => void
  data: Recommendations
  selected: string[]
  refresh: () => Promise<void>
  onComplete: (approval: Approval) => Promise<void>
}) {
  const [acknowledged, setAcknowledged] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const operation = useRef<{ body: string; key: string } | null>(null)
  const items = data.items.filter((i) => selected.includes(i.item_id))
  const issueCodes = [
    ...new Set(
      items
        .filter((i) => i.decision_status === 'needs_review')
        .flatMap((i) => i.issues.map((issue) => issue.code)),
    ),
  ]
  useEffect(() => {
    if (open) {
      setError('')
      setAcknowledged(false)
      operation.current = null
    }
  }, [open])
  async function approve() {
    setBusy(true)
    setError('')
    const payload = {
      expected_revision: data.meta.revision,
      selected_item_ids: selected,
      acknowledged_issue_codes: acknowledged ? issueCodes : [],
    }
    const body = JSON.stringify(payload)
    if (operation.current?.body !== body) operation.current = { body, key: crypto.randomUUID() }
    try {
      await onComplete(await api.approve(data.meta.calculation_id, payload, operation.current!.key))
    } catch (e) {
      setError((e as Error).message)
      if (e instanceof ApiError && e.code === 'STALE_REVISION') await refresh().catch(() => {})
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal
      open={open}
      onClose={() => {
        if (!busy) close()
      }}
      title="Зафиксировать решение"
      description={`${data.meta.mode === 'scenario' ? 'Сценарный' : 'Операционный'} снимок · ревизия ${data.meta.revision}`}
    >
      <div className="approval-intro">
        <span className="approval-icon">
          <SealCheck size={30} />
        </span>
        <p>
          Сохраним выбранные позиции и ваши корректировки. Последующие изменения не затронут этот
          снимок.
        </p>
      </div>
      <div className="approval-list">
        {items.map((item) => (
          <div key={item.item_id}>
            <span>
              <strong>{item.sku}</strong>
              <small>{item.name.split(',')[0]}</small>
            </span>
            <strong>
              {number(item.final_quantity)} {item.unit}
            </strong>
          </div>
        ))}
      </div>
      {issueCodes.length > 0 && (
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(e) => setAcknowledged(e.target.checked)}
          />
          <span>
            Я проверил предупреждения выбранных позиций:{' '}
            {items
              .filter((i) => i.decision_status === 'needs_review')
              .map((i) => i.sku)
              .join(', ')}
            .
          </span>
        </label>
      )}
      <div className="notice warning">
        <Info size={18} />
        <span>Будет сохранён утверждённый снимок выбранных позиций.</span>
      </div>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      <div className="dialog-actions">
        <button className="button" disabled={busy} onClick={close}>
          Вернуться
        </button>
        <button
          className="button button-primary"
          disabled={busy || !selected.length || (issueCodes.length > 0 && !acknowledged)}
          onClick={() => void approve()}
        >
          {busy ? <CircleNotch size={17} className="spin" /> : <DownloadSimple size={17} />}
          Утвердить и скачать CSV
        </button>
      </div>
    </Modal>
  )
}

function ApprovalHistory({
  approvals,
  onDownload,
  onOpenCalculation,
}: {
  approvals: Approval[]
  onDownload: (id: string) => Promise<void>
  onOpenCalculation: (id: string) => Promise<void>
}) {
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>История решений</h1>
          <p>Утверждённые снимки сохраняют состав заказа и все корректировки на момент решения.</p>
        </div>
        <Badge>{approvals.length} снимков</Badge>
      </div>
      <section className="panel">
        {approvals.length === 0 ? (
          <Empty title="Здесь появятся ваши решения">
            <p>
              Выберите позиции в рекомендациях и утвердите сценарий.
              <br />
              После этого вы сможете скачать его в CSV в любой момент.
            </p>
            <Link className="button button-primary" to="/recommendations">
              Перейти к рекомендациям <ArrowRight size={17} />
            </Link>
          </Empty>
        ) : (
          <div className="table-scroll">
            <table className="history-table">
              <thead>
                <tr>
                  <th>Снимок</th>
                  <th>Дата утверждения</th>
                  <th>Позиций</th>
                  <th>Режим</th>
                  <th>Действия</th>
                </tr>
              </thead>
              <tbody>
                {approvals.map((approval) => (
                  <tr key={approval.approval_id}>
                    <td>
                      <strong>Решение #{approval.approval_id.slice(0, 6).toUpperCase()}</strong>
                      <small>Ревизия {approval.revision} · сохранённый снимок</small>
                    </td>
                    <td>
                      {date(approval.approved_at)}
                      <small>
                        {new Date(approval.approved_at).toLocaleTimeString('ru-RU', {
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </small>
                    </td>
                    <td>{approval.selected_item_ids.length}</td>
                    <td>
                      <Badge tone={approval.mode === 'scenario' ? 'scenario' : 'normal'}>
                        {approval.mode === 'scenario' ? 'Сценарий' : 'Операционный'}
                      </Badge>
                    </td>
                    <td>
                      <div className="history-actions">
                        <button
                          className="button"
                          onClick={() => void onDownload(approval.approval_id)}
                        >
                          <DownloadSimple size={17} />
                          CSV
                        </button>
                        <button
                          className="icon-button"
                          title="Открыть текущую ревизию исходного расчёта"
                          aria-label="Открыть исходный расчёт"
                          onClick={() => void onOpenCalculation(approval.calculation_id)}
                        >
                          <ArrowUpRight size={19} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <div className="notice">
        <Info size={18} />
        <span>
          CSV формируется из утверждённого снимка. Открытие исходного расчёта показывает его текущую
          ревизию.
        </span>
      </div>
    </>
  )
}

function Settings({ health, workspace }: { health: Health | null; workspace: Workspace }) {
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Настройки пространства</h1>
          <p>Режим работы, доступные возможности и подключение расчётного модуля.</p>
        </div>
      </div>
      <div className="settings-grid">
        <section className="panel settings-panel">
          <div className="panel-heading">
            <h2>Рабочее пространство</h2>
            <Buildings size={20} />
          </div>
          <div className="settings-row">
            <span>Название</span>
            <strong>Tirek workspace</strong>
          </div>
          <div className="settings-row">
            <span>Склад</span>
            <strong>Алматы</strong>
          </div>
          <div className="settings-row">
            <span>Валюта</span>
            <strong>KZT · тенге</strong>
          </div>
          <div className="settings-row">
            <span>Часовой пояс данных</span>
            <strong>Asia/Almaty</strong>
          </div>
          <div className="settings-row">
            <span>Доступ</span>
            <Badge>Локальный, один пользователь</Badge>
          </div>
        </section>
        <section className="panel settings-panel">
          <div className="panel-heading">
            <h2>Состояние системы</h2>
            <span className="status-dot online" />
          </div>
          <div className="settings-row">
            <span>Сервер платформы</span>
            <Badge tone="normal">{health ? 'Подключён' : 'Недоступен'}</Badge>
          </div>
          <div className="settings-row">
            <span>Хранилище</span>
            <strong>SQLite · сохраняется на диске</strong>
          </div>
          <div className="settings-row">
            <span>ML-модуль</span>
            <Badge tone={workspace.capabilities.ml_connected ? 'normal' : 'scenario'}>
              {workspace.capabilities.ml_connected ? 'Адаптер настроен' : 'Ожидает подключения'}
            </Badge>
          </div>
          <div className="settings-row">
            <span>Проверка ИИ</span>
            <Badge tone={health?.llm_available ? 'normal' : 'scenario'}>
              {health?.llm_available ? 'Провайдер настроен' : 'Требуется настройка провайдера'}
            </Badge>
          </div>
          <div className="settings-row">
            <span>Горизонт расчёта</span>
            <strong>{health?.supported_horizons.join(', ')} дней</strong>
          </div>
        </section>
      </div>
      <section className="panel explanation-panel">
        <span className="explanation-icon">
          <Sparkle size={30} weight="light" />
        </span>
        <div>
          <h2>Готово к следующему этапу</h2>
          <p>
            Сейчас вы можете пройти весь сценарий закупщика на демонстрационных данных: изучить
            рекомендацию, изменить количество, утвердить решение и выгрузить заказ.
          </p>
          <p>
            {workspace.capabilities.ml_connected
              ? 'Доступны расчёт SE и прогноз продаж IEK. Для ИИ-проверки задайте TIREK_LLM_BASE_URL, TIREK_LLM_MODEL и TIREK_LLM_API_KEY в локальном backend/.env и перезапустите сервер. Ключ хранится только на сервере.'
              : workspace.capabilities.ml_error ||
                'Для расчёта по загруженным файлам требуется настроить адаптер модели.'}
          </p>
          <Link className="text-button" to="/data">
            Посмотреть источники <ArrowRight size={17} />
          </Link>
        </div>
      </section>
    </>
  )
}
