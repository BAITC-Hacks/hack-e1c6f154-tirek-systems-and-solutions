import { expect, test } from '@playwright/test'
import type { APIRequestContext } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import type { Workspace } from '../src/api/client'

async function freshScenario(request: APIRequestContext) {
  const response = await request.post('http://127.0.0.1:8000/api/v1/calculations', {
    headers: { 'Idempotency-Key': crypto.randomUUID() },
    data: {
      dataset_id: 'demo-systeme-v1',
      as_of_date: '2026-09-22',
      warehouse_ids: ['almaty'],
      category_codes: [],
      horizon_days: 28,
      lead_time_days: 7,
      review_period_days: 21,
      mode: 'scenario',
      category_policies: [],
      economic_profiles: [],
      growth_adjustments: [],
      budget_kzt: null,
      request_ai_review: false,
    },
  })
  expect(response.status()).toBe(202)
  const job = await response.json()
  await expect
    .poll(async () => {
      const status = await request.get('http://127.0.0.1:8000/api/v1/jobs/' + job.job_id)
      return (await status.json()).status
    })
    .toBe('succeeded')
}

test('frontend proxies API requests as JSON', async ({ request }) => {
  for (const path of ['/api/v1/health', '/api/v1/workspace']) {
    const response = await request.get(path)
    expect(response.status()).toBe(200)
    expect(response.headers()['content-type']).toContain('application/json')
    const body = await response.json()
    if (path.endsWith('/health')) expect(body.status).toBe('ok')
    else expect(body.datasets).toBeInstanceOf(Array)
  }
})

test('desktop overview, search, drawer and data sources', async ({ page, request }) => {
  await freshScenario(request)
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Запасы под контролем.' })).toBeVisible()
  await expect(page.locator('.demand-chart svg').first()).toBeVisible()
  await page.screenshot({ path: 'test-results/tirek-desktop.png', fullPage: true })
  await page.getByRole('link', { name: 'Рекомендации', exact: false }).first().click()
  const search = page.getByRole('textbox', { name: 'Поиск по артикулу или названию' })
  await search.fill('A9R41240')
  await expect(page.locator('.product-cell')).toHaveCount(1)
  await expect(page.locator('.quantity-unknown')).toHaveText('—')
  await page.getByRole('button', { name: 'Открыть A9R41240', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('heading', { name: 'Дифференциальный выключатель' })).toBeVisible()
  await expect(
    dialog.getByText('Нет актуального свободного остатка.', { exact: false }),
  ).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeVisible()
  await search.fill('EZ9F34116')
  await page.getByRole('button', { name: 'Открыть EZ9F34116', exact: true }).click()
  await expect(dialog.getByRole('heading', { name: 'История и прогноз спроса' })).toBeVisible()
  await page.screenshot({ path: 'test-results/tirek-product.png', fullPage: true })
  await page.keyboard.press('Escape')
  await page.getByRole('link', { name: 'Источники данных', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Отчёт о качестве' })).toBeVisible()
  await page
    .getByRole('combobox', { name: 'Версия набора', exact: true })
    .selectOption('demo-systeme-v1')
  await expect(page.locator('.source-table tbody tr')).toHaveCount(6)
  await page.screenshot({ path: 'test-results/tirek-data.png', fullPage: true })
  expect(errors).toEqual([])
})

test('quantity validation, save, approval and real CSV download', async ({ page, request }) => {
  await freshScenario(request)
  await page.goto('/recommendations')
  await page.getByRole('button', { name: 'Открыть EZ9F34116', exact: true }).click()
  await page.getByRole('tab', { name: 'Корректировка' }).click()
  await page.getByLabel('Количество, шт').fill('13')
  await page.getByLabel('Причина корректировки').fill('Уточнён план монтажа')
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('кратно 12')
  await page.getByLabel('Количество, шт').fill('156')
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click()
  await expect(page.getByRole('dialog')).not.toBeVisible()
  await page.getByRole('checkbox', { name: 'Выбрать EZ9F34116', exact: true }).check()
  await page.getByRole('button', { name: 'Утвердить (1)', exact: true }).click()
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Утвердить и скачать CSV' }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toMatch(/^tirek-.*\.csv$/)
  const stream = await download.createReadStream()
  let csv = ''
  for await (const chunk of stream!) csv += chunk.toString('utf8')
  expect(csv).toContain(';156;Уточнён план монтажа;')
  await expect(page.getByRole('heading', { name: 'История решений', exact: true })).toBeVisible()
  await page.reload()
  await expect(page.locator('.history-table tbody tr').first()).toBeVisible()
})

test('mobile layout, navigation and keyboard-accessible drawer', async ({ page, request }) => {
  await freshScenario(request)
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Запасы под контролем.' })).toBeVisible()
  await expect(page.locator('.demand-chart svg').first()).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  )
  await page.screenshot({ path: 'test-results/tirek-mobile.png', fullPage: true })
  await page.getByRole('button', { name: 'Открыть меню', exact: true }).click()
  await page.getByRole('link', { name: 'Рекомендации', exact: false }).first().click()
  await page.locator('.product-cell button').first().click()
  await expect(page.getByRole('dialog')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  )
  await page.screenshot({ path: 'test-results/tirek-mobile-product.png', fullPage: true })
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).not.toBeVisible()
})

test('failed API stays an error and never falls back to mock data', async ({ page }) => {
  await page.route('**/api/v1/workspace', (route) => route.abort('failed'))
  await page.goto('/')
  await expect(page.getByRole('alert')).toContainText('Нет связи с сервером')
  await expect(page.getByRole('heading', { name: 'Запасы под контролем.' })).not.toBeVisible()
  await page.unroute('**/api/v1/workspace')
  await page.getByRole('button', { name: 'Повторить', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Запасы под контролем.' })).toBeVisible()
})

for (const invalidResponse of [
  {
    contentType: 'text/html',
    body: '<!doctype html><html></html>',
    message: 'Сервер вернул неверный формат данных',
  },
  {
    contentType: 'application/json',
    body: '{broken',
    message: 'Не удалось прочитать данные сервера',
  },
]) {
  test(`invalid API response (${invalidResponse.contentType}) is readable and recoverable`, async ({
    page,
  }) => {
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    await page.route('**/api/v1/workspace', (route) =>
      route.fulfill({
        status: 200,
        contentType: invalidResponse.contentType,
        body: invalidResponse.body,
      }),
    )
    await page.goto('/')
    await expect(page.getByRole('alert')).toContainText(invalidResponse.message)
    await expect(page.getByRole('alert')).not.toContainText('Unexpected token')
    await page.unroute('**/api/v1/workspace')
    await page.getByRole('button', { name: 'Повторить', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Запасы под контролем.' })).toBeVisible()
    expect(errors).toEqual([])
  })
}

test('new calculation dialog runs a background job with the selected category', async ({
  page,
}) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Новый расчёт', exact: true }).click()
  await page.getByRole('combobox', { name: 'Категория', exact: true }).selectOption('Автоматы')
  await page.getByRole('button', { name: 'Подготовить рекомендации' }).click()
  await expect(page.getByRole('dialog')).not.toBeVisible({ timeout: 15000 })
  await expect(
    page.getByRole('heading', { name: 'Рекомендации по закупке', exact: true }),
  ).toBeVisible()
  await expect(page.locator('.product-cell')).toHaveCount(4)
  await page.getByRole('textbox', { name: 'Поиск по артикулу или названию' }).fill('NO_SUCH_SKU')
  await expect(page.getByRole('heading', { name: 'Подходящих позиций нет' })).toBeVisible()
  await page.getByRole('button', { name: 'Сбросить фильтры', exact: true }).click()
  await expect(page.locator('.product-cell')).toHaveCount(4)
})

test('uploaded XLSX produces a real report and does not enable an incomplete dataset', async ({
  page,
}) => {
  const suffix = process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'
  const candidates = [resolve('..', '.venv-ml', suffix), resolve('..', '.venv', suffix)]
  const python = process.env.TEST_PYTHON || candidates.find(existsSync) || 'python'
  const buffer = execFileSync(python, [
    '-c',
    'import sys; from io import BytesIO; from openpyxl import Workbook; w=Workbook(); w.active.append(["sku", "quantity"]); w.active.append(["SYNTHETIC-TEST", 12]); b=BytesIO(); w.save(b); sys.stdout.buffer.write(b.getvalue())',
  ])
  await page.goto('/data')
  await page.locator('input[type=file]').setInputFiles({
    name: 'MOQ-ui-test.xlsx',
    mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    buffer,
  })
  await page.getByRole('button', { name: 'Проверить и загрузить' }).click()
  await expect(
    page.getByText('Для модели нужны все шесть ролей файлов', { exact: false }),
  ).toBeVisible({ timeout: 15000 })
  await expect(page.getByRole('button', { name: 'Перейти к расчёту' })).not.toBeVisible()
  await expect(page.locator('.source-table tbody tr')).toHaveCount(1)
})

test('observed dataset passes mode, categories and policies to the calculation API', async ({
  page,
  request,
}) => {
  // A UI contract fixture, not a claim of forecast accuracy on partner data.
  const workspace: Workspace = await (await request.get('/api/v1/workspace')).json()
  const fixture = {
    ...workspace.datasets.find((dataset) => dataset.dataset_id === 'demo-systeme-v1')!,
    dataset_id: 'ui-contract-observed',
    dataset_version: 'ui-contract-v1',
    source_kind: 'observed' as const,
    data_as_of: '2026-09-23',
    sku_count: 565,
    calculation_allowed: true,
  }
  await page.route('**/api/v1/workspace', (route) =>
    route.fulfill({ json: { ...workspace, datasets: [fixture, ...workspace.datasets] } }),
  )
  await page.route('**/api/v1/calculations', (route) =>
    route.fulfill({
      status: 422,
      json: {
        error: {
          code: 'INVALID_PARAMETERS',
          message: 'Проверьте заполнение полей.',
          details: [
            { field: 'category_policies.0.rationale', message: 'Тестовая проверка источника' },
          ],
        },
      },
    }),
  )
  await page.goto('/data')
  await page.getByRole('button', { name: 'Перейти к расчёту' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('combobox', { name: 'Набор данных', exact: true })).toHaveValue(
    fixture.dataset_id,
  )
  await expect(dialog.getByLabel('Дата расчёта', { exact: true })).toHaveValue('2026-09-23')
  await dialog
    .getByRole('combobox', { name: 'Режим расчёта', exact: true })
    .selectOption('operational')
  await dialog.getByRole('textbox', { name: 'Категория', exact: true }).fill('7, 8')
  await dialog.getByLabel('Срок поставки, дней', { exact: true }).fill('10')
  const policies = [
    {
      category_raw: '7',
      target_quantile: 0.9,
      minimum_target_quantile: null,
      source_kind: 'manual',
      rationale: 'Явная политика закупщика',
    },
  ]
  await dialog
    .getByRole('textbox', { name: 'Политики категорий · JSON', exact: false })
    .fill(JSON.stringify(policies))
  const sent = page.waitForRequest(
    (value) => value.url().endsWith('/api/v1/calculations') && value.method() === 'POST',
  )
  await dialog.getByRole('button', { name: 'Подготовить рекомендации' }).click()
  expect((await sent).postDataJSON()).toMatchObject({
    dataset_id: fixture.dataset_id,
    mode: 'operational',
    category_codes: ['7', '8'],
    category_policies: policies,
    lead_time_days: 10,
    review_period_days: 18,
  })
  await expect(dialog.getByRole('alert')).toContainText('category_policies.0.rationale')
})

test('settings reports the runtime and supplier filtering works', async ({ page, request }) => {
  await freshScenario(request)
  await page.goto('/settings')
  await expect(page.getByText('Адаптер настроен', { exact: true })).toBeVisible()
  await page.goto('/recommendations')
  await page
    .getByRole('combobox', { name: 'Поставщик', exact: true })
    .selectOption('systeme-electric')
  await expect(page.locator('.product-cell')).toHaveCount(12)
})

test('AI review button displays a provider judgement without changing quantity', async ({
  page,
  request,
}) => {
  await freshScenario(request)
  const workspace = await (await request.get('/api/v1/workspace')).json()
  const id = workspace.latest_calculation_id
  const items = await (await request.get(`/api/v1/calculations/${id}/recommendations`)).json()
  const item = items.items.find((value: { sku: string }) => value.sku === 'EZ9F34116')
  const detail = await (
    await request.get(`/api/v1/calculations/${id}/items/${item.item_id}`)
  ).json()
  // UI fixture only: the real HTTP transport is covered separately with MockTransport.
  await page.route('**/api/v1/health', async (route) => {
    const response = await route.fetch()
    await route.fulfill({ json: { ...(await response.json()), llm_available: true } })
  })
  await page.route('**/ai-review', (route) =>
    route.fulfill({
      json: {
        ...detail,
        item: {
          ...detail.item,
          ai: {
            status: 'reviewed',
            verdict: 'needs_review',
            reasons: ['Тестовое заключение: проверьте сроки поставки.'],
            evidence_ids: [item.evidence[0].id],
            rule_ids: ['AI-01'],
            suggested_action: 'Проверить сроки поставки',
            provider_model: 'UI-TEST-MODEL',
          },
        },
      },
    }),
  )
  await page.goto('/recommendations')
  await page.getByRole('button', { name: 'Открыть EZ9F34116', exact: true }).click()
  await page.getByRole('tab', { name: 'Обоснование', exact: true }).click()
  await page.getByRole('button', { name: 'Проверить с ИИ', exact: true }).click()
  await expect(
    page.getByText('Тестовое заключение: проверьте сроки поставки.', { exact: true }),
  ).toBeVisible()
  await expect(page.getByText('Модель: UI-TEST-MODEL', { exact: true })).toBeVisible()
  const unchanged = await (
    await request.get(`/api/v1/calculations/${id}/items/${item.item_id}`)
  ).json()
  expect(unchanged.item.final_quantity).toBe(item.final_quantity)
})

test('observed history remains visible when regular sales and quantiles are unavailable', async ({
  page,
}) => {
  await page.route('**/calculations/*/items/*', async (route) => {
    const response = await route.fetch()
    const detail = await response.json()
    detail.history = detail.history.map((row: object) => ({
      ...row,
      observed_sales: 10,
      regular_sales: null,
      source_kind: 'observed',
    }))
    detail.item.forecast = { ...detail.item.forecast, p10: null, p50: null, p90: null, mean: 20 }
    await route.fulfill({ json: detail })
  })
  await page.goto('/')
  await expect(page.getByText('Наблюдаемые продажи', { exact: true })).toBeVisible()
  await expect(page.getByText('Прогнозный интервал не рассчитан', { exact: true })).toBeVisible()
  await expect(page.locator('.demand-chart')).toHaveAttribute(
    'aria-label',
    /Последний период: 10 .*Прогноз: 20/,
  )
  await expect(page.locator('.recharts-area-area').first()).toBeVisible()
})
