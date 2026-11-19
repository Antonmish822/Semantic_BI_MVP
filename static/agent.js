const byId = (id) => document.getElementById(id);
const chatLog = byId('chat-log');
const chatForm = byId('chat-form');
const chatInput = byId('chat-input');
const chatSend = byId('chat-send');
const chatError = byId('chat-error');
const chatReset = byId('chat-reset');
const agentStatus = byId('agent-status');
let sessionId = sessionStorage.getItem('agentSessionId');
let history = [];

function setStatus(state, message) {
  agentStatus.className = `connection ${state}`;
  agentStatus.lastElementChild.textContent = message;
}

function renderMessage(role, text) {
  const item = document.createElement('article');
  item.className = `chat-message chat-${role}`;

  const meta = document.createElement('div');
  meta.className = 'chat-meta';
  meta.textContent = role === 'user' ? 'Вы' : 'Агент';

  const body = document.createElement('div');
  body.className = 'chat-body';
  body.textContent = text;

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
  sessionId = null;
  sessionStorage.removeItem('agentSessionId');
  chatLog.replaceChildren();
  clearError();
  setStatus('pending', 'Ожидание…');
}

async function sendMessage(text) {
  renderMessage('user', text);
  setStatus('pending', 'Думаю…');
  chatSend.disabled = true;

  try {
    const response = await fetch('/api/agent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, session_id: sessionId, history })
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || 'Не удалось получить ответ агента');
    }

    const reply = data.reply || 'Нет ответа от агента.';
    renderMessage('assistant', reply);
    history = Array.isArray(data.history) ? data.history : history;
    if (data.session_id) updateSession(data.session_id);
    setStatus('online', 'Агент онлайн');
  } catch (error) {
    setError(error.message || 'Ошибка запроса');
  } finally {
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
