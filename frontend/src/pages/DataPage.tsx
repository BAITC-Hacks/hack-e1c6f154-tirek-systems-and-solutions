import { useEffect, useRef, useState } from 'react'
import {
  ArrowRight,
  CheckCircle,
  CloudArrowUp,
  DownloadSimple,
  FileXls,
  FolderOpen,
  Info,
  X,
} from '@phosphor-icons/react'
import { api, ApiError, type Dataset, type Job, type Workspace } from '../api/client'
import { Badge, date, Loading, number } from '../components/ui'

const roleLabels: Record<string, string> = {
  sales_transactions: 'Динамика продаж',
  sales_monthly: 'Помесячные продажи',
  stock_monthly: 'История остатков',
  seasonality: 'Сезонность',
  moq: 'Минимальные партии',
  current_stock_inbound: 'Остаток и товар в пути',
  additional_context: 'Дополнительный контекст',
}
const sourceNames: Record<string, string> = {
  sales_transactions: 'Динамика…xlsx',
  sales_monthly: 'Ежемесячные продажи…xlsx',
  stock_monthly: 'Ежемесячные остатки…xlsx',
  seasonality: 'Сезонность…xlsx',
  moq: 'MOQ…xlsx',
  current_stock_inbound: 'Товар в пути на ДД.ММ.ГГГГ.xlsx',
}

export default function DataPage({
  workspace,
  refresh,
  onCalculate,
}: {
  workspace: Workspace
  refresh: () => Promise<void>
  onCalculate: (datasetId: string) => void
}) {
  const [files, setFiles] = useState<File[]>([])
  const [supplier, setSupplier] = useState('systeme-electric')
  const [context, setContext] = useState('')
  const [error, setError] = useState('')
  const [job, setJob] = useState<Job | null>(null)
  const [busy, setBusy] = useState(false)
  const [sampleBusy, setSampleBusy] = useState(false)
  const [contextBusy, setContextBusy] = useState(false)
  const [contextFilename, setContextFilename] = useState('')
  const [dragging, setDragging] = useState(false)
  const [reportId, setReportId] = useState(workspace.datasets[0]?.dataset_id)
  const controller = useRef<AbortController | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const contextInput = useRef<HTMLInputElement>(null)
  const key = useRef(crypto.randomUUID())
  useEffect(() => () => controller.current?.abort(), [])
  const report = workspace.datasets.find((d) => d.dataset_id === reportId) || workspace.datasets[0]
  function addFiles(incoming: File[]) {
    const merged = [...files, ...incoming].filter(
      (file, index, all) => all.findIndex((f) => f.name === file.name) === index,
    )
    if (merged.length > 6) {
      setError('Выберите не более шести файлов.')
      return
    }
    if (merged.some((file) => !file.name.toLowerCase().endsWith('.xlsx'))) {
      setError('Поддерживаются только файлы XLSX.')
      return
    }
    if (merged.reduce((sum, f) => sum + f.size, 0) > 30 * 1024 * 1024) {
      setError('Общий размер файлов должен быть не больше 30 МБ.')
      return
    }
    setFiles(merged)
    setError('')
    key.current = crypto.randomUUID()
  }
  async function upload() {
    if (!files.length || busy || contextBusy) return
    setBusy(true)
    setError('')
    controller.current = new AbortController()
    try {
      const initial = await api.upload(files, context, key.current, supplier)
      const result = await api.wait(initial, setJob, controller.current.signal)
      await refresh()
      setReportId(result.resource_id!)
      setFiles([])
      setContext('')
      setContextFilename('')
      key.current = crypto.randomUUID()
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setError((e as Error).message)
      if (e instanceof ApiError && e.status > 0) key.current = crypto.randomUUID()
    } finally {
      setBusy(false)
    }
  }
  async function loadContext(file: File) {
    if (busy || contextBusy) return
    setError('')
    if (!file.name.toLowerCase().endsWith('.json') || file.size >= 2 * 1024 * 1024) {
      setError('Выберите файл JSON размером меньше 2 МБ.')
      return
    }
    setContextBusy(true)
    try {
      const content = (await file.text()).replace(/^\uFEFF/, '')
      const parsed: unknown = JSON.parse(content)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error(
          'Контекст должен быть JSON-объектом, а не массивом или отдельным значением.',
        )
      }
      setContext(content)
      setContextFilename(file.name)
      key.current = crypto.randomUUID()
    } catch (cause) {
      setError(
        cause instanceof SyntaxError
          ? 'В файле некорректный JSON. Исправьте его и выберите снова.'
          : (cause as Error).message,
      )
    } finally {
      setContextBusy(false)
    }
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Источники данных</h1>
          <p>Все исходные данные в одном месте. Каждая загрузка сохраняет свою версию.</p>
        </div>
        <Badge tone="normal">Systeme Electric · IEK</Badge>
      </div>
      <div className="data-layout">
        <section className="panel upload-panel">
          <div className="panel-heading">
            <h2>Загрузить новый набор</h2>
            <span className="muted small">XLSX · до 30 МБ</span>
          </div>
          <label className="field">
            Поставщик файлов
            <select
              value={supplier}
              disabled={busy || sampleBusy}
              onChange={(e) => {
                setSupplier(e.target.value)
                key.current = crypto.randomUUID()
              }}
            >
              <option value="systeme-electric">Systeme Electric</option>
              <option value="iek">IEK</option>
            </select>
          </label>
          <div
            className={`dropzone ${dragging ? 'dragging' : ''}`}
            onDragOver={(e) => {
              e.preventDefault()
              if (!busy) setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragging(false)
              if (!busy) addFiles(Array.from(e.dataTransfer.files))
            }}
          >
            <div className="upload-symbol">
              <CloudArrowUp size={34} weight="light" />
            </div>
            <h3>Перетащите файлы сюда</h3>
            <p>
              {supplier === 'iek'
                ? 'Продажи IEK и актуальный складской снимок.'
                : 'Шесть источников Systeme Electric.'}
              <br />
              Исходные файлы останутся без изменений.
            </p>
            <button className="button" disabled={busy} onClick={() => input.current?.click()}>
              <FolderOpen size={18} />
              Выбрать файлы
            </button>
            <input
              ref={input}
              type="file"
              aria-label="Файлы XLSX"
              accept=".xlsx"
              multiple
              hidden
              onChange={(e) => {
                addFiles(Array.from(e.target.files || []))
                e.target.value = ''
              }}
            />
          </div>
          {files.length > 0 && (
            <div className="upload-files">
              {files.map((file) => (
                <div className="upload-file" key={file.name}>
                  <FileXls size={22} />
                  <span>
                    {file.name}
                    <small>{number(file.size / 1024)} КБ</small>
                  </span>
                  <button
                    className="icon-button"
                    disabled={busy}
                    aria-label={`Удалить ${file.name}`}
                    onClick={() => {
                      setFiles(files.filter((f) => f.name !== file.name))
                      key.current = crypto.randomUUID()
                    }}
                  >
                    <X size={17} />
                  </button>
                </div>
              ))}
            </div>
          )}
          <details className="context-details">
            <summary>Дополнительный контекст · необязательно</summary>
            <button
              className="button"
              type="button"
              disabled={busy || contextBusy}
              onClick={() => contextInput.current?.click()}
            >
              <FolderOpen size={17} />
              {contextBusy ? 'Читаем файл…' : 'Выбрать JSON контекста'}
            </button>
            <input
              ref={contextInput}
              type="file"
              accept=".json,application/json"
              aria-label="Файл дополнительного контекста"
              hidden
              disabled={busy || contextBusy}
              onChange={(event) => {
                const file = event.target.files?.[0]
                if (file) void loadContext(file)
                event.target.value = ''
              }}
            />
            <p className="field-hint" role="status">
              {contextFilename ? `Прочитан ${contextFilename}. ` : ''}Файл останется локальным до
              нажатия «Проверить и загрузить». В учебном архиве выберите
              additional-context.SYNTHETIC.json.
            </p>
            <label className="field">
              JSON с ценами, наличием и обязательствами
              <textarea
                rows={5}
                value={context}
                disabled={busy || contextBusy}
                onChange={(e) => {
                  setContext(e.target.value)
                  setContextFilename('')
                  key.current = crypto.randomUUID()
                }}
                placeholder='{"client_labels": [], ...}'
              />
              <span className="field-hint">
                Формат AdditionalContext из API-контракта. Не передавайте персональные данные
                клиентов.
              </span>
            </label>
          </details>
          {error && (
            <p className="inline-error" role="alert">
              {error}
            </p>
          )}
          {busy && <Loading text={job?.stage || 'Загружаем файлы…'} />}
          <div className="upload-actions">
            <span>{files.length} из 6 файлов</span>
            <button
              className="button button-primary"
              disabled={!files.length || busy || contextBusy}
              onClick={() => void upload()}
            >
              Проверить и загрузить <ArrowRight size={17} />
            </button>
          </div>
        </section>
        <aside className="data-help">
          <div className="notice">
            <Info size={20} />
            <div>
              <strong>Попробуйте настоящий ML на учебных файлах</strong>
              <p>
                Синтетические XLSX и контекст для {supplier === 'iek' ? 'IEK' : 'Systeme Electric'}.
                Скачайте ZIP, распакуйте, выберите XLSX и добавьте additional-context.SYNTHETIC.json
                в дополнительный контекст. Эти файлы проходят импорт и запускают модель.
              </p>
              <button
                className="button"
                type="button"
                disabled={sampleBusy || busy}
                onClick={async () => {
                  setSampleBusy(true)
                  setError('')
                  try {
                    await api.downloadSample(supplier)
                  } catch (cause) {
                    setError((cause as Error).message)
                  } finally {
                    setSampleBusy(false)
                  }
                }}
              >
                <DownloadSimple size={17} />
                {sampleBusy ? 'Готовим архив…' : 'Скачать учебный набор'}
              </button>
            </div>
          </div>
          <h2>Что нужно для расчёта</h2>
          <p>Названия помогают определить роль каждого источника.</p>
          {Object.entries(roleLabels)
            .filter(([key]) => key !== 'additional_context')
            .map(([role, label], index) => (
              <div className="source-requirement" key={role}>
                <span>{index + 1}</span>
                <div>
                  <strong>{label}</strong>
                  <small>
                    {supplier === 'iek' && role === 'current_stock_inbound'
                      ? 'Путь ИЭК ДД.ММ.ГГГГ.xlsx'
                      : sourceNames[role]}
                  </small>
                </div>
              </div>
            ))}
          <div className="notice">
            <Info size={20} />
            <p>
              {supplier === 'iek'
                ? 'IEK: модель строит прогноз по динамике и помесячным продажам. Для заказа добавьте актуальный свободный остаток и условия партии в стандартном формате; без них будет доступен только прогноз продаж.'
                : 'Сохраните оригинальные имена и структуру выгрузок SE. Для расчёта нужны все шесть ролей. Дату складского снимка берём из имени файла.'}
            </p>
          </div>
        </aside>
      </div>
      <section className="panel dataset-panel">
        <div className="panel-heading">
          <div>
            <h2>Отчёт о качестве</h2>
            <p className="muted small">Проверяем происхождение данных до запуска расчёта</p>
          </div>
          <label className="sr-only" htmlFor="dataset-choice">
            Версия набора
          </label>
          <select
            id="dataset-choice"
            value={report?.dataset_id || ''}
            disabled={busy}
            onChange={(e) => setReportId(e.target.value)}
          >
            {workspace.datasets.map((d) => (
              <option key={d.dataset_id} value={d.dataset_id}>
                {d.dataset_id === 'demo-systeme-v1'
                  ? 'Демонстрационный набор'
                  : `${d.source_kind === 'synthetic' ? 'Учебные XLSX' : 'Загрузка'} ${d.dataset_version}`}
              </option>
            ))}
          </select>
        </div>
        {report && <DatasetReport report={report} />}
        {report?.calculation_allowed && (
          <div className="panel-bottom">
            <span className="muted">
              {report.dataset_id === 'demo-systeme-v1'
                ? 'Доступен сценарий на синтетических данных'
                : 'Набор нормализован и доступен для расчёта'}
            </span>
            <button
              className="button button-primary"
              disabled={busy || contextBusy}
              onClick={() => onCalculate(report.dataset_id)}
            >
              Перейти к расчёту <ArrowRight size={17} />
            </button>
          </div>
        )}
      </section>
    </>
  )
}

function DatasetReport({ report }: { report: Dataset }) {
  return (
    <>
      <div className="dataset-summary">
        <div>
          <span>
            Дата{' '}
            {report.issues.some((issue) => issue.code === 'SOURCE_DATE_UNKNOWN')
              ? 'загрузки'
              : 'данных'}
          </span>
          <strong>{date(report.data_as_of)}</strong>
        </div>
        <div>
          <span>Уникальных позиций</span>
          <strong>{report.sku_count || 'Не определено'}</strong>
        </div>
        <div>
          <span>Источников</span>
          <strong>{report.sources.length} / 6</strong>
        </div>
        <div>
          <span>Происхождение</span>
          <Badge tone={report.source_kind === 'synthetic' ? 'scenario' : 'normal'}>
            {report.source_kind === 'synthetic' ? 'Синтетический набор' : 'Загруженные файлы'}
          </Badge>
        </div>
      </div>
      <div className="table-scroll">
        <table className="source-table">
          <thead>
            <tr>
              <th>Источник</th>
              <th>Файл</th>
              <th>Прочитано строк</th>
              <th>Использовано</th>
              <th>Состояние</th>
            </tr>
          </thead>
          <tbody>
            {report.sources.map((source) => (
              <tr key={source.role}>
                <td>
                  <FileXls size={19} />
                  {roleLabels[source.role]}
                </td>
                <td className="muted">{source.filename}</td>
                <td>{number(source.rows_read)}</td>
                <td>{number(source.rows_used)}</td>
                <td>
                  <span className="check-status">
                    <CheckCircle size={17} />
                    {report.dataset_id === 'demo-systeme-v1' ? 'Пример' : 'Прочитан'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="dataset-issues">
        {report.issues.map((issue, i) => (
          <div
            className={`notice ${issue.severity === 'error' ? 'warning' : ''}`}
            key={`${issue.code}-${i}`}
          >
            <Info size={18} />
            <span>{issue.message}</span>
          </div>
        ))}
      </div>
    </>
  )
}
