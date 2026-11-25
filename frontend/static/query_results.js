(() => {
  function parse(data) {
    const content = data?.tool_result?.content;
    const text = Array.isArray(content) ? content.find((item) => item?.type === 'text')?.text : null;
    if (typeof text !== 'string') return data?.tool_result ?? data;
    try { return JSON.parse(text); } catch { return { message: text }; }
  }

  function formatCell(value) {
    if (value && typeof value === 'object' && !Array.isArray(value) && 'value' in value) {
      return { value: String(value.value ?? ''), detail: [value.type, value['xml:lang'], value.datatype].filter(Boolean).join(' · ') };
    }
    if (value === null || value === undefined) return { value: '—', detail: '' };
    if (typeof value === 'object') return { value: JSON.stringify(value), detail: '' };
    return { value: String(value), detail: '' };
  }

  function appendMetadata(result, target) {
    const metadata = document.createElement('p');
    metadata.className = 'query-result-metadata';
    const parts = [];
    if (typeof result.status === 'string') parts.push(`Статус: ${result.status}`);
    if (Array.isArray(result.bindings)) parts.push(`Строк: ${result.bindings.length}`);
    if (typeof result.effective_limit === 'number') parts.push(`Лимит: ${result.effective_limit}`);
    if (typeof result.truncated === 'boolean') parts.push(result.truncated ? 'Результат усечён' : 'Результат полный');
    if (typeof result.snapshot_id === 'string') parts.push(`Снимок: ${result.snapshot_id}`);
    if (typeof result.entailment === 'string') parts.push(`Вывод: ${result.entailment}`);
    if (typeof result.mode === 'string') parts.push(`Режим: ${result.mode}`);
    metadata.textContent = parts.join(' · ');
    target.append(metadata);
  }

  function render(target, result, mode = 'table', options = {}) {
    target.replaceChildren();
    if (mode === 'json') {
      const json = document.createElement('pre');
      json.className = 'query-result-fallback';
      json.textContent = JSON.stringify(result, null, 2);
      target.append(json);
      return;
    }
    if (!result || typeof result !== 'object' || Array.isArray(result)) {
      target.textContent = typeof result === 'string' ? result : JSON.stringify(result, null, 2);
      return;
    }
    appendMetadata(result, target);
    if (!Array.isArray(result.bindings)) {
      const fallback = document.createElement('pre');
      fallback.className = 'query-result-fallback';
      fallback.textContent = JSON.stringify(result, null, 2);
      target.append(fallback);
      return;
    }
    if (!result.bindings.length) {
      const empty = document.createElement('p');
      empty.className = 'query-result-empty';
      empty.textContent = 'Запрос выполнен, строк не найдено.';
      target.append(empty);
      return;
    }

    const columns = [...new Set(result.bindings.flatMap((row) => (
      row && typeof row === 'object' && !Array.isArray(row) ? Object.keys(row) : []
    )))];
    if (!columns.length) {
      const fallback = document.createElement('pre');
      fallback.className = 'query-result-fallback';
      fallback.textContent = JSON.stringify(result.bindings, null, 2);
      target.append(fallback);
      return;
    }
    const scroller = document.createElement('div');
    scroller.className = 'query-result-table-scroll';
    const table = document.createElement('table');
    table.className = 'query-result-table';
    const head = document.createElement('thead');
    const headerRow = document.createElement('tr');
    columns.forEach((column) => {
      const cell = document.createElement('th');
      cell.scope = 'col';
      cell.textContent = column;
      headerRow.append(cell);
    });
    head.append(headerRow);
    const body = document.createElement('tbody');
    result.bindings.forEach((row) => {
      const tableRow = document.createElement('tr');
      columns.forEach((column) => {
        const cell = document.createElement('td');
        const formatted = formatCell(row?.[column]);
        const value = document.createElement('span');
        value.className = 'query-result-cell-value';
        value.textContent = formatted.value;
        cell.dataset.exportValue = formatted.value;
        cell.append(value);
        if (formatted.detail) {
          const detail = document.createElement('small');
          detail.className = 'query-result-cell-detail';
          detail.textContent = formatted.detail;
          cell.append(detail);
        }
        tableRow.append(cell);
      });
      body.append(tableRow);
    });
    table.append(head, body);
    scroller.append(table);
    const exports = TableExport.createButtons(table, options.filename || 'query-results', options.onError);
    target.append(exports, scroller);
  }

  window.QueryResultView = { parse, render };
})();
