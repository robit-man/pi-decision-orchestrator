import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { mkdir } from 'node:fs/promises';

async function login(page: any) {
  await page.goto('/');
  await page.getByLabel('Username', { exact: true }).fill(process.env.PI_TEST_USER || 'admin');
  await page.getByLabel('Password', { exact: true }).fill(process.env.PI_TEST_PASSWORD || 'browser-test-password');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Overview', exact: true })).toBeVisible();
}

test('guided workflow, graph, inference, fixtures, connections, and account controls', async ({ page }) => {
  test.setTimeout(process.env.PI_TEST_URL ? 240000 : 90000);
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await mkdir('../artifacts/screenshots', { recursive: true });
  await login(page);
  await page.screenshot({ path: '../artifacts/screenshots/overview-desktop.png', fullPage: true });
  const overviewA11y = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(overviewA11y.violations).toEqual([]);
  await page.goto('/#/models');
  await expect(page.getByRole('heading', { name: 'Models', exact: true })).toBeVisible();
  if (!process.env.PI_TEST_URL) {
    const omni = page.locator('.model-row').filter({ has: page.getByRole('heading', { name: 'D1-Omni 600M', exact: true }) });
    if (await omni.getByRole('button', { name: 'Install', exact: true }).count()) {
      await omni.getByRole('button', { name: 'Install', exact: true }).click();
      await expect(omni.getByRole('button', { name: 'Verify', exact: true })).toBeVisible({ timeout: 15000 });
    }
  }
  await page.goto('/#/decisions');
  await page.getByRole('button', { name: 'New decision' }).click();
  await page.getByRole('button', { name: 'Use template' }).first().click();
  await expect(page.getByLabel('Workflow name', { exact: true })).toBeVisible();
  await page.getByLabel('Workflow name', { exact: true }).fill('Browser route ' + Date.now());
  await page.getByLabel('Assigned model').selectOption('laya');
  await page.getByRole('tab', { name: 'Input', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Input contract', exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'Workflow', exact: true }).click();
  await page.getByRole('button', { name: 'Apply policy, Decision evaluation', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Configure Apply policy', exact: true })).toBeVisible();
  await page.screenshot({ path: '../artifacts/screenshots/builder-desktop.png', fullPage: true });
  await page.getByRole('button', { name: 'Save draft', exact: true }).click();
  await expect(page.getByText('Draft saved.', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Test this draft', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Run once', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Run once', exact: true }).click();
  await expect(page.getByText('Acceptance policy passed', { exact: true })).toBeVisible({ timeout: process.env.PI_TEST_URL ? 180000 : 15000 });
  await expect(page.getByRole('img', { name: 'next_action option probabilities' })).toBeVisible();
  await expect(page.locator('.probability-row')).toHaveCount(4);
  await page.screenshot({ path: '../artifacts/screenshots/testing-desktop.png', fullPage: true });
  await page.getByRole('button', { name: 'Save test case', exact: true }).click();
  await page.getByLabel('New dataset name', { exact: true }).fill('Browser evaluation ' + Date.now());
  await page.getByRole('dialog').getByRole('button', { name: 'Save test case', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.goto('/#/connections');
  await page.getByRole('button', { name: 'New connection', exact: true }).click();
  await page.getByLabel('Connection name', { exact: true }).fill('Browser local source ' + Date.now());
  await page.getByLabel('Connection type', { exact: true }).selectOption('json');
  await page.getByLabel('Enable connection', { exact: true }).check();
  await page.getByRole('button', { name: 'Save connection', exact: true }).click();
  await expect(page.getByText('Connection saved.', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Close editor', exact: true }).click();
  await page.getByRole('button', { name: 'Test', exact: true }).last().click();
  await expect(page.getByText('Local input passes its schema.', { exact: true })).toBeVisible();
  await page.screenshot({ path: '../artifacts/screenshots/connections-desktop.png', fullPage: true });
  await page.goto('/#/history');
  await expect(page.getByRole('heading', { name: 'Decision history', exact: true })).toBeVisible();
  await page.goto('/#/settings');
  await page.getByRole('tab', { name: 'Access', exact: true }).click();
  await page.getByRole('button', { name: 'Create token', exact: true }).click();
  await page.getByLabel('Agent name', { exact: true }).fill('Browser test agent');
  await page.getByRole('button', { name: 'Create token', exact: true }).last().click();
  await expect(page.locator('.token-secret')).toContainText('pidec_');
  await page.getByRole('button', { name: 'Done', exact: true }).click();
  const a11y = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze(); expect(a11y.violations).toEqual([]);
  expect(errors).toEqual([]);
});

test('mobile navigation, form usability, dark mode, and layout', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 }); await login(page);
  await page.screenshot({ path: '../artifacts/screenshots/overview-mobile.png', fullPage: true });
  await page.getByRole('button', { name: 'Open menu', exact: true }).click();
  await page.getByRole('navigation').getByRole('link', { name: 'Builder', exact: true }).click();
  await expect(page.getByLabel('Workflow name', { exact: true })).toBeVisible();
  await page.getByLabel('Workflow name', { exact: true }).fill('Mobile workflow');
  await page.getByRole('tab', { name: 'Input', exact: true }).click();
  await page.getByRole('button', { name: 'Add field', exact: true }).click();
  await page.screenshot({ path: '../artifacts/screenshots/builder-mobile.png', fullPage: true });
  const dimensions = await page.evaluate(() => ({ width: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth })); expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.width);
  await page.getByRole('button', { name: /Switch to .* mode/ }).click();
  const a11y = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze(); expect(a11y.violations).toEqual([]);
  await page.screenshot({ path: '../artifacts/screenshots/builder-mobile-dark.png', fullPage: true });
});
