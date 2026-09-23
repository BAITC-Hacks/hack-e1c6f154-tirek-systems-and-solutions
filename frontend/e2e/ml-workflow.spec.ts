import { expect, test } from '@playwright/test'
import { readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'

// Generate with scripts/generate_sample_data.py first. All sales and accounts
// below are explicit synthetic fixtures; the server executes actual model code.
test.use({ actionTimeout: 15000 })

for (const scenario of [
  {
    supplier: 'systeme-electric',
    directory: 'samples',
    count: 4,
    category: 'Учебная категория',
    sku: 'SYN-REG-001',
  },
  {
    supplier: 'iek',
    directory: 'samples-iek',
    count: 3,
    category: 'Учебная IEK',
    sku: 'SYN-IEK-PCS',
  },
]) {
  test(`${scenario.supplier}: uploaded XLSX runs model and produces approved CSV`, async ({
    page,
  }) => {
    test.setTimeout(180000)
    const sampleDir = resolve(process.cwd(), '../.local-data', scenario.directory)
    const screenshot = (name: string) =>
      page.screenshot({ path: `test-results/${scenario.supplier}-${name}.png`, fullPage: true })
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    await page.goto('/')
    await page.getByRole('button', { name: 'Зарегистрироваться', exact: true }).click()
    await page.getByLabel('Ваше имя', { exact: true }).fill('Проверка ML')
    await page.getByLabel('Название компании или команды').fill('SYNTHETIC ML test')
    await page.getByLabel('Электронная почта').fill(`ml-${Date.now()}@example.test`)
    await page.getByLabel('Пароль', { exact: true }).fill('Synthetic model browser test 2026!')
    await page.getByRole('button', { name: 'Создать аккаунт', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Запасы под контролем.' })).toBeVisible()
    await page.getByRole('link', { name: 'Источники данных', exact: true }).click()
    await page
      .getByRole('combobox', { name: 'Поставщик файлов', exact: true })
      .selectOption(scenario.supplier)
    const sampleDownload = page.waitForEvent('download')
    await page.getByRole('button', { name: 'Скачать учебный набор', exact: true }).click()
    expect((await sampleDownload).suggestedFilename()).toBe(
      `tirek-synthetic-${scenario.supplier}.zip`,
    )
    await page.locator('input[type=file][accept=".xlsx"]').setInputFiles(
      readdirSync(sampleDir)
        .filter((name) => name.endsWith('.xlsx'))
        .map((name) => resolve(sampleDir, name)),
    )
    await page.locator('.context-details summary').click()
    await page
      .getByLabel('Файл дополнительного контекста', { exact: true })
      .setInputFiles(resolve(sampleDir, 'additional-context.SYNTHETIC.json'))
    await expect(page.getByLabel('JSON с ценами, наличием и обязательствами')).not.toHaveValue('')
    expect(
      JSON.parse(await page.getByLabel('JSON с ценами, наличием и обязательствами').inputValue()),
    ).toEqual(
      JSON.parse(readFileSync(resolve(sampleDir, 'additional-context.SYNTHETIC.json'), 'utf-8')),
    )
    await page.getByRole('button', { name: 'Проверить и загрузить', exact: true }).click()
    await expect(
      page.getByRole('combobox', { name: 'Версия набора', exact: true }),
    ).not.toHaveValue('demo-systeme-v1', { timeout: 60000 })
    await expect(page.getByRole('button', { name: 'Перейти к расчёту', exact: true })).toBeVisible({
      timeout: 60000,
    })
    await screenshot('mvp-imported-xlsx')
    await page.getByRole('button', { name: 'Перейти к расчёту', exact: true }).click()
    await page.getByLabel('Коды категорий для политики', { exact: true }).fill(scenario.category)
    await page
      .getByLabel('Основание политики', { exact: true })
      .fill('Явная учебная политика для проверки сквозного пути')
    await page.getByRole('button', { name: 'Применить политику к расчёту' }).click()
    await page.getByRole('button', { name: 'Подготовить рекомендации' }).click()
    await expect(page.getByRole('dialog')).not.toBeVisible({ timeout: 60000 })
    await expect(page.locator('.product-cell')).toHaveCount(scenario.count)

    const workspace = await (await page.request.get('/api/v1/workspace')).json()
    const calculationId = workspace.latest_calculation_id
    expect(calculationId).not.toBe('demo-calc-001')
    const recommendations = await (
      await page.request.get(`/api/v1/calculations/${calculationId}/recommendations`)
    ).json()
    expect(recommendations.meta.mode).toBe('scenario')
    expect(
      recommendations.items.every(
        (item: { forecast: { method: string }; final_quantity: number | null }) =>
          item.forecast.method !== 'contract_example' && item.final_quantity !== null,
      ),
    ).toBe(true)
    expect(
      recommendations.items.some(
        (item: { forecast: { method: string } }) => item.forecast.method === 'ml',
      ),
    ).toBe(true)
    if (scenario.supplier === 'iek')
      expect(new Set(recommendations.items.map((item: { unit: string }) => item.unit))).toEqual(
        new Set(['шт', 'м', 'упак']),
      )
    await screenshot('mvp-ml-recommendations')

    if (scenario.supplier === 'systeme-electric') {
      await page.getByRole('button', { name: 'Открыть SYN-ONE-003', exact: true }).click()
      await page.getByRole('tab', { name: 'Обоснование' }).click()
      await expect(page.getByRole('dialog')).toContainText(/300/)
      await screenshot('mvp-ml-evidence')
      await page.keyboard.press('Escape')
    }
    await page.getByRole('checkbox', { name: 'Выбрать все видимые позиции' }).check()
    await page.getByRole('button', { name: `Утвердить (${scenario.count})`, exact: true }).click()
    const acknowledge = page.getByRole('checkbox', { name: /Я проверил предупреждения/ })
    if (await acknowledge.isVisible()) await acknowledge.check()
    const downloaded = page.waitForEvent('download')
    await page.getByRole('button', { name: 'Утвердить и скачать CSV', exact: true }).click()
    const download = await downloaded
    const stream = await download.createReadStream()
    let csv = ''
    for await (const chunk of stream!) csv += chunk.toString('utf-8')
    expect(csv).toContain(scenario.sku)
    expect(csv).toContain('scenario')
    await expect(page.getByRole('heading', { name: 'История решений', exact: true })).toBeVisible()
    await page.reload()
    await expect(page.locator('.history-table tbody tr')).toHaveCount(1)
    await screenshot('mvp-approved-order')
    expect(errors).toEqual([])
  })
}
