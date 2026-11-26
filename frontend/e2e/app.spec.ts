import { expect, test } from '@playwright/test';

test('dashboard loads data, filters a funnel and refreshes it', async ({ page }) => {
  const projects: string[] = [];
  await page.route('**/api/pulse?*', async route => {
    const project = new URL(route.request().url()).searchParams.get('project')!;
    projects.push(project);
    await route.fulfill({ json: {
      available: true, backend: 'graphdb', categories: [{ id: '7', name: 'Продажи' }],
      summary: { active_deals: project === '7' ? 12 : 42, created_24h: 1,
        created_7d: 3, created_30d: 5, red_deals_count: 0 },
    } });
  });
  await page.route('**/api/view-state?*', route => route.fulfill({ json: {
    available: true, active_situations: [], categories: [],
    view_profile: { block_order: [], highlights: [] },
  } }));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Пульс сделок', exact: true })).toBeVisible();
  await expect(page.locator('#active-deals')).toHaveText('42');
  await page.getByRole('combobox', { name: 'Фильтр дашборда по воронке' }).selectOption('7');
  await expect(page.locator('#active-deals')).toHaveText('12');
  await page.getByRole('button', { name: 'Обновить данные' }).click();
  await expect.poll(() => projects.filter(project => project === '7').length).toBe(2);
});

test('chat sends a question, restores the answer after reload and resets it', async ({ page }) => {
  await page.route('**/api/agent/progress?*', route => route.fulfill({ json: { stage: 'done' } }));
  await page.route('**/api/agent/profile', route => route.fulfill({ json: {} }));
  await page.route('**/api/agent', async route => {
    expect(route.request().method()).toBe('POST');
    expect(route.request().postDataJSON().message).toBe('Сколько сделок?');
    await route.fulfill({ json: {
      reply: 'Активных сделок: **42**', session_id: 'e2e-session',
      history: [{ role: 'user', content: 'Сколько сделок?' },
        { role: 'assistant', content: 'Активных сделок: **42**' }],
    } });
  });
  await page.goto('/agent');
  await page.locator('#chat-input').fill('Сколько сделок?');
  await page.getByRole('button', { name: 'Отправить', exact: true }).click();
  await expect(page.locator('.chat-assistant')).toContainText('Активных сделок: 42');
  await page.reload();
  await expect(page.locator('.chat-assistant')).toContainText('Активных сделок: 42');
  await page.getByRole('button', { name: 'Сбросить чат' }).click();
  await page.reload();
  await expect(page.locator('.chat-message')).toHaveCount(0);
});

test('chat shows an API error and re-enables sending', async ({ page }) => {
  await page.route('**/api/agent/progress?*', route => route.fulfill({ json: { stage: 'done' } }));
  await page.route('**/api/agent/profile', route => route.fulfill({ json: {} }));
  await page.route('**/api/agent', route => route.fulfill({ status: 503, json: { error: 'Агент временно недоступен' } }));
  await page.goto('/agent');
  await page.locator('#chat-input').fill('Сколько сделок?');
  await page.getByRole('button', { name: 'Отправить', exact: true }).click();
  await expect(page.getByRole('alert')).toHaveText('Агент временно недоступен');
  await expect(page.getByRole('button', { name: 'Отправить', exact: true })).toBeEnabled();
});

test('theme survives reload', async ({ page }) => {
  await page.goto('/agent');
  const toggle = page.getByRole('switch', { name: 'Светлая тема' });
  await toggle.click();
  await expect(toggle).toBeChecked();
  await page.reload();
  await expect(toggle).toBeChecked();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});
