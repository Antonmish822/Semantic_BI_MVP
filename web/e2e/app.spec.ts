import { expect, test, type Page } from '@playwright/test';

const categories = [{ id: '5', name: 'Входящие' }, { id: '9', name: 'Партнёры' }];
const pulse = { available: true, summary: { active_deals: 42, created_24h: 3, created_7d: 12, created_30d: 34, red_deals_count: 4 }, categories, employees: [{ name: 'Анна', active_deals: 8, activities_24h: 2 }] };
const leadPulse = { available: true, summary: { created_1d: 6, created_7d: 18, created_30d: 70, average_response_minutes: 42, sla_within_2h_ratio: 0.75, sla_eligible_count: 12 }, response_trend: [{ date: '2026-11-20', sla_within_2h_ratio: 0.75 }], red_leads: [{ title: 'Лид для проверки', manager: 'Анна' }] };

async function mockApi(page: Page) {
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    let data: unknown = {};
    if (path === '/api/pulse') data = pulse;
    else if (path === '/api/red-deals') data = { red_deals: [{ title: 'Сделка с паузой', manager: 'Анна' }] };
    else if (path === '/api/recent-activities') data = { activities: [{ title: 'Звонок', manager: 'Анна' }] };
    else if (path === '/api/deal-activity-distribution') data = { buckets: [{ key: 30, deal_count: 4 }], categories };
    else if (path === '/api/view-state') data = { available: true, active_situations: [{ id: 'red', title: 'Проверить задержки' }] };
    else if (path === '/api/lead-pulse') data = leadPulse;
    else if (path === '/api/lead-conversion-trend') data = { conversion_trend: [{ date: '2026-11-20', average_conversion_minutes: 90, sample_size: 4 }], categories };
    else if (path === '/api/employee-recent-activities') data = { activities: [{ title: 'Встреча', manager: 'Анна' }], employees: [] };
    else if (path === '/api/lead-employee-pulse') data = { employees: [{ uri: 'urn:user:1', name: 'Анна' }], employee_trend: [{ date: '2026-11-20', average_response_minutes: 40 }] };
    else if (path === '/api/agent') data = { reply: 'Есть 42 активные сделки.', history: [], session_id: 'session-demo' };
    else if (path === '/api/agent/progress') { await route.fulfill({ status: 404, json: { error: 'Запрос не найден' } }); return; }
    else if (path === '/api/published-queries' && request.method() === 'GET') data = { queries: [
      { id: 'q1', title: 'Сделки на паузе', description: 'Проверка реакции', query: 'SELECT * WHERE { ?s ?p ?o } LIMIT 3' },
      { id: 'q2', title: 'Период по параметрам', description: 'Проверка параметров', parameters: ['enabled', 'from', 'at'], query: 'SELECT * WHERE { FILTER (?enabled = {{enabled}}) FILTER (?from = {{from}}) FILTER (?at = {{at}}) }' },
    ], admin_enabled: true };
    else if (path === '/api/agent/query') data = { request_id: 'demo', tool_result: { rows: [{ deal: 'urn:deal:1' }] } };
    else if (path === '/api/ontologies' && request.method() === 'GET') data = { ontologies: [{ slug: 'sales', publication_status: 'published' }], admin_enabled: true };
    else if (path === '/api/ontologies/quarantine') data = { items: [] };
    else if (path === '/api/ontologies/sales') data = { slug: 'sales', sha256: 'abc123', text: '@prefix owl: <http://www.w3.org/2002/07/owl#> .\n<urn:bitrix24:ontology:sales> a owl:Ontology .', publication_status: 'published' };
    else if (path === '/api/ontologies/validate') data = { valid: true, uri: 'urn:bitrix24:ontology:sales' };
    else if (path === '/api/ontologies' && request.method() === 'POST') data = { slug: 'sales', sha256: 'abc123', uri: 'urn:bitrix24:ontology:sales' };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
  });
}

test('deals dashboard loads and follows funnel changes', async ({ page }) => {
  await mockApi(page);
  await page.goto('/assets/');
  await expect(page.getByRole('heading', { name: 'Пульс сделок' })).toBeVisible();
  await expect(page.getByText('42')).toBeVisible();
  await page.getByRole('combobox', { name: 'Воронка' }).selectOption('5');
  await expect(page.getByText('Проверить задержки')).toBeVisible();
});

test('leads and employees pages render real API data', async ({ page }) => {
  await mockApi(page);
  await page.goto('/assets/leads');
  await expect(page.getByText('Лид для проверки')).toBeVisible();
  await page.goto('/assets/employees');
  await expect(page.getByRole('table').first().getByText('Анна')).toBeVisible();
});

test('chat submits a question and keeps its session in this tab', async ({ page }) => {
  await mockApi(page);
  await page.goto('/assets/agent');
  await page.getByLabel('Сообщение').fill('Сколько активных сделок?');
  await page.getByRole('button', { name: 'Отправить' }).click();
  await expect(page.getByText('Есть 42 активные сделки.')).toBeVisible();
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem('reactAgentSession'))).toBe('session-demo');
});

test('query catalog opens a saved query and executes it over MCP API', async ({ page }) => {
  await mockApi(page);
  await page.goto('/assets/queries');
  await page.getByRole('button', { name: /Сделки на паузе/ }).click();
  await page.getByRole('button', { name: 'Выполнить через MCP' }).click();
  await expect(page.getByText(/urn:deal:1/)).toBeVisible();
});

test('query catalog submits parameter values as strings', async ({ page }) => {
  await mockApi(page);
  let submitted: Record<string, unknown> | undefined;
  await page.route('**/api/agent/query', async (route) => {
    submitted = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ rows: [] }) });
  });
  await page.goto('/assets/queries');
  await page.getByRole('button', { name: /Период по параметрам/ }).click();
  await page.getByLabel('enabled').fill('true');
  await page.getByLabel('from').fill('2026-11-24');
  await page.getByLabel('at').fill('14:25');
  await page.getByRole('button', { name: 'Выполнить через MCP' }).click();
  await expect.poll(() => submitted).toEqual({
    query: 'SELECT * WHERE { FILTER (?enabled = {{enabled}}) FILTER (?from = {{from}}) FILTER (?at = {{at}}) }',
    parameters: { enabled: 'true', from: '2026-11-24', at: '14:25' },
  });
});

test('query debugger remains at its tools alias', async ({ page }) => {
  await mockApi(page);
  await page.goto('/assets/tools/queries');
  await expect(page.getByRole('heading', { name: 'Отладчик запросов' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Проверить через MCP' })).toBeVisible();
});

test('ontology editor loads TTL and validates without publishing it', async ({ page }) => {
  await mockApi(page);
  await page.goto('/assets/tools/ontology');
  await page.getByRole('button', { name: /sales\.ttl/ }).click();
  await expect(page.getByLabel('Текст Turtle')).toHaveValue(/urn:bitrix24:ontology:sales/);
  await page.getByRole('button', { name: 'Проверить', exact: true }).click();
  await expect(page.getByText(/Turtle корректен/)).toBeVisible();
});

test('mobile layout stays within the viewport', async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/assets/');
  await expect(page.getByRole('heading', { name: 'Пульс сделок' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(391);
});
