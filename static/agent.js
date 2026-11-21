const byId = (id) => document.getElementById(id);
const chatLog = byId('chat-log');
const chatForm = byId('chat-form');
const chatInput = byId('chat-input');
const chatSend = byId('chat-send');
const chatError = byId('chat-error');
const chatReset = byId('chat-reset');
const agentStatus = byId('agent-status');
const agentProgress = byId('agent-progress');
const agentProgressStage = byId('agent-progress-stage');
let sessionId = sessionStorage.getItem('agentSessionId');
let history = readStoredMessages('agentHistory');
let transcript = readStoredMessages('agentTranscript');

function readStoredMessages(key) {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) || '[]');
    if (!Array.isArray(value)) return [];
    return value.filter((item) => item &&
      (item.role === 'user' || item.role === 'assistant') &&
      typeof item.content === 'string');
  } catch {
    return [];
  }
}

function saveStoredMessages() {
  try {
    sessionStorage.setItem('agentHistory', JSON.stringify(history));
    sessionStorage.setItem('agentTranscript', JSON.stringify(transcript));
  } catch {
    // Keep the current chat usable if the browser storage quota is reached.
  }
}

function restoreChat() {
  for (const item of transcript) renderMessage(item.role, item.content);
  if (transcript.length) setStatus('online', 'Агент онлайн');
}

function setStatus(state, message) {
  agentStatus.className = `connection ${state}`;
  agentStatus.lastElementChild.textContent = message;
}

function renderToolState(payload) {
  if (!payload || typeof payload !== 'object') return;
  const tool = payload.tool;
  if (tool && typeof tool === 'object') {
    const name = tool.name || 'инструмент';
    setStatus('pending', `MCP: ${name}`);
    return;
  }
  if (payload.tool_result) {
    setStatus('pending', 'MCP: ответ получен');
  }
}

function appendInlineMarkdown(parent, text) {
  const pattern = /`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)|\*\*([^*]+)\*\*|__([^_]+)__|\*([^*\n]+)\*|_([^_\n]+)_|~~([^~]+)~~/g;
  let offset = 0;
  let match;

  while ((match = pattern.exec(text))) {
    parent.append(document.createTextNode(text.slice(offset, match.index)));
    let node;
    if (match[1] !== undefined) {
      node = document.createElement('code');
      node.textContent = match[1];
    } else if (match[2] !== undefined) {
      let href;
      try {
        const url = new URL(match[3], window.location.href);
        if (['http:', 'https:', 'mailto:'].includes(url.protocol)) href = url.href;
      } catch {
        href = null;
      }
      if (href) {
        node = document.createElement('a');
        node.href = href;
        node.rel = 'noopener noreferrer';
        node.textContent = match[2];
      } else {
        node = document.createTextNode(match[0]);
      }
    } else if (match[4] !== undefined || match[5] !== undefined) {
      node = document.createElement('strong');
      node.textContent = match[4] ?? match[5];
    } else if (match[6] !== undefined || match[7] !== undefined) {
      node = document.createElement('em');
      node.textContent = match[6] ?? match[7];
    } else {
      node = document.createElement('del');
      node.textContent = match[8];
    }
    parent.append(node);
    offset = pattern.lastIndex;
  }
  parent.append(document.createTextNode(text.slice(offset)));
}

function markdownCells(line) {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim());
}

function sanitizeCsvValue(value) {
  const text = String(value ?? '').replace(/\r\n?/g, '\n');
  if (/["\n,;]/.test(text)) {
    return `"${text.replace(/"/g, '""')}"`;
  }
  return text;
}

function tableToCsv(table) {
  const rows = Array.from(table.querySelectorAll('tr'));
  return rows.map((row) => {
    const cells = Array.from(row.querySelectorAll('th, td'));
    return cells.map((cell) => sanitizeCsvValue(cell.textContent?.trim() || '')).join(',');
  }).join('\n');
}

function downloadTableAsCsv(table, filename) {
  const csv = tableToCsv(table);
  const blob = new Blob(["\ufeff", csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function ensureSheetJs() {
  if (window.XLSX) {
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = '/vendor/xlsx.full.min.js';
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error('Не удалось загрузить SheetJS'));
    document.head.append(script);
  });
}

async function downloadTableAsXlsx(table, filename) {
  await ensureSheetJs();
  const workbook = window.XLSX.utils.table_to_book(table, { sheet: 'Данные' });
  window.XLSX.writeFile(workbook, filename);
}

function isTableDivider(line) {
  const cells = markdownCells(line);
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function renderMarkdown(parent, markdown) {
  const lines = String(markdown).replace(/\r\n?/g, '\n').split('\n');
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    if (/^\s*```/.test(line)) {
      index += 1;
      const code = [];
      while (index < lines.length && !/^\s*```/.test(lines[index])) code.push(lines[index++]);
      if (index < lines.length) index += 1;
      const pre = document.createElement('pre');
      const codeNode = document.createElement('code');
      codeNode.textContent = code.join('\n');
      pre.append(codeNode);
      parent.append(pre);
      continue;
    }

    const heading = line.match(/^\s{0,3}(#{1,4})\s+(.+?)\s*#*\s*$/);
    if (heading) {
      const node = document.createElement(`h${heading[1].length + 1}`);
      appendInlineMarkdown(node, heading[2]);
      parent.append(node);
      index += 1;
      continue;
    }

    if (/^\s{0,3}(?:\*\s*){3,}$/.test(line) || /^\s{0,3}(?:-\s*){3,}$/.test(line)) {
      parent.append(document.createElement('hr'));
      index += 1;
      continue;
    }

    if (index + 1 < lines.length && line.includes('|') && isTableDivider(lines[index + 1])) {
      const table = document.createElement('table');
      const thead = document.createElement('thead');
      const headerRow = document.createElement('tr');
      for (const value of markdownCells(line)) {
        const cell = document.createElement('th');
        appendInlineMarkdown(cell, value);
        headerRow.append(cell);
      }
      thead.append(headerRow);
      table.append(thead);
      index += 2;
      const tbody = document.createElement('tbody');
      while (index < lines.length && lines[index].includes('|') && lines[index].trim()) {
        const row = document.createElement('tr');
        for (const value of markdownCells(lines[index])) {
          const cell = document.createElement('td');
          appendInlineMarkdown(cell, value);
          row.append(cell);
        }
        tbody.append(row);
        index += 1;
      }
      table.append(tbody);
      const wrapper = document.createElement('div');
      wrapper.className = 'chat-table-wrap';
      const actions = document.createElement('div');
      actions.className = 'chat-table-actions';
      const downloadCsv = document.createElement('button');
      downloadCsv.type = 'button';
      downloadCsv.className = 'chat-table-download';
      downloadCsv.textContent = 'Скачать CSV';
      downloadCsv.addEventListener('click', () => {
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
        downloadTableAsCsv(table, `agent-table-${timestamp}.csv`);
      });
      const downloadExcel = document.createElement('button');
      downloadExcel.type = 'button';
      downloadExcel.className = 'chat-table-download chat-table-download--excel';
      downloadExcel.textContent = 'Скачать Excel';
      downloadExcel.addEventListener('click', async () => {
        try {
          const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
          await downloadTableAsXlsx(table, `agent-table-${timestamp}.xlsx`);
        } catch (error) {
          setError(error?.message || 'Не удалось скачать Excel');
        }
      });
      actions.append(downloadCsv, downloadExcel);
      wrapper.append(actions, table);
      parent.append(wrapper);
      continue;
    }

    const listItem = line.match(/^\s*(?:([-+*])\s+|(\d+)[.)]\s+)(.+)$/);
    if (listItem) {
      const ordered = listItem[2] !== undefined;
      const list = document.createElement(ordered ? 'ol' : 'ul');
      while (index < lines.length) {
        const item = lines[index].match(/^\s*(?:([-+*])\s+|(\d+)[.)]\s+)(.+)$/);
        if (!item || (item[2] !== undefined) !== ordered) break;
        const li = document.createElement('li');
        appendInlineMarkdown(li, item[3]);
        list.append(li);
        index += 1;
      }
      parent.append(list);
      continue;
    }

    if (/^\s*&gt;\s?/.test(line) || /^\s*>\s?/.test(line)) {
      const quote = document.createElement('blockquote');
      while (index < lines.length && /^\s*>\s?/.test(lines[index])) {
        const paragraph = document.createElement('p');
        appendInlineMarkdown(paragraph, lines[index++].replace(/^\s*>\s?/, ''));
        quote.append(paragraph);
      }
      parent.append(quote);
      continue;
    }

    const paragraph = [];
    while (index < lines.length && lines[index].trim() &&
      !/^\s{0,3}#{1,4}\s/.test(lines[index]) &&
      !/^\s*```/.test(lines[index]) &&
      !/^\s*(?:[-+*]\s+|\d+[.)]\s+)/.test(lines[index]) &&
      !(index + 1 < lines.length && lines[index].includes('|') && isTableDivider(lines[index + 1]))) {
      paragraph.push(lines[index++].trim());
    }
    const p = document.createElement('p');
    appendInlineMarkdown(p, paragraph.join(' '));
    parent.append(p);
  }
}

function renderMessage(role, text, { durationMs } = {}) {
  const item = document.createElement('article');
  item.className = `chat-message chat-${role}`;

  const meta = document.createElement('div');
  meta.className = 'chat-meta';
  meta.textContent = role === 'user' ? 'Вы' : 'Агент';
  if (role === 'assistant' && Number.isFinite(durationMs)) {
    const duration = document.createElement('span');
    duration.className = 'chat-duration';
    duration.textContent = `Ответ за ${(durationMs / 1000).toFixed(1)} с`;
    meta.append(duration);
  }

  const body = document.createElement('div');
  body.className = 'chat-body';
  if (role === 'assistant') renderMarkdown(body, text);
  else body.textContent = text;

  item.append(meta, body);
  chatLog.append(item);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function setError(message) {
  chatError.textContent = message;
  chatError.hidden = false;
  setStatus('offline', 'Ошибка');
}

function clearError() {
  chatError.hidden = true;
}

function updateSession(session) {
  if (!sessionId || sessionId !== session) {
    sessionId = session;
    sessionStorage.setItem('agentSessionId', sessionId);
  }
}

function resetChat() {
  history = [];
  transcript = [];
  sessionId = null;
  sessionStorage.removeItem('agentSessionId');
  sessionStorage.removeItem('agentHistory');
  sessionStorage.removeItem('agentTranscript');
  chatLog.replaceChildren();
  clearError();
  setStatus('pending', 'Ожидание…');
}

async function sendMessage(text) {
  let requestId = globalThis.crypto?.randomUUID?.();
  const startedAt = performance.now();
  let httpStatus = null;
  let phase = 'network';
  let polling = true;
  const logTiming = (outcome, errorClass = null) => {
    if (!requestId) return;
    const body = JSON.stringify({
      request_id: requestId,
      outcome,
      duration_ms: Math.round((performance.now() - startedAt) * 100) / 100,
      http_status: httpStatus,
      error_class: errorClass
    });
    fetch('/api/agent/profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      keepalive: true
    }).catch(() => {});
  };
  renderMessage('user', text);
  transcript.push({ role: 'user', content: text });
  saveStoredMessages();
  agentProgress.hidden = false;
  agentProgressStage.textContent = 'Подготавливаю запрос';
  setStatus('pending', 'Думаю…');
  chatSend.disabled = true;

  const progressPoll = (async () => {
    while (polling && requestId) {
      try {
        const response = await fetch(`/api/agent/progress?request_id=${encodeURIComponent(requestId)}`, {
          cache: 'no-store'
        });
        if (response.ok) {
          const progress = await response.json();
          if (progress.message) agentProgressStage.textContent = progress.message;
          if (progress.stage === 'done' || progress.stage === 'error') break;
        }
      } catch {
        // The main request remains the source of truth if progress polling fails.
      }
      await new Promise((resolve) => window.setTimeout(resolve, 500));
    }
  })();

  try {
    const payload = { message: text, session_id: sessionId, history };
    if (window.__AGENT_DEBUG__) {
      console.info('[agent] request', payload);
    }
    const response = await fetch('/api/agent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(requestId ? { 'X-Request-ID': requestId } : {}) },
      body: JSON.stringify(payload)
    });
    httpStatus = response.status;
    requestId = response.headers.get('X-Request-ID') || requestId;
    phase = response.ok ? 'json' : 'http';
    const data = await response.json();
    if (window.__AGENT_DEBUG__) {
      console.info('[agent] response', response.status, data);
    }
    if (!response.ok) {
      throw new Error(data.error || 'Не удалось получить ответ агента');
    }

    phase = 'render';
    renderToolState(data);
    const reply = data.reply || 'Нет ответа от агента.';
    renderMessage('assistant', reply, { durationMs: performance.now() - startedAt });
    transcript.push({ role: 'assistant', content: reply });
    history = Array.isArray(data.history) ? data.history : history;
    saveStoredMessages();
    if (data.session_id) updateSession(data.session_id);
    setStatus('online', 'Агент онлайн');
    requestId = data.request_id || requestId;
    logTiming('ok');
  } catch (error) {
    setError(error.message || 'Ошибка запроса');
    logTiming('error', phase);
  } finally {
    polling = false;
    await progressPoll;
    agentProgress.hidden = true;
    chatSend.disabled = false;
  }
}

chatForm.addEventListener('submit', (event) => {
  event.preventDefault();
  const text = chatInput.value.trim();
  if (!text) return;
  chatInput.value = '';
  clearError();
  sendMessage(text);
});

chatInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    chatForm.requestSubmit();
  }
});

chatReset.addEventListener('click', () => {
  resetChat();
});

setStatus('pending', 'Ожидание…');
restoreChat();
