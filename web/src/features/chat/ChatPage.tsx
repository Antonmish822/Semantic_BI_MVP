import { useState, type FormEvent } from 'react';
import { api, text, type Row } from '../../shared/api';
import { Layout, Notice, Panel } from '../../shared/Layout';

type Message = { role: 'user' | 'assistant'; content: string };
const HISTORY_KEY = 'reactAgentHistory';
const SESSION_KEY = 'reactAgentSession';

function readHistory(): Message[] {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(HISTORY_KEY) || '[]');
    return Array.isArray(value) ? value.filter((item): item is Message => !!item && typeof item.content === 'string' && (item.role === 'user' || item.role === 'assistant')) : [];
  } catch { return []; }
}

export function ChatPage() {
  const [history, setHistory] = useState<Message[]>(readHistory);
  const [sessionId, setSessionId] = useState(() => sessionStorage.getItem(SESSION_KEY) || '');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [progress, setProgress] = useState('');

  async function submit(event: FormEvent) {
    event.preventDefault();
    const current = message.trim();
    if (!current || busy) return;
    const nextHistory = [...history, { role: 'user' as const, content: current }];
    setHistory(nextHistory); sessionStorage.setItem(HISTORY_KEY, JSON.stringify(nextHistory));
    setMessage(''); setBusy(true); setError(''); setProgress('Подготавливаю запрос');
    const requestId = crypto.randomUUID();
    let poll = true;
    const pollStatus = async () => {
      while (poll) {
        try {
          const status = await api<Row>(`/api/agent/progress?request_id=${requestId}`);
          setProgress(text(status.message, text(status.stage, 'Обрабатываю запрос')));
        } catch { /* Progress is best effort until the Agent registers this request. */ }
        await new Promise((resolve) => window.setTimeout(resolve, 900));
      }
    };
    void pollStatus();
    try {
      const result = await api<Row>('/api/agent', { method: 'POST', headers: { 'X-Request-ID': requestId }, body: JSON.stringify({ message: current, history: nextHistory, session_id: sessionId || undefined }) });
      const reply = text(result.reply, 'Ответ пуст.');
      const complete = [...nextHistory, { role: 'assistant' as const, content: reply }];
      setHistory(complete); setSessionId(text(result.session_id, '')); sessionStorage.setItem(HISTORY_KEY, JSON.stringify(complete));
      if (typeof result.session_id === 'string') sessionStorage.setItem(SESSION_KEY, result.session_id);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось получить ответ агента'); }
    finally { poll = false; setBusy(false); setProgress(''); }
  }

  function reset() {
    setHistory([]); setSessionId(''); sessionStorage.removeItem(HISTORY_KEY); sessionStorage.removeItem(SESSION_KEY); setError('');
  }

  return <Layout title="AI Agent"><Panel title="Спросите о сделках, лидах и сотрудниках" eyebrow="MCP · SEMANTIC BI">
    <p className="panel-subtitle">Ответы строятся по данным CRM через разрешённые MCP-инструменты.</p>
    <div className="chat-transcript" aria-live="polite">{history.length ? history.map((item, index) => <article className={`chat-message ${item.role}`} key={`${index}-${item.role}`}><b>{item.role === 'user' ? 'Вы' : 'AI Agent'}</b><p>{item.content}</p></article>) : <p className="empty-state">Например: «Сколько активных сделок без активности больше 10 дней?»</p>}</div>
    {error && <Notice error>{error}</Notice>}{busy && <p className="panel-subtitle" role="status">{progress || 'Выполняю запрос…'}</p>}
    <form className="react-form chat-composer" onSubmit={submit}><label htmlFor="agent-message">Сообщение</label><textarea id="agent-message" rows={3} maxLength={12000} value={message} onChange={(event) => setMessage(event.target.value)} placeholder="Сформулируйте вопрос по данным CRM" disabled={busy} /><div className="react-actions"><button className="refresh" type="submit" disabled={busy || !message.trim()}>{busy ? 'Обрабатываю…' : 'Отправить'}</button><button className="secondary-button" type="button" onClick={reset} disabled={busy}>Новый диалог</button></div></form>
  </Panel></Layout>;
}
