import { expect, test, type Page } from '@playwright/test';

const categories = [{ id: '5', name: 'Входящие' }, { id: '9', name: 'Партнёры' }];
const snapshotTime = '2026-11-25T09:35:00Z';
const pulse = { available: true, as_of: snapshotTime, summary: { active_deals: 42, created_24h: 3, created_7d: 12, created_30d: 34, red_deals_count: 4 }, categories, employees: [{ name: 'Анна', active_deals: 8, activities_24h: 2 }] };
const leadPulse = { available: true, as_of: snapshotTime, summary: { created_1d: 6, created_7d: 18, created_30d: 70, average_response_minutes: 42, sla_within_2h_ratio: 0.75, sla_eligible_count: 12 }, response_trend: [{ date: '2026-11-20', sla_within_2h_ratio: 0.75 }], red_leads: [{ title: 'Лид для проверки', manager: 'Анна' }] };
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
  await expect(page.getByRole('heading', { name: 'Пульс сделок', level: 1 })).toBeVisible();
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

const dashboardPages = [
  ['/', 'deals', 'Пульс сделок', 'Проверить задержки'],
  ['/leads', 'leads', 'Пульс лидов', 'Лид для проверки'],
  ['/employees', 'employees', 'Сотрудники', 'Анна'],
  ['/agent', 'agent', 'AI Agent', 'Сохранённые промпты'],
  ['/queries', 'catalog', 'Запросы', 'Сделки на паузе'],
  ['/tools/queries', 'debugger', 'Инструменты', 'Сохранённых запросов пока нет.'],
  ['/tools/ontology', 'ontology', 'Инструменты', 'sales.ttl'],
] as const;

test('sidebar links and tool sections follow direct URLs and browser history', async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  const navigation = page.getByRole('navigation', { name: 'Страницы дашборда' });
  const tools = page.getByRole('navigation', { name: 'Инструменты' });
  await page.goto('/index.html');
  await expect(navigation.getByRole('link')).toHaveCount(6);
  const deals = navigation.getByRole('link', { name: 'Пульс сделок', exact: true });
  const leads = navigation.getByRole('link', { name: 'Пульс лидов', exact: true });
  await page.keyboard.press('Tab');
  await expect(deals).toBeFocused();
  await expect(deals).toHaveCSS('outline-style', 'solid');
  await page.keyboard.press('Tab');
  await expect(leads).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(deals).toBeFocused();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL('/leads');
  await expect(leads).toHaveAttribute('aria-current', 'page');
  await page.goBack();
  await expect(page).toHaveURL('/index.html');
  await expect(deals).toHaveAttribute('aria-current', 'page');
  await page.goForward();
  await expect(leads).toHaveAttribute('aria-current', 'page');

  const aliases = [
    ['/leads.html', 'Пульс лидов'], ['/employees.html', 'Сотрудники'], ['/agent.html', 'AI Agent'],
    ['/published-queries', 'Запросы'], ['/ontologies', 'Инструменты'],
    ['/ontologies.html', 'Инструменты'], ['/tools', 'Инструменты'],
  ];
  for (const [url, label] of aliases) {
    await page.goto(url);
    await expect(navigation.locator('[aria-current="page"]')).toHaveText(label);
  }
  const ontology = tools.getByRole('link', { name: 'Онтология' });
  const debuggerLink = tools.getByRole('link', { name: 'Запросы' });
  await expect(ontology).toHaveAttribute('aria-current', 'page');
  await debuggerLink.click();
  await expect(page).toHaveURL('/tools/queries');
  await page.reload();
  await expect(debuggerLink).toHaveAttribute('aria-current', 'page');
  await ontology.click();
  await expect(page).toHaveURL('/tools/ontology');
  await expect(ontology).toHaveAttribute('aria-current', 'page');
});

for (const width of [1440, 390]) {
  test(`analytics header follows reading order and reports data status at ${width}px`, async ({ page }) => {
    await mockApi(page);
    await page.setViewportSize({ width, height: 900 });
    const initialRequest = Promise.withResolvers<void>();
    let failed = false;
    await page.route('**/api/pulse?*', async (route) => {
      await initialRequest.promise;
      await route.fulfill(failed
        ? { status: 503, json: { error: 'База недоступна' } }
        : { json: { ...pulse, categories: [{ id: '5', name: 'Очень длинное название воронки для проверки адаптивной шапки' }] } });
    });
    await page.goto('/');
    const header = page.locator('.analytics-header');
    const status = header.getByRole('status');
    const time = header.locator('time');
    const refresh = header.getByRole('button', { name: 'Обновить данные' });
    await expect(status).toHaveText('Подключение…');
    await expect(time).toHaveCount(0);
    await expect(refresh).toBeDisabled();
    initialRequest.resolve();
    await expect(status).toHaveText('GraphDB подключён');
    await expect(time).toHaveAttribute('datetime', snapshotTime);
    await expect(refresh).toBeEnabled();

    const logout = header.getByRole('link', { name: 'Выйти' });
    const filter = header.getByRole('combobox');
    const tabs = header.getByRole('navigation').getByRole('button');
    await expect(logout).toHaveAttribute('href', '/auth/logout');
    await logout.focus();
    const inOneRow = await page.evaluate(() => CSS.supports('reading-flow', 'grid-rows')) && width === 1440;
    for (const control of inOneRow
      ? [tabs.nth(0), tabs.nth(1), filter, refresh]
      : [filter, refresh, tabs.nth(0), tabs.nth(1)]) {
      await page.keyboard.press('Tab');
      await expect(control).toBeFocused();
    }

    await filter.selectOption('5');
    await expect(filter).toHaveValue('5');
    await expect(status).toHaveText('GraphDB подключён');
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    failed = true;
    await refresh.click();
    await expect(status).toHaveText('Нет соединения');
    await expect(time).toHaveAttribute('datetime', snapshotTime);
  });
}

for (const width of [1440, 768, 390, 320]) {
  for (const [url, name, label, ready] of dashboardPages) {
    test(`dark React layout for ${name} at ${width}px`, { tag: '@visual' }, async ({ page }, testInfo) => {
      test.setTimeout(60_000);
      await mockApi(page);
      await page.setViewportSize({ width, height: 900 });
      await page.clock.setFixedTime(new Date('2026-11-25T09:00:00Z'));
      const errors: string[] = [];
      page.on('pageerror', error => errors.push(error.message));

      async function verifyLayout() {
        const sidebar = page.locator('.app-sidebar');
        const main = page.locator('main');
        await expect(main).toBeVisible();
        await expect(page.locator('html')).toHaveCSS('color-scheme', 'dark');
        const sidebarBox = (await sidebar.boundingBox())!;
        const mainBox = (await main.boundingBox())!;
        const sidebarWidth = width >= 1120 ? 224 : width >= 768 ? 168 : 64;
        expect(sidebarBox.width).toBe(sidebarWidth);
        expect(sidebarBox.x + sidebarBox.width).toBeLessThanOrEqual(mainBox.x);
        const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
        expect(scrollWidth).toBeLessThanOrEqual(width);
      }

      async function capture(name: string) {
        await verifyLayout();
        if (width === 320) return;
        const filename = `${name}-${width}.png`;
        if (process.env.PLAYWRIGHT_COMPARE_SCREENSHOTS === '1') {
          await expect(page).toHaveScreenshot(filename, { maxDiffPixelRatio: 0.002, timeout: 15_000 });
          return;
        }
        await page.screenshot({
          animations: 'disabled',
          path: testInfo.outputPath(filename),
        });
      }

      await page.goto(url);
      await expect(page.getByText(ready).first()).toBeVisible();
      const nav = page.getByRole('navigation', { name: 'Страницы дашборда' });
      await expect(nav.locator('[aria-current="page"]')).toHaveText(label);
      if (name === 'deals') await expect(page.locator('#active-deals')).toHaveText('42');
      if (name === 'leads') await expect(page.locator('#lead-conversion-category option')).toHaveCount(3);
      if (name === 'employees') await expect(page.locator('#employee-activities-count')).toHaveText('1');
      if (name === 'ontology') await expect(page.locator('#assistant-state')).toHaveText('Готов');
      await capture(name);

      if (name === 'deals') {
        await page.locator('#toggle-red').click();
        await page.locator('#toggle-activities').click();
        await expect(page.locator('#red-table')).toBeVisible();
        await expect(page.locator('#activities-table')).toBeVisible();
        await capture('deals-lists');
        await page.getByRole('button', { name: 'Сводки' }).click();
        await expect(page.locator('#activity-distribution-chart')).toBeVisible();
        await capture('deals-distribution');
      }

      if (name === 'leads') {
        await page.getByRole('button', { name: 'Сводки' }).click();
        await expect(page.locator('#sla-trend-chart')).toBeVisible();
        await expect(page.locator('#conversion-trend-chart')).toBeVisible();
        await capture('lead-charts');
      }

      if (name === 'employees') {
        await page.locator('#toggle-employee-activities').click();
        await expect(page.locator('#employee-activities-table')).toBeVisible();
        await page.getByRole('button', { name: 'Сводка', exact: true }).click();
        await expect(page.locator('#employee-pulse-chart')).toBeVisible();
        await capture('employee-charts');
      }

      if (name === 'catalog') {
        await page.getByText('Сделки на паузе').click();
        await page.getByRole('button', { name: 'Выполнить запрос' }).click();
        await expect(page.getByText('urn:deal:1')).toBeVisible();
        await capture('catalog-result');
      }

      if (name === 'agent') {
        await page.evaluate(() => sessionStorage.setItem('agentTranscript', JSON.stringify([
          { role: 'user', content: 'Покажи сделки' },
          { role: 'assistant', content: '## Сделки\n\n| Название |\n| --- |\n| Пример |' },
        ])));
        await page.reload();
        await expect(page.getByRole('cell', { name: 'Пример' })).toBeVisible();
        await capture('agent-answer');
        if (width === 390) {
          const download = page.waitForEvent('download');
          await page.getByRole('button', { name: 'Скачать CSV' }).click();
          expect((await download).suggestedFilename()).toMatch(/\.csv$/);
        }
      }

      if (name === 'ontology') {
        await page.getByRole('button', { name: 'sales.ttl' }).click();
        await expect(page.getByRole('textbox', { name: 'Редактор Turtle' })).toHaveValue(/owl:Ontology/);
        const trigger = page.locator('#delete-ontology');
        await trigger.click();
        const dialog = page.getByRole('dialog', { name: 'Подтвердить удаление' });
        await expect(dialog).toBeInViewport();
        await capture('ontology-delete-dialog');
        await page.keyboard.press('Escape');
        await expect(dialog).toBeHidden();
        await expect(trigger).toBeFocused();
      }
      expect(errors).toEqual([]);
    });
  }
}
