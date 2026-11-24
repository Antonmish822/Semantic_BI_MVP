const byId = (id) => document.getElementById(id);
const STORAGE_KEY = 'agentSavedPrompts';
const listNode = byId('saved-query-list');
const countNode = byId('saved-query-count');
const editor = byId('saved-query-editor');
const parametersNode = byId('query-parameters');
const publishPanel = byId('query-publish-panel');
const publishButton = byId('query-publish');
const publishTokenInput = byId('query-admin-token');
const promptNode = byId('selected-query-prompt');
const runButton = byId('query-run');
const saveButton = byId('query-save');
const copyButton = byId('query-copy');
const resultNode = byId('query-result');
const tableModeButton = byId('query-result-table-mode');
const jsonModeButton = byId('query-result-json-mode');
const statusNode = byId('query-editor-status');
const noticeNode = byId('query-tool-notice');

let allSavedPrompts = [];
let savedQueries = [];
let selectedPrompt = null;
let dirty = false;
let resultView = 'table';
let lastResult = null;
let selectedIsPublishedDraft = false;
let lastDebugPassed = false;
let lastDebugQuery = '';
let lastDebugParameters = null;
const PUBLISH_TOKEN_KEY = 'crm-pulse-tools-admin-token';
const PUBLISHED_DRAFT_KEY = 'agentPublishedQueryDraft';

try { publishTokenInput.value = localStorage.getItem(PUBLISH_TOKEN_KEY) || ''; } catch (_) {}
publishTokenInput.addEventListener('input', () => {
  try {
    if (publishTokenInput.value) localStorage.setItem(PUBLISH_TOKEN_KEY, publishTokenInput.value);
    else localStorage.removeItem(PUBLISH_TOKEN_KEY);
  } catch (_) {}
});

function readSavedPrompts() {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
    if (!Array.isArray(value)) return [];
    return value.map((item) => {
      if (typeof item === 'string') return { prompt: item.trim(), sparql: '' };
      if (!item || typeof item.prompt !== 'string') return null;
      return { prompt: item.prompt.trim(), sparql: typeof item.sparql === 'string' ? item.sparql : '' };
    }).filter((item) => item && item.prompt);
  } catch {
    return [];
  }
}

function refreshQueries() {
  allSavedPrompts = readSavedPrompts();
  try {
    const draft = JSON.parse(sessionStorage.getItem(PUBLISHED_DRAFT_KEY) || 'null');
    if (draft && typeof draft.id === 'string' && typeof draft.title === 'string' && typeof draft.query === 'string') {
      allSavedPrompts.push({
        prompt: `Опубликованный запрос: ${draft.title} · ${draft.id}`,
        sparql: draft.query,
        publishedTitle: draft.title,
        publishedDraft: true,
      });
    }
  } catch (_) {}
  savedQueries = allSavedPrompts.filter((item) => item.sparql.trim());
  countNode.textContent = String(savedQueries.length);
  listNode.replaceChildren();
  if (!savedQueries.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-state';
    empty.textContent = 'Сохранённых запросов пока нет.';
    listNode.append(empty);
    return;
  }
  savedQueries.forEach((item) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `saved-query-select${selectedPrompt === item.prompt ? ' active' : ''}`;
    button.setAttribute('aria-pressed', String(selectedPrompt === item.prompt));
    const prompt = document.createElement('span');
    prompt.className = 'saved-query-select-prompt';
    prompt.textContent = item.prompt;
    const detail = document.createElement('span');
    detail.className = 'saved-query-select-detail';
    detail.textContent = `${item.sparql.split(/\r?\n/).length} строк SPARQL`;
    button.append(prompt, detail);
    button.addEventListener('click', () => selectQuery(item.prompt));
    listNode.append(button);
  });
}

function showNotice(message, isError = false) {
  noticeNode.hidden = !message;
  noticeNode.textContent = message;
  noticeNode.classList.toggle('query-notice-success', Boolean(message) && !isError);
}

function setDirty(value) {
  dirty = value;
  saveButton.disabled = selectedIsPublishedDraft || !selectedPrompt || !dirty;
  statusNode.textContent = dirty ? 'Есть несохранённые изменения.' : '';
}

function selectQuery(prompt) {
  if (dirty && !window.confirm('Отбросить несохранённые изменения текущего запроса?')) return;
  const selected = savedQueries.find((item) => item.prompt === prompt);
  if (!selected) return;
  selectedPrompt = selected.prompt;
  selectedIsPublishedDraft = Boolean(selected.publishedDraft);
  if (selectedIsPublishedDraft) byId('query-publish-title').value = selected.publishedTitle || '';
  editor.value = selected.sparql;
  parametersNode.replaceChildren();
  renderParameterFields();
  editor.disabled = false;
  runButton.disabled = false;
  copyButton.disabled = false;
  promptNode.className = 'query-linked-prompt';
  promptNode.textContent = selectedIsPublishedDraft ? selected.prompt : `Промпт: ${selected.prompt}`;
  resultNode.textContent = 'Запрос ещё не запускался.';
  lastResult = null;
  lastDebugPassed = false;
  lastDebugQuery = '';
  lastDebugParameters = null;
  publishPanel.hidden = true;
  publishButton.disabled = true;
  showNotice('');
  setDirty(false);
  refreshQueries();
}

function queryParameterNames(query) {
  return [...new Set([...query.matchAll(/\{\{([A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*)\}\}/g)]
    .map((match) => match[1]))];
}

function renderParameterFields() {
  const previousValues = Object.fromEntries(
    [...parametersNode.querySelectorAll('[data-query-parameter]')]
      .map((input) => [input.dataset.queryParameter, input.value]),
  );
  let parameters;
  try { parameters = QueryParameters.describe(editor.value); }
  catch (error) {
    parametersNode.hidden = false;
    parametersNode.textContent = error.message;
    return;
  }
  parametersNode.replaceChildren();
  parametersNode.hidden = !parameters.length;
  if (!parameters.length) return;

  const heading = document.createElement('h3');
  heading.textContent = 'Параметры запроса';
  const hint = document.createElement('p');
  hint.className = 'query-parameter-hint';
  hint.textContent = 'Тип поля определяется по xsd-типу рядом с параметром. Дата и время отправляются с часовым поясом UTC; значения экранируются перед подстановкой.';
  const fields = document.createElement('div');
  fields.className = 'query-parameter-fields';
  parameters.forEach(({ name, type }, index) => {
    const field = document.createElement('label');
    field.className = 'query-parameter-field';
    field.htmlFor = `query-parameter-${index}`;
    const title = document.createElement('span');
    title.textContent = name;
    const input = type === 'boolean' ? document.createElement('select') : document.createElement('input');
    input.id = `query-parameter-${index}`;
    input.dataset.queryParameter = name;
    input.dataset.parameterType = type;
    if (type === 'boolean') {
      for (const value of ['true', 'false']) {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = value;
        input.append(option);
      }
      input.value = previousValues[name] || '';
      if (!previousValues[name]) input.selectedIndex = -1;
    } else {
      input.type = QueryParameters.inputType(type);
      const step = QueryParameters.step(type);
      if (step) input.step = step;
      input.value = previousValues[name] || '';
    }
    input.required = true;
    input.autocomplete = 'off';
    field.append(title, input);
    fields.append(field);
  });
  parametersNode.append(heading, hint, fields);
}

function collectParameterValues() {
  const inputs = [...parametersNode.querySelectorAll('[data-query-parameter]')];
  const values = {};
  for (const input of inputs) {
    if (!input.value.trim()) {
      input.focus();
      throw new Error(`Заполните параметр «${input.dataset.queryParameter}»`);
    }
    values[input.dataset.queryParameter] = QueryParameters.valueForApi(input);
  }
  return values;
}

function saveEditedQuery() {
  if (!selectedPrompt) return;
  if (selectedIsPublishedDraft) {
    showNotice('Опубликованный шаблон доступен только для чтения здесь. Сохраните копию как промпт в AI Agent.', true);
    return;
  }
  const source = readSavedPrompts();
  const item = source.find((saved) => saved.prompt === selectedPrompt);
  if (!item) {
    showNotice('Связанный промпт больше не найден в хранилище браузера.', true);
    return;
  }
  item.sparql = editor.value;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(source));
    setDirty(false);
    showNotice('Изменения запроса сохранены вместе с промптом.');
    refreshQueries();
  } catch {
    showNotice('Не удалось записать изменения в хранилище браузера.', true);
  }
}

async function copyEditedQuery() {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(editor.value);
    } else {
      const temporaryInput = document.createElement('textarea');
      temporaryInput.value = editor.value;
      temporaryInput.setAttribute('readonly', '');
      temporaryInput.style.position = 'fixed';
      temporaryInput.style.opacity = '0';
      document.body.append(temporaryInput);
      temporaryInput.select();
      const copied = document.execCommand('copy');
      temporaryInput.remove();
      if (!copied) throw new Error('Clipboard unavailable');
    }
    statusNode.textContent = 'SPARQL скопирован.';
  } catch {
    statusNode.textContent = 'Не удалось скопировать запрос. Проверьте разрешение браузера на буфер обмена.';
  }
}

function renderResult(result) {
  lastResult = result;
  QueryResultView.render(resultNode, result, resultView, {
    filename: 'query-debug-results',
    onError: (error) => { statusNode.textContent = error?.message || 'Не удалось скачать Excel'; },
  });
}

function setResultView(view) {
  resultView = view;
  tableModeButton.classList.toggle('active', view === 'table');
  tableModeButton.setAttribute('aria-pressed', String(view === 'table'));
  jsonModeButton.classList.toggle('active', view === 'json');
  jsonModeButton.setAttribute('aria-pressed', String(view === 'json'));
  if (lastResult !== null) renderResult(lastResult);
}

async function runQuery() {
  const query = editor.value.trim();
  if (!query) {
    statusNode.textContent = 'Введите SPARQL перед запуском.';
    return;
  }
  runButton.disabled = true;
  saveButton.disabled = true;
  statusNode.textContent = 'Выполняю через MCP…';
  lastDebugPassed = false;
  lastDebugQuery = '';
  lastDebugParameters = null;
  publishPanel.hidden = true;
  publishButton.disabled = true;
  lastResult = null;
  resultNode.textContent = '';
  const requestId = globalThis.crypto?.randomUUID?.();
  let parameters = {};
  try {
    parameters = collectParameterValues();
    const requestBody = { query };
    if (Object.keys(parameters).length) requestBody.parameters = parameters;
    const response = await fetch('/api/agent/query', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(requestId ? { 'X-Request-ID': requestId } : {}),
      },
      body: JSON.stringify(requestBody),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'MCP отклонил запрос.');
    const result = QueryResultView.parse(data);
    renderResult(result);
    lastDebugPassed = result?.status === 'ok';
    lastDebugQuery = lastDebugPassed ? query : '';
    lastDebugParameters = lastDebugPassed ? parameters : null;
    publishPanel.hidden = !lastDebugPassed;
    publishButton.disabled = !lastDebugPassed;
    statusNode.textContent = `Проверка завершена · request_id ${data.request_id || requestId || '—'}`;
  } catch (error) {
    resultNode.textContent = '';
    statusNode.textContent = error?.message || 'Не удалось выполнить запрос.';
  } finally {
    runButton.disabled = !selectedPrompt;
    saveButton.disabled = selectedIsPublishedDraft || !selectedPrompt || !dirty;
  }
}

function adminAuthorizationHeader() {
  const value = publishTokenInput.value;
  if (!value) return {};
  const bytes = new TextEncoder().encode(value);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  const encoded = btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
  return { Authorization: `Bearer-UTF8 ${encoded}` };
}

async function publishDebuggedQuery() {
  const title = byId('query-publish-title').value.trim();
  const description = byId('query-publish-description').value.trim();
  const status = byId('query-publish-status');
  if (!lastDebugPassed || lastDebugQuery !== editor.value.trim()) {
    status.textContent = 'Сначала успешно выполните текущий вариант запроса.';
    return;
  }
  if (!title) {
    byId('query-publish-title').focus();
    status.textContent = 'Укажите название запроса.';
    return;
  }
  if (!publishTokenInput.value) {
    publishTokenInput.focus();
    status.textContent = 'Введите ONTOLOGY_ADMIN_TOKEN администратора инструментов.';
    return;
  }
  publishButton.disabled = true;
  status.textContent = 'Публикую шаблон…';
  try {
    const response = await fetch('/api/published-queries', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...adminAuthorizationHeader() },
      body: JSON.stringify({ title, description, query: editor.value.trim() }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    status.textContent = 'Запрос опубликован в общем каталоге. Открыть вкладку «Запросы».';
    showNotice('Параметризованный шаблон опубликован. Он доступен на вкладке «Запросы».');
    publishButton.disabled = true;
  } catch (error) {
    status.textContent = error.message || 'Не удалось опубликовать запрос.';
    publishButton.disabled = false;
  }
}

editor.addEventListener('input', () => {
  setDirty(true);
  renderParameterFields();
  if (lastDebugPassed && editor.value.trim() !== lastDebugQuery) {
    lastDebugPassed = false;
    publishPanel.hidden = true;
    publishButton.disabled = true;
  }
});
parametersNode.addEventListener('input', () => {
  if (!lastDebugPassed) return;
  const current = Object.fromEntries([...parametersNode.querySelectorAll('[data-query-parameter]')]
    .map((input) => [input.dataset.queryParameter, input.value]));
  if (JSON.stringify(current) !== JSON.stringify(lastDebugParameters)) {
    lastDebugPassed = false;
    publishPanel.hidden = true;
    publishButton.disabled = true;
  }
});
saveButton.addEventListener('click', saveEditedQuery);
copyButton.addEventListener('click', copyEditedQuery);
runButton.addEventListener('click', runQuery);
tableModeButton.addEventListener('click', () => setResultView('table'));
jsonModeButton.addEventListener('click', () => setResultView('json'));
publishButton.addEventListener('click', publishDebuggedQuery);
refreshQueries();
const pendingPublishedDraft = savedQueries.find((item) => item.publishedDraft);
if (pendingPublishedDraft) selectQuery(pendingPublishedDraft.prompt);
