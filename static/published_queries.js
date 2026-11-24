(() => {
  const $ = (id) => document.getElementById(id);
  const tokenKey = 'crm-pulse-tools-admin-token';
  const draftKey = 'agentPublishedQueryDraft';
  const list = $('published-query-list');
  const tokenInput = $('published-query-token');

  try { tokenInput.value = localStorage.getItem(tokenKey) || ''; } catch (_) {}
  tokenInput.addEventListener('input', () => {
    try {
      if (tokenInput.value) localStorage.setItem(tokenKey, tokenInput.value);
      else localStorage.removeItem(tokenKey);
    } catch (_) {}
  });

  function authorizationHeader() {
    const value = tokenInput.value;
    if (!value) return {};
    const bytes = new TextEncoder().encode(value);
    let binary = '';
    for (const byte of bytes) binary += String.fromCharCode(byte);
    const encoded = btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
    return { Authorization: `Bearer-UTF8 ${encoded}` };
  }

  function notice(message, error = false) {
    const node = $('published-query-notice');
    node.textContent = message;
    node.hidden = !message;
    node.classList.toggle('query-notice-success', Boolean(message) && !error);
  }

  async function api(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers: { ...(options.headers || {}), ...authorizationHeader() },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  async function copyQuery(query) {
    try {
      await navigator.clipboard.writeText(query);
      notice('Шаблон скопирован. Плейсхолдеры {{имя}} сохранены.');
    } catch {
      notice('Не удалось скопировать запрос: проверьте разрешение браузера на буфер обмена.', true);
    }
  }

  async function runPublishedQuery(item, inputs, runButton, statusNode, resultNode, modeButtons, resultView) {
    const parameters = {};
    for (const input of inputs) {
      if (!input.value.trim()) {
        input.focus();
        statusNode.textContent = `Заполните параметр «${input.dataset.queryParameter}».`;
        return;
      }
      try { parameters[input.dataset.queryParameter] = QueryParameters.valueForApi(input); }
      catch (error) { input.focus(); statusNode.textContent = error.message; return; }
    }
    runButton.disabled = true;
    statusNode.textContent = 'Выполняю через MCP…';
    resultNode.replaceChildren();
    const requestId = globalThis.crypto?.randomUUID?.();
    try {
      const response = await fetch('/api/agent/query', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(requestId ? { 'X-Request-ID': requestId } : {}),
        },
        body: JSON.stringify({ query: item.query, ...(inputs.length ? { parameters } : {}) }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'MCP отклонил запрос.');
      const result = QueryResultView.parse(data);
      resultView.result = result;
      QueryResultView.render(resultNode, result, resultView.mode, {
        filename: `published-query-${item.id}`,
        onError: (error) => { statusNode.textContent = error?.message || 'Не удалось скачать Excel'; },
      });
      statusNode.textContent = `Завершено · request_id ${data.request_id || requestId || '—'}`;
      modeButtons.hidden = false;
    } catch (error) {
      resultView.result = null;
      resultNode.textContent = '';
      statusNode.textContent = error?.message || 'Не удалось выполнить запрос.';
      modeButtons.hidden = true;
    } finally {
      runButton.disabled = false;
    }
  }

  function openInDebugger(item) {
    try {
      sessionStorage.setItem(draftKey, JSON.stringify({ id: item.id, title: item.title, query: item.query }));
      window.location.href = '/tools/queries';
    } catch {
      notice('Не удалось передать запрос в отладчик текущей вкладки.', true);
    }
  }

  async function removeQuery(item) {
    if (!window.confirm(`Удалить «${item.title}» из общего каталога?`)) return;
    try {
      await api(`/api/published-queries/${encodeURIComponent(item.id)}`, { method: 'DELETE' });
      notice('Запрос удалён из каталога.');
      await refresh();
    } catch (error) {
      notice(error.message, true);
    }
  }

  function render(items) {
    list.replaceChildren();
    if (!items.length) {
      list.append(element('p', 'Опубликованных запросов пока нет. Проверенный запрос можно опубликовать из Инструменты → Запросы.', 'empty-state'));
      return;
    }
    items.forEach((item) => {
      const card = document.createElement('details');
      card.className = 'published-query-card';
      const heading = document.createElement('summary');
      heading.className = 'published-query-card-heading';
      const title = element('h3', item.title);
      const when = element('span', item.updated_at ? new Date(item.updated_at).toLocaleString() : '', 'published-query-date');
      heading.append(title, when);
      card.append(heading);
      if (item.description) card.append(element('p', item.description, 'published-query-description'));
      const params = Array.isArray(item.parameters) ? item.parameters : [];
      card.append(element('p', params.length ? `Параметры: ${params.map((name) => `{{${name}}}`).join(', ')}` : 'Без параметров', 'published-query-params'));
      const runPanel = element('section', undefined, 'published-query-runbox');
      const inputs = [];
      if (params.length) {
        const fields = element('div', undefined, 'query-parameter-fields');
        let parameterDetails;
        try { parameterDetails = QueryParameters.describe(item.query); }
        catch (error) {
          runPanel.append(element('p', error.message, 'error-banner'));
          parameterDetails = [];
        }
        parameterDetails.forEach(({ name, type }, index) => {
          const label = element('label', undefined, 'query-parameter-field');
          const input = type === 'boolean' ? document.createElement('select') : document.createElement('input');
          input.id = `published-query-${item.id}-param-${index}`;
          input.dataset.queryParameter = name;
          input.dataset.parameterType = type;
          if (type === 'boolean') {
            const placeholder = element('option', 'Выберите значение');
            placeholder.value = '';
            input.append(placeholder);
            for (const value of ['true', 'false']) {
              const option = element('option', value);
              option.value = value;
              input.append(option);
            }
          } else {
            input.type = QueryParameters.inputType(type);
            const step = QueryParameters.step(type);
            if (step) input.step = step;
          }
          input.required = true;
          input.autocomplete = 'off';
          label.htmlFor = input.id;
          label.append(element('span', name), input);
          fields.append(label);
          inputs.push(input);
        });
        runPanel.append(fields);
      }
      const runControls = element('div', undefined, 'published-query-run-controls');
      const run = element('button', 'Выполнить запрос', 'refresh');
      run.type = 'button';
      const runStatus = element('span', '', 'published-query-run-status');
      runControls.append(run, runStatus);
      runPanel.append(runControls);
      const resultHeading = element('div', undefined, 'query-result-heading published-query-result-heading');
      resultHeading.append(element('h4', 'Результат выполнения'));
      const resultView = { mode: 'table', result: null };
      const viewButtons = element('div', undefined, 'query-result-view-switch');
      viewButtons.setAttribute('role', 'group');
      viewButtons.setAttribute('aria-label', 'Формат результата');
      viewButtons.hidden = true;
      const tableMode = element('button', 'Таблица', 'query-result-view-button active');
      tableMode.type = 'button';
      tableMode.setAttribute('aria-pressed', 'true');
      const jsonMode = element('button', 'JSON', 'query-result-view-button');
      jsonMode.type = 'button';
      jsonMode.setAttribute('aria-pressed', 'false');
      tableMode.addEventListener('click', () => {
        resultView.mode = 'table';
        tableMode.classList.add('active');
        tableMode.setAttribute('aria-pressed', 'true');
        jsonMode.classList.remove('active');
        jsonMode.setAttribute('aria-pressed', 'false');
        if (resultView.result !== null) QueryResultView.render(resultNode, resultView.result, resultView.mode, {
          filename: `published-query-${item.id}`,
          onError: (error) => { runStatus.textContent = error?.message || 'Не удалось скачать Excel'; },
        });
      });
      jsonMode.addEventListener('click', () => {
        resultView.mode = 'json';
        jsonMode.classList.add('active');
        jsonMode.setAttribute('aria-pressed', 'true');
        tableMode.classList.remove('active');
        tableMode.setAttribute('aria-pressed', 'false');
        if (resultView.result !== null) QueryResultView.render(resultNode, resultView.result, resultView.mode, {
          filename: `published-query-${item.id}`,
          onError: (error) => { runStatus.textContent = error?.message || 'Не удалось скачать Excel'; },
        });
      });
      viewButtons.append(tableMode, jsonMode);
      resultHeading.append(viewButtons);
      const resultNode = element('div', 'Запрос ещё не запускался.', 'query-result published-query-result');
      resultNode.setAttribute('aria-live', 'polite');
      run.addEventListener('click', () => runPublishedQuery(item, inputs, run, runStatus, resultNode, viewButtons, resultView));
      runPanel.append(resultHeading, resultNode);
      card.append(runPanel);
      const disclosure = document.createElement('details');
      disclosure.className = 'published-query-source';
      disclosure.append(element('summary', 'Показать SPARQL-шаблон'));
      disclosure.append(element('pre', item.query, 'published-query-code'));
      card.append(disclosure);
      const actions = element('div', undefined, 'published-query-actions');
      const debug = element('button', 'Открыть в отладчике', 'secondary-button');
      debug.type = 'button';
      debug.addEventListener('click', () => openInDebugger(item));
      const copy = element('button', 'Копировать', 'secondary-button');
      copy.type = 'button';
      copy.addEventListener('click', () => copyQuery(item.query));
      actions.append(debug, copy);
      if ($('published-query-admin-status').dataset.adminEnabled === 'true') {
        const remove = element('button', 'Удалить', 'secondary-button published-query-delete');
        remove.type = 'button';
        remove.addEventListener('click', () => removeQuery(item));
        actions.append(remove);
      }
      card.append(actions);
      list.append(card);
    });
  }

  async function refresh() {
    const refreshButton = $('published-query-refresh');
    refreshButton.disabled = true;
    try {
      const data = await api('/api/published-queries');
      const enabled = Boolean(data.admin_enabled);
      const adminStatus = $('published-query-admin-status');
      adminStatus.dataset.adminEnabled = String(enabled);
      adminStatus.textContent = enabled
        ? 'Публикация и удаление доступны администратору с ONTOLOGY_ADMIN_TOKEN.'
        : 'Публикация и удаление отключены: серверу не задан ONTOLOGY_ADMIN_TOKEN.';
      render(Array.isArray(data.queries) ? data.queries : []);
    } catch (error) {
      list.replaceChildren(element('p', error.message, 'assistant-error'));
    } finally {
      refreshButton.disabled = false;
    }
  }

  $('published-query-refresh').addEventListener('click', refresh);
  refresh();
})();
