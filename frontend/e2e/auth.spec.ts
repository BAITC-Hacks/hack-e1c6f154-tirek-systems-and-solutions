import { expect, test } from '@playwright/test'

test.use({ baseURL: process.env.AUTH_BASE_URL || 'http://127.0.0.1:5173' })

test.beforeEach(async ({ request }) => {
  const response = await request.get('/api/v1/auth/me')
  if (response.ok())
    test.skip(
      (await response.json()).auth_enabled === false,
      'Run against an isolated auth-enabled API.',
    )
  else expect(response.status()).toBe(401)
})

test('register, real calculation, assistant, persistent login and password change', async ({
  page,
}) => {
  test.setTimeout(90000)
  const email = `ui-${crypto.randomUUID()}@example.test`
  const password = 'Tirek browser test password 42!'
  const nextPassword = 'Tirek updated browser password 84!'
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))

  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'С возвращением' })).toBeVisible()
  await page.getByRole('button', { name: 'Зарегистрироваться', exact: true }).click()
  await page.getByLabel('Ваше имя', { exact: true }).fill('Проверка Tirek')
  await page
    .getByLabel('Название компании или команды', { exact: true })
    .fill('Тестовое пространство')
  await page.getByLabel('Электронная почта', { exact: true }).fill(email)
  await page.getByLabel('Пароль', { exact: true }).fill(password)
  await page.screenshot({ path: 'test-results/tirek-registration.png', fullPage: true })
  await page.getByRole('button', { name: 'Создать аккаунт', exact: true }).click()
  await expect(page.getByRole('link', { name: 'Аккаунт: Проверка Tirek' })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('link', { name: 'Аккаунт: Проверка Tirek' })).toBeVisible()

  // A private workspace is usable immediately; the demo is labelled as synthetic.
  await page.getByRole('link', { name: 'Источники данных', exact: true }).click()
  await page
    .getByRole('combobox', { name: 'Версия набора', exact: true })
    .selectOption('demo-systeme-v1')
  await page.getByRole('button', { name: 'Перейти к расчёту', exact: true }).click()
  await page.getByRole('button', { name: 'Подготовить рекомендации', exact: true }).click()
  await expect(
    page.getByRole('heading', { name: 'Рекомендации по закупке', exact: true }),
  ).toBeVisible()
  await expect(page.locator('.product-cell')).toHaveCount(12)

  await page.getByRole('button', { name: 'Открыть помощника', exact: true }).click()
  const assistant = page.getByRole('dialog')
  await expect(assistant.getByRole('heading', { name: 'Помощник Tirek' })).toBeVisible()
  await assistant
    .getByLabel('Ваш вопрос', { exact: true })
    .fill('Как запустить прогноз по моим Excel?')
  const sent = page.waitForRequest(
    (request) => request.url().endsWith('/assistant/chat') && request.method() === 'POST',
  )
  await assistant.getByRole('button', { name: 'Отправить вопрос', exact: true }).click()
  const payload = (await sent).postDataJSON()
  expect(payload.share_context).toBe(false)
  expect(payload.calculation_id).toBeUndefined()
  await expect(assistant.locator('.assistant-message-assistant')).toBeVisible({ timeout: 45000 })
  await page.screenshot({ path: 'test-results/tirek-assistant.png', fullPage: true })
  await page.keyboard.press('Escape')

  await page.getByRole('link', { name: 'Аккаунт: Проверка Tirek' }).click()
  const stock = page.locator('.stock-simulator')
  await stock.getByRole('button', { name: 'Начать симуляцию', exact: true }).click()
  await expect(stock.locator('.stock-result')).toContainText('60%')
  await stock.getByLabel('Остаток', { exact: true }).fill('15')
  await stock.getByRole('button', { name: 'Применить снимок', exact: true }).click()
  await expect(stock.locator('.stock-result')).toContainText('Критично')
  await expect(stock.locator('.stock-result')).toContainText('15%')
  await page.screenshot({ path: 'test-results/tirek-stock-monitor.png', fullPage: true })
  await page.getByLabel('Текущий пароль', { exact: true }).fill(password)
  await page.getByLabel('Новый пароль', { exact: true }).fill(nextPassword)
  await page.getByLabel('Повторите новый пароль', { exact: true }).fill(nextPassword)
  await page.getByRole('button', { name: 'Изменить пароль', exact: true }).click()
  await expect(
    page.getByText('Пароль обновлён. Другие сессии завершены.', { exact: true }),
  ).toBeVisible()
  await page.getByRole('button', { name: 'Выйти из аккаунта', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'С возвращением' })).toBeVisible()
  await page.getByLabel('Электронная почта', { exact: true }).fill(email)
  await page.getByLabel('Пароль', { exact: true }).fill(nextPassword)
  await page.getByRole('button', { name: 'Войти', exact: true }).click()
  await expect(
    page.getByRole('heading', { name: 'Настройки пространства', exact: true }),
  ).toBeVisible()
  await expect(page.getByText(email, { exact: true })).toBeVisible()
  expect(errors).toEqual([])
})

test('mobile entry and invalid credentials keep the workspace private', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto('/data')
  await expect(page.getByRole('heading', { name: 'С возвращением' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  )
  await page
    .getByLabel('Электронная почта', { exact: true })
    .fill(`missing-${crypto.randomUUID()}@example.test`)
  await page.getByLabel('Пароль', { exact: true }).fill('Wrong-but-long-password!')
  await page.getByRole('button', { name: 'Войти', exact: true }).click()
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Основная навигация' })).not.toBeVisible()
  await page.screenshot({ path: 'test-results/tirek-login-mobile.png', fullPage: true })
})
