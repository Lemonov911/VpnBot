import { test, expect } from '@playwright/test'

const PAGES = [
  { path: '/', name: 'home' },
  { path: '/vpn', name: 'vpn' },
  { path: '/esim', name: 'esim' },
  { path: '/support', name: 'support' },
  { path: '/referral', name: 'referral' },
  { path: '/instructions', name: 'instructions' },
]

for (const lang of ['ru', 'en']) {
  for (const { path: urlPath, name } of PAGES) {
    test(`${name} — ${lang}`, async ({ page }) => {
      await page.goto('/')
      await page.evaluate((l) => localStorage.setItem('lang', l), lang)
      await page.goto(urlPath)
      await page.waitForTimeout(500)
      await expect(page).toHaveScreenshot(`${name}-${lang}.png`, {
        fullPage: true,
        maxDiffPixelRatio: 0.2,
      })
    })
  }
}

test('navigation works', async ({ page }) => {
  await page.goto('/')
  await page.evaluate(() => localStorage.setItem('lang', 'en'))

  await page.goto('/')
  await page.waitForTimeout(300)
  expect(await page.locator('text=VPN & eSIM').first().isVisible()).toBeTruthy()

  await page.locator('text=Help').first().click()
  await page.waitForTimeout(300)
  expect(page.url()).toContain('/support')
  expect(await page.locator('text=Support').first().isVisible()).toBeTruthy()

  await page.locator('text=Friends').first().click()
  await page.waitForTimeout(300)
  expect(page.url()).toContain('/referral')
  expect(await page.locator('text=Invite a friend').first().isVisible()).toBeTruthy()
})

test('language switch ru->en', async ({ page }) => {
  await page.goto('/')
  await page.evaluate(() => localStorage.setItem('lang', 'ru'))
  await page.goto('/')
  await page.waitForTimeout(300)
  expect(await page.locator('text=Пригласи друга').first().isVisible()).toBeTruthy()

  await page.locator('button:has-text("RU")').first().click()
  await page.locator('text=English').first().click()
  await page.waitForTimeout(300)
  expect(await page.locator('text=Invite a friend').first().isVisible()).toBeTruthy()
})