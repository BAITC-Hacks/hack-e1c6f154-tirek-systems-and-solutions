import type { components } from './schema'

export type Item = components['schemas']['Recommendation']
export type ItemDetail = components['schemas']['ItemDetail']
export type Recommendations = components['schemas']['RecommendationsResponse']
export type Dataset = components['schemas']['DatasetReport']
export type Approval = components['schemas']['Approval']
export type Job = components['schemas']['Job']
export type CalculationRequest = components['schemas']['CalculationRequest']
export type Health = components['schemas']['Health']
export type Workspace = {
  latest_calculation_id: string | null
  datasets: Dataset[]
  approvals: Approval[]
  calculations: Recommendations['meta'][]
  capabilities: {
    demo: boolean
    ml_connected: boolean
    real_import: string
    ml_error?: string | null
  }
}
export type StockLevel = 'normal' | 'warning' | 'high' | 'critical' | 'stockout' | 'unknown'
export type StockState = {
  sku: string
  warehouse_id: string
  unit: string
  mode: 'operational' | 'scenario'
  revision: number
  on_hand: number | null
  reserved: number | null
  free_stock: number | null
  remaining_pct: number | null
  observed_level: StockLevel
  active_level: StockLevel
  evaluated_at: string
  issues: string[]
  policy: {
    reference_stock: number | null
    reference_kind: 'manual' | 'calculation' | 'unavailable'
    reference_id: string | null
    source_kind: 'observed' | 'manual' | 'synthetic'
    policy_version: string
    thresholds: { warning_pct: number; high_pct: number; critical_pct: number; hysteresis_pp: number }
  }
}
export type StockEventResult = {
  state: StockState
  transition: null | { from_level: StockLevel; to_level: StockLevel; kind: string; created_at: string }
  deduplicated: boolean
  recalculation_requested: boolean
}

const base = (import.meta.env.VITE_API_URL || '/api/v1').replace(/\/$/, '')
export class ApiError extends Error {
  constructor(
    message: string,
    public code: string,
    public status: number,
  ) {
    super(message)
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(base + path, options)
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(
      'Нет связи с сервером. Проверьте, что бэкенд запущен, и повторите попытку.',
      'NETWORK',
      0,
    )
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new ApiError(
      [
        body?.error?.message || body?.message || 'Сервер не смог выполнить запрос.',
        ...(Array.isArray(body?.error?.details)
          ? body.error.details.map(
              (detail: { field?: string; message?: string }) =>
                `${detail.field || 'Параметры'}: ${detail.message || 'некорректное значение'}`,
            )
          : []),
      ].join(' '),
      body?.error?.code || body?.code || 'HTTP_ERROR',
      response.status,
    )
  }
  const contentType = response.headers.get('content-type') || ''
  if (!/^application\/(?:[\w.-]+\+)?json(?:\s*;|$)/i.test(contentType)) {
    throw new ApiError(
      'Сервер вернул неверный формат данных. Проверьте подключение к API и перезапустите проект.',
      'INVALID_RESPONSE',
      response.status,
    )
  }
  try {
    return (await response.json()) as T
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(
      'Не удалось прочитать данные сервера. Повторите запрос.',
      'INVALID_JSON',
      response.status,
    )
  }
}

function json(body: unknown, method = 'POST', key?: string): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json', ...(key ? { 'Idempotency-Key': key } : {}) },
    body: JSON.stringify(body),
  }
}

export const api = {
  workspace: () => request<Workspace>('/workspace'),
  health: () => request<Health>('/health'),
  recommendations: (id: string) => request<Recommendations>(`/calculations/${id}/recommendations`),
  detail: (id: string, item: string, signal?: AbortSignal) =>
    request<ItemDetail>(`/calculations/${id}/items/${item}`, { signal }),
  review: (id: string, item: string, revision: number) =>
    request<ItemDetail>(
      `/calculations/${id}/items/${item}/ai-review`,
      json({ expected_revision: revision }),
    ),
  override: (id: string, item: string, payload: components['schemas']['OverrideRequest']) =>
    request<ItemDetail>(`/calculations/${id}/items/${item}`, json(payload, 'PATCH')),
  calculate: (payload: CalculationRequest, key: string) =>
    request<Job>('/calculations', json(payload, 'POST', key)),
  approve: (id: string, payload: components['schemas']['ApprovalRequest'], key: string) =>
    request<Approval>(`/calculations/${id}/approve`, json(payload, 'POST', key)),
  initializeStock: (payload: unknown) =>
    request<StockState>('/stock-monitoring', json(payload)),
  stockState: (warehouse: string, sku: string) =>
    request<StockState>(`/stock-monitoring/${encodeURIComponent(warehouse)}/${encodeURIComponent(sku)}`),
  stockEvent: (payload: unknown) => request<StockEventResult>('/stock-events', json(payload)),
  upload: (files: File[], context: string, key: string, supplier = 'systeme-electric') => {
    const body = new FormData()
    files.forEach((file) => body.append('files', file))
    body.append('supplier_id', supplier)
    if (context.trim()) body.append('context', context)
    return request<Job>('/datasets/import', {
      method: 'POST',
      headers: { 'Idempotency-Key': key },
      body,
    })
  },
  wait: async (initial: Job, update: (job: Job) => void, signal: AbortSignal) => {
    signal.throwIfAborted()
    let job = initial
    const started = Date.now()
    while (job.status !== 'succeeded' && job.status !== 'failed') {
      update(job)
      await new Promise<void>((resolve, reject) => {
        const cancel = () => {
          clearTimeout(timer)
          reject(new DOMException('Aborted', 'AbortError'))
        }
        const timer = setTimeout(() => {
          signal.removeEventListener('abort', cancel)
          resolve()
        }, 1200)
        if (signal.aborted) cancel()
        else signal.addEventListener('abort', cancel, { once: true })
      })
      if (Date.now() - started > 120000)
        throw new ApiError(
          'Задача выполняется дольше обычного. Обновите страницу позже.',
          'TIMEOUT',
          0,
        )
      job = await request<Job>(`/jobs/${job.job_id}`, { signal })
    }
    signal.throwIfAborted()
    update(job)
    if (job.status === 'failed')
      throw new ApiError(
        job.error?.error.message || 'Не удалось завершить задачу.',
        job.error?.error.code || 'FAILED',
        422,
      )
    return job
  },
  download: async (id: string) => {
    const response = await fetch(`${base}/approvals/${id}/export.csv`)
    if (!response.ok) throw new Error('Не удалось скачать CSV. Повторите попытку.')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = `tirek-${id.slice(0, 8)}.csv`
    document.body.append(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  },
}
