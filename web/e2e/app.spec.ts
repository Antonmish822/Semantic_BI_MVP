import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';

const categories = [{ id: '5', name: 'Входящие' }, { id: '9', name: 'Партнёры' }];
const pulse = { available: true, summary: { active_deals: 42, created_24h: 3, created_7d: 12, created_30d: 34, red_deals_count: 4 }, categories, employees: [{ name: 'Анна', active_deals: 8, activities_24h: 2 }] };
const leadPulse = { available: true, summary: { created_1d: 6, created_7d: 18, created_30d: 70, average_response_minutes: 42, sla_within_2h_ratio: 0.75, sla_eligible_count: 12 }, response_trend: [{ date: '2026-11-20', sla_within_2h_ratio: 0.75 }], red_leads: [{ title: 'Лид для проверки', manager: 'Анна' }] };
const parameterizedQuery = 'PREFIX xsd: <http://www.w3.org/2001/XMLSchema#> SELECT * WHERE { FILTER (?enabled = {{enabled}}^^xsd:boolean) FILTER (?from = {{from}}^^xsd:date) FILTER (?at = {{at}}^^xsd:time) }';

async function mockApi(page: Page) {
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    let data: unknown = {};
    if (path === '/api/pulse') data = pulse;
    else if (path === '/api/red-deals') data = { red_deals: [{ title: 'Сделка с паузой', manager: 'Анна' }] };
    else if (path === '/api/recent-activities') data = { activities: [{ title: 'Звонок', manager: 'Анна' }] };
    else if (path === '/api/deal-activity-distribution') data = { total_deals: 4, buckets: [{ key: 30, label: '30+ дней', deal_count: 4 }], categories };
    else if (path === '/api/view-state') data = { available: true, active_situations: [{ id: 'red', title: 'Проверить задержки' }] };
    else if (path === '/api/lead-pulse') data = leadPulse;
    else if (path === '/api/lead-conversion-trend') data = { conversion_trend: [{ date: '2026-11-20', average_conversion_minutes: 90, sample_size: 4 }], categories };
    else if (path === '/api/employee-recent-activities') data = { activities: [{ title: 'Встреча', manager: 'Анна' }], employees: [] };
    else if (path === '/api/lead-employee-pulse') data = { employees: [{ uri: 'urn:user:1', name: 'Анна' }], employee_trend: [{ date: '2026-11-20', average_response_minutes: 40 }] };
    else if (path === '/api/agent') data = { reply: 'Есть 42 активные сделки.', history: [], session_id: 'session-demo' };
    else if (path === '/api/agent/progress') { await route.fulfill({ status: 404, json: { error: 'Запрос не найден' } }); return; }
    else if (path === '/api/published-queries' && request.method() === 'GET') data = { queries: [
      { id: 'q1', title: 'Сделки на паузе', description: 'Проверка реакции', query: 'SELECT * WHERE { ?s ?p ?o } LIMIT 3' },
      { id: 'q2', title: 'Период по параметрам', description: 'Проверка параметров', parameters: ['enabled', 'from', 'at'], query: parameterizedQuery },
    ], admin_enabled: true };
    else if (path === '/api/agent/query') data = { request_id: 'demo', tool_result: { status: 'ok', bindings: [{ deal: 'urn:deal:1' }] } };
    else if (path === '/api/ontologies' && request.method() === 'GET') data = { ontologies: [{ slug: 'sales', filename: 'sales.ttl', publication_status: 'published' }], admin_enabled: true };
    else if (path === '/api/ontologies/quarantine') data = { items: [] };
    else if (path === '/api/ontologies/sales') data = { slug: 'sales', filename: 'sales.ttl', uri: 'urn:bitrix24:ontology:sales', sha256: 'abc123', text: '@prefix owl: <http://www.w3.org/2002/07/owl#> .\n<urn:bitrix24:ontology:sales> a owl:Ontology .', publication_status: 'published' };
    else if (path === '/api/ontologies/sales/delete-preview') data = { graph_fingerprint: 'demo-fingerprint', revision_count: 2, graph_present: true, quarantine_days: 7 };
    else if (path === '/api/ontologies/validate') data = { valid: true, uri: 'urn:bitrix24:ontology:sales' };
    else if (path === '/api/ontologies' && request.method() === 'POST') data = { slug: 'sales', sha256: 'abc123', uri: 'urn:bitrix24:ontology:sales' };
    else if (path === '/api/ontology-assistant/config') data = { enabled: true, model: 'Demo' };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
  });
}

test('deals dashboard loads and follows funnel changes', async ({ page }) => {
  const legacyScripts: string[] = [];
  page.on('request', (request) => {
    if (/\/(?:app\.js|app-[^/]+\.js)$/.test(new URL(request.url()).pathname)) legacyScripts.push(request.url());
  });
  await mockApi(page);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'CRM Pulse' })).toBeVisible();
  await expect(page.locator('#active-deals')).toHaveText('42');
  await page.getByRole('combobox', { name: /по воронке/ }).selectOption('5');
  await expect(page.getByText('Проверить задержки')).toBeVisible();
  await page.locator('#toggle-red').click();
  await expect(page.getByText('Сделка с паузой')).toBeVisible();
  await page.getByRole('button', { name: 'Сводки' }).click();
  await expect(page.locator('#activity-distribution-chart')).toBeVisible();
  expect(legacyScripts).toEqual([]);
  await page.getByRole('link', { name: 'Пульс лидов' }).click();
  await expect(page.getByText('Лид для проверки')).toBeVisible();
  await page.getByRole('link', { name: 'Пульс сделок' }).click();
  await expect(page.locator('#active-deals')).toHaveText('42');
  await expect(page.getByRole('button', { name: 'Сделки' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#red-content')).toBeHidden();
});

test('deals ignore a delayed response for the previous funnel', async ({ page }) => {
  await mockApi(page);
  let releaseOldResponse!: () => void;
  const oldResponse = new Promise<void>((resolve) => { releaseOldResponse = resolve; });
  let completeOldResponse!: () => void;
  const oldResponseCompleted = new Promise<void>((resolve) => { completeOldResponse = resolve; });
  await page.route('**/api/red-deals?project=*', async (route) => {
    const project = new URL(route.request().url()).searchParams.get('project');
    if (project !== '5') {
      await route.fulfill({ json: { red_deals: [{ title: `Сделка воронки ${project}` }] } });
      return;
    }
    await oldResponse;
    try {
      await route.fulfill({ json: { red_deals: [{ title: 'Сделка воронки 5' }] } });
    } finally {
      completeOldResponse();
    }
  });
  const filter = page.getByRole('combobox', { name: /по воронке/ });
  try {
    await page.goto('/index.html');
    await filter.selectOption('5');
    const started = page.waitForRequest('**/api/red-deals?project=5');
    await page.locator('#toggle-red').click();
    await started;
    await filter.selectOption('9');
    await expect(page.getByText('Сделка воронки 9')).toBeVisible();
    releaseOldResponse();
    await oldResponseCompleted;
    await expect(page.locator('#red-rows')).toContainText('Сделка воронки 9');
    await expect(page.locator('#red-rows')).not.toContainText('Сделка воронки 5');
  } finally {
    releaseOldResponse();
  }
});

test('deals refresh the open sections manually and each minute', async ({ page }) => {
  await page.clock.install();
  await mockApi(page);
  await page.goto('/');
  await expect(page.locator('#active-deals')).toHaveText('42');
  await page.locator('#toggle-red').click();
  await expect(page.locator('#red-table')).toBeVisible();
  const refreshRequests = () => Promise.all(
    ['pulse', 'view-state', 'red-deals'].map((path) => page.waitForRequest(
      (request) => new URL(request.url()).pathname === `/api/${path}`,
    )),
  );
  const manual = refreshRequests();
  await page.getByRole('button', { name: 'Обновить данные' }).click();
  await manual;
  const periodic = refreshRequests();
  await page.clock.fastForward(60_000);
  await periodic;
});

test('the deals view profile selects a default funnel and survives browser navigation', async ({ page }) => {
  await mockApi(page);
  await page.route('**/api/view-state?page=deals&project=*', (route) => route.fulfill({ json: {
    view_profile: { block_order: ['red_deals', 'kpi'], highlights: ['red_deals'], default_filters: { project: 'nonempty' } },
  } }));
  await page.goto('/index.html');
  const filter = page.getByRole('combobox', { name: /по воронке/ });
  await expect(filter).toHaveValue('5');
  await expect(page.locator('#deals-red')).toHaveClass(/is-promoted.*is-highlighted/);
  await expect(page.locator('#deals-red')).toHaveCSS('order', '0');
  await expect(page.locator('#deals-activities')).toBeHidden();
  await page.getByRole('link', { name: 'Пульс лидов' }).click();
  await page.goBack();
  await expect(page).toHaveURL(/\/index\.html$/);
  await expect(filter).toHaveValue('5');
  await page.goForward();
  await expect(page).toHaveURL(/\/leads$/);
});

test('leads and employees pages render real API data', async ({ page }) => {
  await mockApi(page);
  await page.goto('/leads');
  await expect(page.getByText('Лид для проверки')).toBeVisible();
  await page.goto('/employees');
  await expect(page.locator('#employees-table').getByText('Анна')).toBeVisible();
});

test('the leads funnel changes conversion independently of the lead pulse', async ({ page }) => {
  await mockApi(page);
  await page.route('**/api/lead-conversion-trend?category=5', (route) =>
    route.fulfill({ json: { conversion_trend: [], categories } }));
  await page.goto('/leads.html');
  await expect(page.locator('#created-1d')).toHaveText('6');
  await page.getByRole('button', { name: 'Сводки' }).click();
  await expect(page.locator('#conversion-trend-chart')).toBeVisible();
  await expect(page.locator('#sla-trend-chart')).toBeVisible();
  const pulseRequests: string[] = [];
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/lead-pulse') pulseRequests.push(request.url());
  });
  await page.getByRole('combobox', { name: 'Фильтр по воронке' }).selectOption('5');
  await expect(page.locator('#conversion-trend-empty')).toBeVisible();
  await expect(page.locator('#sla-trend-chart')).toBeVisible();
  expect(pulseRequests).toEqual([]);
});

test('employee filters stay independent when the activity employee leaves a funnel', async ({ page }) => {
  await mockApi(page);
  const employee = { id: 'urn:user:1', name: 'Анна' };
  await page.route('**/api/employee-recent-activities?*', (route) => {
    const { searchParams } = new URL(route.request().url());
    const secondProject = searchParams.get('project') === '5';
    return route.fulfill({ json: {
      employees: secondProject ? [] : [employee],
      activities: secondProject && searchParams.get('employee') === 'all' ? [{ subject: 'Новое событие' }] : [],
    } });
  });
  await page.route('**/api/lead-employee-pulse?*', (route) => route.fulfill({ json: {
    employees: [employee], employee_trend: [{ date: '2026-11-20', average_response_minutes: 40 }],
  } }));
  await page.goto('/employees.html');
  await page.getByRole('button', { name: 'Сводка' }).click();
  const chartEmployee = page.getByRole('combobox', { name: 'Фильтр графика по сотруднику' });
  await chartEmployee.selectOption(employee.id);
  await page.getByRole('button', { name: 'Сотрудники' }).click();
  const activityEmployee = page.getByRole('combobox', { name: 'Фильтр активностей по сотруднику' });
  await activityEmployee.selectOption(employee.id);
  await page.locator('#toggle-employee-activities').click();
  await page.getByRole('combobox', { name: 'Общий фильтр по воронке' }).selectOption('5');
  await expect(page.getByText('Новое событие')).toBeVisible();
  await expect(activityEmployee).toHaveValue('all');
  await page.getByRole('button', { name: 'Сводка' }).click();
  await expect(chartEmployee).toHaveValue(employee.id);
});

test('chat submits a question and keeps its session in this tab', async ({ page }) => {
  await mockApi(page);
  await page.goto('/agent');
  await expect(page.locator('#saved-prompts-list')).toHaveAttribute('hidden', '');
  await page.locator('#chat-input').fill('Сколько активных сделок?');
  await page.getByRole('button', { name: 'Отправить' }).click();
  await expect(page.getByText('Есть 42 активные сделки.')).toBeVisible();
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem('agentSessionId'))).toBe('session-demo');
});

test('chat restores stored data and saves a Markdown answer query for the debugger', async ({ page }) => {
  await page.addInitScript(() => {
    if (sessionStorage.getItem('seeded')) return;
    sessionStorage.setItem('seeded', '1');
    sessionStorage.setItem('reactAgentSession', 'restored-session');
    sessionStorage.setItem('reactAgentHistory', JSON.stringify([
      { role: 'user', content: 'Старый вопрос' }, { role: 'assistant', content: 'Старый ответ' },
    ]));
    localStorage.setItem('agentSavedPrompts', JSON.stringify(['Ранее сохранённый промпт']));
  });
  await mockApi(page);
  const query = 'SELECT * WHERE { ?s ?p ?o } LIMIT 1';
  await page.route('**/api/agent', (route) => {
    expect(route.request().postDataJSON()).toMatchObject({
      message: 'Первая строка\nВторая строка', session_id: 'restored-session',
    });
    return route.fulfill({ json: {
      reply: '## Ответ\n\n| Название |\n| --- |\n| Сделка |',
      saved_query: query, session_id: 'restored-session', history: [],
    } });
  });
  await page.goto('/agent.html');
  await expect(page.getByText('Старый ответ')).toBeVisible();
  await expect(page.locator('#saved-prompts-count')).toHaveText('1/10');
  const input = page.locator('#chat-input');
  await input.fill('Первая строка');
  await input.press('Shift+Enter');
  await input.pressSequentially('Вторая строка');
  await input.press('Enter');
  await expect(page.getByRole('heading', { name: 'Ответ', exact: true })).toBeVisible();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Скачать CSV' }).click();
  expect((await download).suggestedFilename()).toMatch(/^agent-table-.*\.csv$/);
  await page.getByRole('button', { name: 'Сохранить промпт' }).last().click();
  await page.goto('/tools/queries');
  await page.getByRole('button', { name: /Первая строка Вторая строка/ }).click();
  await expect(page.locator('#saved-query-editor')).toHaveValue(query);
});

test('resetting chat stops progress and drops the in-flight answer', async ({ page }) => {
  await page.clock.install();
  await mockApi(page);
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  let finish!: () => void;
  const finished = new Promise<void>((resolve) => { finish = resolve; });
  await page.route('**/api/agent', async (route) => {
    await pending;
    try { await route.fulfill({ json: { reply: 'Поздний ответ', session_id: 'late-session' } }); }
    finally { finish(); }
  });
  const progress: string[] = [];
  await page.route('**/api/agent/progress?*', (route) => {
    progress.push(route.request().url());
    return route.fulfill({ json: { stage: 'working', message: 'Собираю данные' } });
  });
  try {
    await page.goto('/agent');
    await page.locator('#chat-input').fill('Вопрос');
    await page.locator('#chat-input').press('Enter');
    await expect(page.getByText('Собираю данные')).toBeVisible();
    await page.getByRole('button', { name: 'Сбросить чат' }).click();
    await expect(page.locator('#agent-progress')).toBeHidden();
    const before = progress.length;
    release();
    await finished;
    await page.clock.fastForward(2_000);
    expect(progress).toHaveLength(before);
    await expect(page.locator('#chat-log')).toBeEmpty();
    await expect.poll(() => page.evaluate(() => sessionStorage.getItem('agentSessionId'))).toBeNull();
  } finally { release(); }
});

test('query catalog opens a saved query and executes it over MCP API', async ({ page }) => {
  await mockApi(page);
  await page.goto('/queries');
  await page.getByText('Сделки на паузе').click();
  await page.getByRole('button', { name: 'Выполнить запрос' }).click();
  await expect(page.getByText(/urn:deal:1/)).toBeVisible();
});

test('query catalog submits parameter values as strings', async ({ page }) => {
  await mockApi(page);
  let submitted: Record<string, unknown> | undefined;
  await page.route('**/api/agent/query', async (route) => {
    submitted = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ rows: [] }) });
  });
  await page.goto('/queries');
  await page.getByText('Период по параметрам').click();
  await page.getByLabel('enabled').selectOption('true');
  await page.getByLabel('from').fill('2026-11-24');
  await page.getByLabel('at').fill('14:25');
  await page.getByRole('button', { name: 'Выполнить запрос' }).click();
  await expect.poll(() => submitted).toEqual({
    query: parameterizedQuery,
    parameters: { enabled: 'true', from: '2026-11-24', at: '14:25:00' },
  });
});

test('the catalog preserves a query after cancel or denied deletion and removes it after success', async ({ page }) => {
  await mockApi(page);
  let deleted = false;
  let attempts = 0;
  await page.route('**/api/published-queries', async (route) => {
    if (!deleted) return route.fallback();
    await route.fulfill({ json: { queries: [], admin_enabled: true } });
  });
  await page.route('**/api/published-queries/q1', (route) => {
    if (++attempts === 1) return route.fulfill({ status: 401, json: { error: 'Требуется токен администратора' } });
    deleted = true;
    return route.fulfill({ json: { deleted: true } });
  });
  await page.goto('/queries');
  await page.getByText('Сделки на паузе').click();
  const remove = page.getByRole('button', { name: 'Удалить', exact: true });
  page.once('dialog', (dialog) => dialog.dismiss());
  await remove.click();
  expect(attempts).toBe(0);
  page.once('dialog', (dialog) => dialog.accept());
  await remove.click();
  await expect(page.getByText('Требуется токен администратора')).toBeVisible();
  await expect(page.getByText('Сделки на паузе')).toBeVisible();
  page.once('dialog', (dialog) => dialog.accept());
  await remove.click();
  await expect(page.getByText(/Опубликованных запросов пока нет/)).toBeVisible();
  expect(attempts).toBe(2);
});

test('query debugger remains at its tools alias', async ({ page }) => {
  await mockApi(page);
  await page.goto('/queries');
  await page.getByText('Сделки на паузе').click();
  await page.getByRole('button', { name: 'Открыть в отладчике' }).click();
  await expect(page).toHaveURL('/tools/queries');
  await expect(page.getByRole('heading', { name: 'Сохранённые SPARQL-запросы' })).toBeVisible();
  await expect(page.getByLabel('Редактор SPARQL')).toHaveValue(/SELECT/);
});

test('editing a parameter invalidates verification before publishing the catalog draft', async ({ page }) => {
  await mockApi(page);
  const requests: unknown[] = [];
  await page.route('**/api/published-queries', (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    requests.push(route.request().postDataJSON());
    return route.fulfill({ json: { id: 'published' }, status: 201 });
  });
  await page.goto('/published-queries');
  await page.getByText('Период по параметрам').click();
  await page.getByRole('button', { name: 'Открыть в отладчике' }).click();
  await expect(page.locator('#saved-query-editor')).toHaveValue(parameterizedQuery);
  await page.getByLabel('enabled').selectOption('true');
  await page.getByLabel('from').fill('2026-11-24');
  await page.getByLabel('at').fill('14:25');
  await page.locator('#query-run').click();
  await expect(page.locator('#query-publish-panel')).toBeVisible();
  await page.getByLabel('enabled').selectOption('false');
  await expect(page.locator('#query-publish-panel')).toBeHidden();
  await page.locator('#query-run').click();
  await page.getByText('Опубликовать проверенный запрос в каталоге').click();
  await page.locator('#query-admin-token').fill('demo-token');
  await page.locator('#query-publish').click();
  await expect(page.getByText('Параметризованный шаблон опубликован.', { exact: false })).toBeVisible();
  expect(requests).toEqual([{ title: 'Период по параметрам', description: '', query: parameterizedQuery }]);
});

test('ontology editor loads TTL and validates without publishing it', async ({ page }) => {
  await mockApi(page);
  await page.goto('/tools/ontology');
  await page.getByRole('button', { name: /sales\.ttl/ }).click();
  await expect(page.getByLabel('Редактор Turtle')).toHaveValue(/urn:bitrix24:ontology:sales/);
  await page.getByRole('button', { name: 'Проверить', exact: true }).click();
  await expect(page.getByText(/Turtle корректен/)).toBeVisible();
});

test('ontology preserves editor text on a SHA conflict and uses the confirmed delete preview', async ({ page }) => {
  await mockApi(page);
  const deletions: unknown[] = [];
  await page.route('**/api/ontologies/sales', async (route) => {
    if (route.request().method() !== 'PUT') return route.fallback();
    expect(route.request().postDataJSON()).toMatchObject({ expected_sha256: 'abc123' });
    await route.fulfill({ status: 409, json: { error: 'Версия файла изменилась' } });
  });
  await page.route('**/api/ontologies/sales/delete', async (route) => {
    deletions.push(route.request().postDataJSON());
    await route.fulfill({ json: { graph_deleted: true, expires_at: '2026-12-01T00:00:00Z' } });
  });
  await page.goto('/tools/ontology');
  await page.getByRole('button', { name: 'sales.ttl' }).click();
  const editor = page.getByRole('textbox', { name: 'Редактор Turtle' });
  await expect(editor).toHaveValue(/owl:Ontology/);
  const original = await editor.inputValue();
  await editor.fill(original + '\n# local edit');
  await page.locator('#save').click();
  await expect(page.getByText('Версия файла изменилась')).toBeVisible();
  await expect(editor).toHaveValue(original + '\n# local edit');
  await editor.fill(original);
  await page.locator('#delete-ontology').click();
  const dialog = page.getByRole('dialog', { name: 'Подтвердить удаление' });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('#confirm-delete-ontology')).toBeDisabled();
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  expect(deletions).toHaveLength(0);
  await page.locator('#delete-ontology').click();
  await dialog.getByLabel('Введите slug для подтверждения').fill('sales');
  await dialog.getByRole('button', { name: 'Удалить в карантин' }).click();
  await expect(page.getByText('Онтология помещена в карантин.')).toBeVisible();
  expect(deletions).toEqual([{ graph_fingerprint: 'demo-fingerprint', revision_count: 2, graph_present: true, quarantine_days: 7 }]);
});

test('ontology applies an assistant proposal only to the editor and rejects a stale proposal', async ({ page }) => {
  await mockApi(page);
  const proposed = '<urn:bitrix24:ontology:sales> a <http://www.w3.org/2002/07/owl#Ontology> .\n# suggested';
  let serverFingerprint = 'graph-v1';
  const writes: string[] = [];
  page.on('request', (request) => {
    if (request.method() !== 'GET' && new URL(request.url()).pathname.startsWith('/api/ontologies')) {
      writes.push(request.url());
    }
  });
  await page.route('**/api/ontology-assistant/suggest', (route) => {
    const { slug, editor_sha256, file_sha256 } = route.request().postDataJSON();
    return route.fulfill({ json: {
      slug, editor_sha256, file_sha256, model: 'Demo', summary: 'Предложение проверено',
      published_schema_fingerprint: 'graph-v1', published_schema_status: 'published',
      proposed_ttl: proposed, applicable: true,
    } });
  });
  await page.route('**/api/ontology-assistant/version?slug=sales', (route) => route.fulfill({ json: {
    file_sha256: 'abc123', published_schema_fingerprint: serverFingerprint, published_status: 'published',
  } }));
  await page.goto('/ontologies.html');
  await page.getByRole('button', { name: 'sales.ttl' }).click();
  const editor = page.getByRole('textbox', { name: 'Редактор Turtle' });
  const apply = page.getByRole('button', { name: 'Применить в редактор' });
  await expect(editor).toHaveValue(/owl:Ontology/);
  const original = await editor.inputValue();
  await page.getByRole('button', { name: 'Создать аксиому' }).click();
  await expect(apply).toBeEnabled();
  serverFingerprint = 'graph-v2';
  await apply.click();
  await expect(page.getByText(/Опубликованная схема изменилась/)).toBeVisible();
  await expect(editor).toHaveValue(original);
  serverFingerprint = 'graph-v1';
  await page.getByRole('button', { name: 'Пересчитать по текущему тексту' }).click();
  await apply.click();
  await expect(editor).toHaveValue(proposed);
  await expect(page.locator('#publish-status')).toContainText('есть несохранённые изменения');
  expect(writes).toEqual([]);
});

test('ontology imports, saves, publishes, rolls back and loads class statistics', async ({ page }) => {
  await mockApi(page);
  const ttl = '<urn:bitrix24:ontology:smoke> a <http://www.w3.org/2002/07/owl#Ontology> .';
  const file = { slug: 'smoke', filename: 'smoke.ttl', uri: 'urn:bitrix24:ontology:smoke', sha256: 'saved-sha', text: ttl };
  await page.route('**/api/ontologies', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    expect(route.request().postDataJSON()).toEqual({ slug: 'smoke', text: ttl });
    await route.fulfill({ json: file, status: 201 });
  });
  await page.route('**/api/ontologies/smoke/publish', async (route) => {
    expect(route.request().postDataJSON()).toEqual({ expected_sha256: 'saved-sha' });
    await route.fulfill({ json: { sha256: 'saved-sha', message: 'Граф опубликован' } });
  });
  await page.route('**/api/ontologies/smoke/rollback', (route) => route.fulfill({ json: {
    sha256: 'old-sha', text: ttl + '\n# old',
  } }));
  await page.route('**/api/ontologies/class-statistics', (route) => route.fulfill({ json: {
    namespace: 'https://example.org/bitrix24#', class_count: 1,
    classes: [{ uri: 'https://example.org/bitrix24#Deal', name: 'Deal', label_ru: 'Сделка', features: [], instance_count: 5 }],
  } }));
  await page.goto('/tools');
  await page.locator('#file-input').setInputFiles({ name: 'smoke.ttl', mimeType: 'text/turtle', buffer: Buffer.from(ttl) });
  await expect(page.locator('#new-slug')).toHaveValue('smoke');
  await page.locator('#create').click();
  await expect(page.getByText('Новый файл сохранён. Граф не изменён.')).toBeVisible();
  await page.locator('#publish').click();
  await expect(page.locator('#publish-status')).toContainText('saved-sha');
  page.once('dialog', (dialog) => dialog.accept());
  await page.locator('#rollback').click();
  await expect(page.locator('#editor')).toHaveValue(ttl + '\n# old');
  await page.locator('#class-statistics-namespace').fill('https://example.org/bitrix24#');
  await page.locator('#class-statistics-submit').click();
  await expect(page.getByRole('heading', { name: 'Сделка', exact: true })).toBeVisible();
});

test('mobile layout stays within the viewport', async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'CRM Pulse' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(391);
});

for (const width of [1440, 768, 390]) {
test(`React and legacy render the same pages at ${width}px`, async ({ context, page }, testInfo) => {
  const legacy = await context.newPage();
  const tabs = [page, legacy];
  await Promise.all(tabs.map(async (tab) => {
    await tab.setViewportSize({ width, height: 900 });
    await tab.clock.setFixedTime(new Date('2026-11-25T09:00:00Z'));
  }));
  const pages = [
    ['/', 'index.html', 'Проверить задержки'],
    ['/leads', 'leads.html', 'Лид для проверки'],
    ['/employees', 'employees.html', 'Анна'],
    ['/agent', 'agent.html', 'Сохранённые промпты'],
    ['/queries', 'published_queries.html', 'Сделки на паузе'],
    ['/tools/queries', 'queries.html', 'Сохранённых запросов пока нет.'],
    ['/tools/ontology', 'ontologies.html', 'sales.ttl'],
  ];
  const filenames = new Map(pages.map(([url, filename]) => [url, filename]));
  const staticPath = (name: string) => fileURLToPath(new URL(`../../static/${name}`, import.meta.url));
  await legacy.route('**/*', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const filename = filenames.get(path)
      ?? (/^\/(?:vendor\/)?[\w.]+\.(?:js|css)$/.test(path) ? path.slice(1) : undefined);
    if (filename) await route.fulfill({ path: staticPath(filename) });
    else await route.fallback();
  });
  await Promise.all([mockApi(page), mockApi(legacy)]);

  async function compare(name: string) {
    const filePrefix = name.replace(/[^a-z0-9-]+/gi, '-').replace(/^-/, '') || 'home';
    const [actual, expected] = await Promise.all(tabs.map(
      (tab, index) => tab.locator('main').screenshot({
        animations: 'disabled',
        path: testInfo.outputPath(`${filePrefix}-${index === 0 ? 'react' : 'legacy'}.png`),
      }),
    ));
    if (actual.equals(expected)) return;
    const difference = await page.evaluate(async ([react, legacy]) => {
      async function decode(base64: string) {
        const image = new Image();
        image.src = `data:image/png;base64,${base64}`;
        await image.decode();
        const canvas = document.createElement('canvas');
        canvas.width = image.width;
        canvas.height = image.height;
        const context = canvas.getContext('2d')!;
        context.drawImage(image, 0, 0);
        return { canvas, context, pixels: context.getImageData(0, 0, canvas.width, canvas.height) };
      }
      const [actual, expected] = await Promise.all([decode(react), decode(legacy)]);
      const dimensions = (image: ImageData) => `${image.width}×${image.height}`;
      if (dimensions(actual.pixels) !== dimensions(expected.pixels)) {
        return { dimensions: [dimensions(actual.pixels), dimensions(expected.pixels)], pixels: -1 };
      }
      let pixels = 0;
      const output = actual.context.createImageData(actual.pixels.width, actual.pixels.height);
      for (let i = 0; i < output.data.length; i += 4) {
        const changed = [0, 1, 2].some((channel) =>
          Math.abs(actual.pixels.data[i + channel] - expected.pixels.data[i + channel]) > 4);
        if (changed) pixels++;
        output.data.set(changed ? [255, 30, 60, 255] : [255, 255, 255, 255], i);
      }
      actual.context.putImageData(output, 0, 0);
      return { pixels, diff: actual.canvas.toDataURL().split(',')[1] };
    }, [actual.toString('base64'), expected.toString('base64')]);
    // Chromium can round a few edge pixels differently during rasterization.
    if (difference.pixels < 0 || difference.pixels > 2) {
      if (difference.diff) await testInfo.attach(`${name} diff`, {
        body: Buffer.from(difference.diff, 'base64'), contentType: 'image/png',
      });
      expect(difference.dimensions?.[0], `Screenshot dimensions for ${name}`).toBe(difference.dimensions?.[1]);
      expect(difference.pixels, `Different pixels in ${name}`).toBeLessThanOrEqual(2);
    }
  }

  for (const [url, , ready] of pages) {
    await test.step(url, async () => {
      await Promise.all([page.goto(url), legacy.goto(url)]);
      await Promise.all([page, legacy].map((tab) => expect(tab.getByText(ready).first()).toBeVisible()));
      if (url === '/tools/ontology') {
        await Promise.all([page, legacy].map((tab) => expect(tab.locator('#assistant-state')).toHaveText('Готов')));
      }
      if (url === '/') await Promise.all(tabs.map((tab) => expect(tab.locator('#active-deals')).toHaveText('42')));
      if (url === '/leads' || url === '/employees') {
        await Promise.all(tabs.map((tab) => expect(tab.getByText('Проверить задержки')).toBeVisible()));
      }
      if (url === '/leads') {
        await Promise.all(tabs.map((tab) => expect(tab.locator('#lead-conversion-category option')).toHaveCount(3)));
      }
      if (url === '/employees') {
        await Promise.all(tabs.map((tab) => expect(tab.locator('#employee-activities-count')).toHaveText('1')));
      }
      await compare(url);
      if (url === '/') {
        await Promise.all(tabs.map(async (tab) => {
          await tab.locator('#toggle-red').click();
          await tab.locator('#toggle-activities').click();
          await expect(tab.locator('#red-table')).toBeVisible();
          await expect(tab.locator('#activities-table')).toBeVisible();
        }));
        await compare('deals lists');
        await Promise.all(tabs.map(async (tab) => {
          await tab.getByRole('button', { name: 'Сводки' }).click();
          await expect(tab.locator('#activity-distribution-chart')).toBeVisible();
        }));
        await compare('deals distribution');
      }
      if (url === '/leads') {
        await Promise.all(tabs.map(async (tab) => {
          await tab.getByRole('button', { name: 'Сводки' }).click();
          await expect(tab.locator('#sla-trend-chart')).toBeVisible();
          await expect(tab.locator('#conversion-trend-chart')).toBeVisible();
        }));
        await compare('lead charts');
      }
      if (url === '/employees') {
        await Promise.all(tabs.map(async (tab) => {
          await tab.locator('#toggle-employee-activities').click();
          await expect(tab.locator('#employee-activities-table')).toBeVisible();
        }));
        await compare('employee activities');
        await Promise.all(tabs.map(async (tab) => {
          await tab.getByRole('button', { name: 'Сводка' }).click();
          await expect(tab.locator('#employee-pulse-chart')).toBeVisible();
        }));
        await compare('employee charts');
      }
      if (url === '/queries') {
        await Promise.all(tabs.map(async (tab) => {
          await tab.getByText('Сделки на паузе').click();
          await tab.getByRole('button', { name: 'Выполнить запрос' }).click();
          await expect(tab.getByText('urn:deal:1')).toBeVisible();
        }));
        await compare('catalog result');
      }
      if (url === '/agent') {
        await Promise.all(tabs.map(async (tab) => {
          await tab.evaluate(() => sessionStorage.setItem('agentTranscript', JSON.stringify([
            { role: 'user', content: 'Покажи сделки' },
            { role: 'assistant', content: '## Сделки\n\n| Название |\n| --- |\n| Пример |' },
          ])));
          await tab.reload();
          await expect(tab.getByRole('cell', { name: 'Пример' })).toBeVisible();
        }));
        await compare('agent answer');
      }
      if (url === '/tools/ontology') {
        await Promise.all(tabs.map(async (tab) => {
          await tab.getByRole('button', { name: 'sales.ttl' }).click();
          await expect(tab.getByRole('textbox', { name: 'Редактор Turtle' })).toHaveValue(/owl:Ontology/);
          await tab.locator('#delete-ontology').click();
          await expect(tab.getByRole('dialog')).toBeVisible();
        }));
        await compare('ontology delete dialog');
      }
    });
  }
});
}
