import { expect, test } from '@playwright/test'
import type { APIRequestContext } from '@playwright/test'

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
