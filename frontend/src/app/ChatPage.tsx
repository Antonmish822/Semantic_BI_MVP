import { useEffect, useReducer, useRef, useState, type FormEvent } from 'react';
import { apiJson, ApiError } from './api';
import { ChatMarkdown } from './ChatMarkdown';
import { DashboardHeader, PageNav } from './DashboardChrome';
import {
  clearChat, maxSavedPrompts, normalizeMessages, readChat, readSavedPrompts, savedPromptsKey, writeChat,
  type ChatMessage, type ChatSession, type SavedPrompt,
} from './browserStorage';
import { copyText } from './clipboard';

type DisplayMessage = ChatMessage & { duration?: number; answered?: boolean };
type Chat = Omit<ChatSession, 'transcript'> & {
  transcript: DisplayMessage[];
  progress: string;
  error: string;
  status: 'pending' | 'online' | 'offline';
};
type AgentReply = { reply?: string; history?: unknown[]; session_id?: string; saved_query?: string; request_id?: string };
type Action =
  | { type: 'send'; text: string }
  | { type: 'progress'; message: string }
  | { type: 'reply'; result: AgentReply; duration: number }
  | { type: 'error'; message: string; request?: boolean }
  | { type: 'reset' };
const initialProgress = 'Подготавливаю запрос';
const limitMessage = 'Достигнут лимит 10. Удалите один промпт вручную, чтобы сохранить новый.';
const emptyChat: Chat = { history: [], transcript: [], sessionId: null, progress: '', error: '', status: 'pending' };

function restore(): Chat {
  const previous = readChat(sessionStorage);
  return { ...emptyChat, ...previous,
    status: previous.transcript.length ? 'online' : 'pending',
    transcript: previous.transcript.map((item, index) => ({
      ...item, answered: item.role === 'user' && previous.transcript[index + 1]?.role === 'assistant',
    })),
  };
}

function reducer(chat: Chat, action: Action): Chat {
  switch (action.type) {
    case 'send':
      return { ...chat, transcript: [...chat.transcript, { role: 'user', content: action.text }],
        progress: initialProgress, status: 'pending', error: '' };
    case 'progress':
      return { ...chat, progress: action.message };
    case 'reply': {
      const { result, duration } = action;
      const transcript = [...chat.transcript];
      const question = transcript[transcript.length - 1];
      if (question?.role === 'user') {
        transcript[transcript.length - 1] = { ...question, answered: true, saved_query: result.saved_query };
      }
      transcript.push({ role: 'assistant', content: result.reply || 'Нет ответа от агента.', duration });
      return { ...chat, transcript, progress: '', status: 'online',
        sessionId: result.session_id || chat.sessionId,
        history: Array.isArray(result.history) ? normalizeMessages(result.history) : chat.history,
      };
    }
    case 'error':
      return { ...chat, error: action.message, status: 'offline', progress: action.request ? '' : chat.progress };
    case 'reset':
      return emptyChat;
  }
}

export function ChatPage() {
  const [chat, dispatch] = useReducer(reducer, undefined, restore);
  const [input, setInput] = useState('');
  const [prompts, setPrompts] = useState(() => readSavedPrompts(localStorage).slice(0, maxSavedPrompts));
  const [promptsOpen, setPromptsOpen] = useState(false);
  const [feedback, setFeedback] = useState(prompts.length >= maxSavedPrompts ? limitMessage : '');
  const log = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const request = useRef<{ cancel: () => void } | null>(null);
  const full = prompts.length >= maxSavedPrompts;

  useEffect(() => {
    document.title = 'AI Agent — Semantic BI';
    const cancel = () => {
      if (!request.current) return;
      request.current?.cancel();
      request.current = null;
      dispatch({ type: 'progress', message: '' });
    };
    window.addEventListener('pagehide', cancel);
    return () => {
      cancel();
      window.removeEventListener('pagehide', cancel);
    };
  }, []);
  useEffect(() => {
    if (!chat.transcript.length && !chat.history.length && !chat.sessionId) clearChat(sessionStorage);
    else writeChat(sessionStorage, {
      sessionId: chat.sessionId, history: chat.history,
      transcript: chat.transcript.map(({ role, content, saved_query }) => ({ role, content, saved_query })),
    });
    if (log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [chat.history, chat.sessionId, chat.transcript]);

  function updatePrompts(next: SavedPrompt[], message: string) {
    try {
      localStorage.setItem(savedPromptsKey, JSON.stringify(next));
      setPrompts(next);
      setFeedback(next.length >= maxSavedPrompts ? limitMessage : message);
    } catch {
      setFeedback('Не удалось сохранить список в хранилище браузера.');
    }
  }

  function savePrompt(item: ChatMessage) {
    const prompt = item.content.trim();
    if (!prompt || full) return;
    if (prompts.some((saved) => saved.prompt === prompt)) {
      setFeedback('Такая формулировка уже сохранена.');
      return;
    }
    updatePrompts([...prompts, { prompt, sparql: item.saved_query ?? '' }], 'Промпт сохранён.');
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const message = input.trim();
    if (!message || request.current) return;
    const controller = new AbortController();
    const progress = new AbortController();
    let timer: number | undefined;
    function stopProgress() {
      progress.abort();
      window.clearTimeout(timer);
    }
    request.current = { cancel: () => { controller.abort(); stopProgress(); } };
    setInput('');
    dispatch({ type: 'send', text: message });
    let id: string | undefined = globalThis.crypto?.randomUUID?.();
    const startedAt = performance.now();

    async function poll() {
      if (!id || progress.signal.aborted) return;
      try {
        const update = await apiJson<{ message?: string; stage?: string }>(
          `/api/agent/progress?request_id=${encodeURIComponent(id)}`, { signal: progress.signal });
        if (progress.signal.aborted) return;
        if (update.message) dispatch({ type: 'progress', message: update.message });
        if (update.stage === 'done' || update.stage === 'error') return;
      } catch {
        // The main request can succeed while progress is unavailable.
      }
      if (!progress.signal.aborted) timer = window.setTimeout(() => void poll(), 500);
    }

    void poll();
    let httpStatus: number | null = null;
    let errorClass: string | null = null;
    try {
      const result = await apiJson<AgentReply>('/api/agent', {
        method: 'POST', signal: controller.signal,
        headers: { 'Content-Type': 'application/json', ...(id ? { 'X-Request-ID': id } : {}) },
        body: JSON.stringify({ message, session_id: chat.sessionId, history: chat.history }),
      });
      if (controller.signal.aborted) return;
      httpStatus = 200;
      id = result.request_id || id;
      dispatch({ type: 'reply', result, duration: performance.now() - startedAt });
    } catch (error) {
      if (controller.signal.aborted) return;
      httpStatus = error instanceof ApiError ? error.status ?? null : null;
      errorClass = error instanceof ApiError
        ? error.kind === 'invalid-response' ? 'json' : httpStatus ? 'http' : 'network'
        : 'render';
      dispatch({ type: 'error', message: error instanceof Error ? error.message : 'Ошибка запроса', request: true });
    } finally {
      stopProgress();
      if (!controller.signal.aborted) {
        request.current = null;
        if (id) void fetch('/api/agent/profile', {
          method: 'POST', keepalive: true, headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ request_id: id, outcome: errorClass ? 'error' : 'ok',
            duration_ms: Math.round((performance.now() - startedAt) * 100) / 100,
            http_status: httpStatus, error_class: errorClass }),
        }).catch(() => {});
      }
    }
  }

  return <main className="shell">
    <DashboardHeader title="AI Agent">
      <span id="agent-status" className={`connection ${chat.status}`}><i/><span>
        {chat.status === 'online' ? 'Агент онлайн' : chat.status === 'offline' ? 'Ошибка' : chat.progress ? 'Думаю…' : 'Ожидание…'}
      </span></span>
      <button id="chat-reset" className="secondary-button" type="button" aria-label="Сбросить чат" onClick={() => {
        request.current?.cancel();
        request.current = null;
        dispatch({ type: 'reset' });
      }}>Сбросить чат</button>
    </DashboardHeader>
    <PageNav active="/agent"/>
    <div id="chat-error" className="error-banner" role="alert" hidden={!chat.error}>{chat.error}</div>
    <section id="agent-progress" className="agent-progress" aria-label="Ход обработки запроса" hidden={!chat.progress}>
      <div className="agent-progress-copy">
        <span className="agent-progress-label">Текущий этап</span>
        <span id="agent-progress-stage" role="status" aria-live="polite">{chat.progress || initialProgress}</span>
      </div>
      <div className="agent-progress-track" role="progressbar" aria-label="Запрос обрабатывается">
        <span className="agent-progress-indicator"/>
      </div>
    </section>
    <section className="panel agent-panel" aria-labelledby="agent-title">
      <div className="panel-heading"><div>
        <p className="eyebrow">ЧАТ С СЕМАНТИЧЕСКИМ BI</p>
        <h2 id="agent-title">Спросите о сделках, лидах и сотрудниках</h2>
        <p className="panel-subtitle">Агент использует MCP-инструменты для выборок из RDF-хранилища.</p>
      </div></div>
      <details id="saved-prompts-panel" className="saved-prompts" onToggle={(event) => setPromptsOpen(event.currentTarget.open)}>
        <summary className="saved-prompts-summary">
          <span className="saved-prompts-title">Сохранённые промпты <span id="saved-prompts-count">{`${prompts.length}/${maxSavedPrompts}`}</span></span>
          <span className="saved-prompts-toggle">{promptsOpen ? 'Скрыть' : 'Показать'}</span>
        </summary>
        <div className="saved-prompts-content">
          <p className="panel-subtitle">Сохраняйте удачные вопросы после ответа агента. Список хранится в этом браузере.</p>
          <ul id="saved-prompts-list" className="saved-prompts-list" hidden={!prompts.length}>
            {prompts.map((saved, index) => <li className="saved-prompt-item" key={index}>
              <div className="saved-prompt-content">
                <span className="saved-prompt-text">{saved.prompt}</span>
                {saved.sparql && <details className="saved-prompt-query">
                  <summary>Показать SPARQL</summary>
                  <p className="saved-prompt-query-hint">Запрос, переданный агентом инструменту sparql_select. MCP проверяет его и может применить ограничения перед выполнением.</p>
                  <pre><code>{saved.sparql}</code></pre>
                  <button type="button" className="saved-prompt-button" onClick={() => {
                    void copyText(saved.sparql).then(() => setFeedback('SPARQL скопирован.'),
                      () => setFeedback('Не удалось скопировать запрос. Разрешите доступ к буферу обмена в браузере.'));
                  }}>Копировать SPARQL</button>
                </details>}
              </div>
              <div className="saved-prompt-actions">
                <button type="button" className="saved-prompt-button" onClick={() => {
                  setInput(saved.prompt);
                  textarea.current?.focus();
                }}>Вставить</button>
                <button type="button" className="saved-prompt-button saved-prompt-button--delete"
                  aria-label={`Удалить сохранённый промпт ${index + 1}`} onClick={() =>
                    updatePrompts(prompts.filter((_, position) => index !== position), 'Промпт удалён. Теперь можно сохранить новый.')}>Удалить</button>
              </div>
            </li>)}
          </ul>
          <p id="saved-prompts-feedback" className="saved-prompts-feedback" role="status" aria-live="polite">{feedback}</p>
        </div>
      </details>
      <div ref={log} id="chat-log" className="chat-log" aria-live="polite">
        {chat.transcript.map((item, index) => <article className={`chat-message chat-${item.role}`} key={index}>
          <div className="chat-meta">{item.role === 'user' ? 'Вы' : 'Агент'}
            {item.duration !== undefined && <span className="chat-duration">Ответ за {(item.duration / 1000).toFixed(1)} с</span>}
          </div>
          <div className="chat-body">{item.role === 'user' ? item.content :
            <ChatMarkdown text={item.content} onError={(message) => dispatch({ type: 'error', message })}/>}</div>
          {item.answered && <div className="chat-message-actions">
            <button className="save-prompt-button" type="button" disabled={full}
              title={full ? 'Удалите сохранённый промпт, чтобы освободить место' : 'Сохранить эту формулировку'}
              onClick={() => savePrompt(item)}>Сохранить промпт</button>
          </div>}
        </article>)}
      </div>
      <form id="chat-form" className="chat-form" autoComplete="off" onSubmit={(event) => void submit(event)}>
        <textarea ref={textarea} id="chat-input" className="chat-input" rows={3} required value={input}
          placeholder="Например: Покажи топ-5 категорий по активным сделкам"
          onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}/>
        <div className="chat-actions">
          <span id="chat-hint" className="panel-subtitle">Enter — отправить, Shift+Enter — новая строка.</span>
          <button id="chat-send" className="refresh" type="submit" disabled={!!chat.progress}>Отправить</button>
        </div>
      </form>
    </section>
  </main>;
}
