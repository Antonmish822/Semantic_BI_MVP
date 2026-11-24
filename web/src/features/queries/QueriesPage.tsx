import { useEffect, useState, type FormEvent } from 'react';
import { adminHeaders, api, rows, text, type Row } from '../../shared/api';
import { Layout, Notice, Panel } from '../../shared/Layout';

export function QueriesPage() {
  const [catalog, setCatalog] = useState<Row[]>([]);
  const [selected, setSelected] = useState<Row | null>(null);
  const [query, setQuery] = useState('SELECT * WHERE { ?subject ?predicate ?object } LIMIT 100');
  const [parameters, setParameters] = useState<Record<string, string>>({});
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [token, setToken] = useState(() => sessionStorage.getItem('toolAdminToken') || '');
  const [result, setResult] = useState<unknown>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const params = [...new Set([...query.matchAll(/\{\{([A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*)\}\}/g)].map((match) => match[1]))];

  async function refresh() { try { const data = await api<Row>('/api/published-queries'); setCatalog(rows(data.queries)); } catch (cause) { setError(cause instanceof Error ? cause.message : 'Каталог недоступен'); } }
  useEffect(() => {
    void refresh();
    try {
      const draft = JSON.parse(sessionStorage.getItem('agentPublishedQueryDraft') || 'null') as Row | null;
      if (draft && typeof draft.id === 'string' && typeof draft.query === 'string' && typeof draft.title === 'string') {
        setSelected(draft); setQuery(draft.query); setTitle(draft.title); sessionStorage.removeItem('agentPublishedQueryDraft');
      }
    } catch { sessionStorage.removeItem('agentPublishedQueryDraft'); }
  }, []);

  async function run(event: FormEvent) {
    event.preventDefault(); setError(''); setResult(null); setBusy(true);
    try {
      const values = Object.fromEntries(params.map((name) => [name, parameters[name] || '']));
      const data = await api<Row>('/api/agent/query', { method: 'POST', body: JSON.stringify({ query, ...(params.length ? { parameters: values } : {}) }) });
      setResult(data);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Запрос не выполнен'); }
    finally { setBusy(false); }
  }

  async function publish(event: FormEvent) {
    event.preventDefault(); setError(''); setNotice('');
    try {
      sessionStorage.setItem('toolAdminToken', token);
      const response = await api<Row>('/api/published-queries', { method: 'POST', headers: adminHeaders(), body: JSON.stringify({ title, description, query }) });
      setNotice(`Опубликован запрос «${text((response.query as Row)?.title, title)}».`); setTitle(''); setDescription(''); await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось опубликовать запрос'); }
  }

  async function remove(id: string) {
    if (!window.confirm('Удалить этот запрос из общего каталога?')) return;
    try { await api(`/api/published-queries/${encodeURIComponent(id)}`, { method: 'DELETE', headers: adminHeaders() }); setNotice('Запрос удалён.'); await refresh(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось удалить запрос'); }
  }

  return <Layout title="Отладчик запросов"><nav className="section-tabs" aria-label="Инструменты"><a className="section-tab" href="/tools/ontology">Онтология</a><a className="section-tab active" href="/tools/queries">Отладчик запросов</a></nav><div className="react-grid query-grid"><Panel title="Опубликованные запросы" eyebrow={`${catalog.length} В КАТАЛОГЕ`}><div className="react-list">{catalog.map((item) => <button className={`react-list-item${selected?.id === item.id ? ' active' : ''}`} key={text(item.id)} onClick={() => { setSelected(item); setQuery(text(item.query, '')); setTitle(text(item.title, '')); setDescription(text(item.description, '')); }}><b>{text(item.title)}</b><small>{text(item.description, 'Без описания')}</small></button>)}{!catalog.length && <p className="empty-state">Каталог пока пуст.</p>}</div>
    {selected && <button className="secondary-button" onClick={() => remove(text(selected.id))}>Удалить выбранный</button>}</Panel>
    <Panel title="Редактор SPARQL" eyebrow="ЗАПУСК ЧЕРЕЗ MCP"><form className="react-form" onSubmit={run}><label htmlFor="query-editor">Запрос</label><textarea id="query-editor" rows={11} spellCheck={false} value={query} onChange={(event) => setQuery(event.target.value)} />{params.map((name) => <label key={name}>{name}<input value={parameters[name] || ''} onChange={(event) => setParameters({ ...parameters, [name]: event.target.value })} required /></label>)}<button className="refresh" disabled={busy}>{busy ? 'Проверяю…' : 'Проверить через MCP'}</button></form>
    <details className="react-publish"><summary>Опубликовать в общем каталоге</summary><form className="react-form" onSubmit={publish}><label>Название<input maxLength={120} value={title} onChange={(event) => setTitle(event.target.value)} required /></label><label>Описание<textarea maxLength={500} rows={2} value={description} onChange={(event) => setDescription(event.target.value)} /></label><label>Токен администратора<input type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} /></label><button className="secondary-button">Опубликовать</button></form></details>
    {error && <Notice error>{error}</Notice>}{notice && <Notice>{notice}</Notice>}{result !== null && <pre className="react-json">{JSON.stringify(result, null, 2)}</pre>}</Panel></div></Layout>;
}

export function PublishedQueriesPage() {
  const [catalog, setCatalog] = useState<Row[]>([]);
  const [selected, setSelected] = useState<Row | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [token, setToken] = useState(() => sessionStorage.getItem('toolAdminToken') || '');
  const [result, setResult] = useState<unknown>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  async function refresh() {
    try { const data = await api<Row>('/api/published-queries'); setCatalog(rows(data.queries)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Каталог недоступен'); }
  }
  useEffect(() => { void refresh(); }, []);
  const params = Array.isArray(selected?.parameters) ? selected.parameters.filter((item): item is string => typeof item === 'string') : [];

  async function run() {
    if (!selected) return;
    setBusy(true); setError(''); setResult(null);
    try {
      const parameters = Object.fromEntries(params.map((name) => {
        const value = values[name] ?? '';
        return [name, value];
      }));
      const data = await api<Row>('/api/agent/query', { method: 'POST', body: JSON.stringify({ query: selected.query, ...(params.length ? { parameters } : {}) }) });
      setResult(data);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Запрос не выполнен'); }
    finally { setBusy(false); }
  }

  async function remove() {
    if (!selected || !window.confirm(`Удалить «${text(selected.title)}» из общего каталога?`)) return;
    sessionStorage.setItem('toolAdminToken', token);
    try { await api(`/api/published-queries/${encodeURIComponent(text(selected.id))}`, { method: 'DELETE', headers: adminHeaders() }); setSelected(null); setNotice('Запрос удалён.'); await refresh(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось удалить запрос'); }
  }

  return <Layout title="Опубликованные запросы"><div className="react-toolbar"><p className="eyebrow">ОБЩИЙ КАТАЛОГ · ЗАПУСК ЧЕРЕЗ MCP</p><button className="secondary-button" onClick={() => void refresh()}>Обновить</button></div>
    {error && <Notice error>{error}</Notice>}{notice && <Notice>{notice}</Notice>}
    <div className="react-grid query-grid"><Panel title="Запросы" eyebrow={`${catalog.length} В КАТАЛОГЕ`}><div className="react-list">{catalog.map((item) => <button className={`react-list-item${selected?.id === item.id ? ' active' : ''}`} key={text(item.id)} onClick={() => { setSelected(item); setValues({}); setResult(null); }}><b>{text(item.title)}</b><small>{text(item.description, 'Без описания')}</small></button>)}{!catalog.length && <p className="empty-state">Опубликованных запросов пока нет.</p>}</div></Panel>
      <Panel title={text(selected?.title, 'Выберите запрос')} eyebrow="ВЫПОЛНЕНИЕ ПО ШАБЛОНУ">{selected ? <><p className="panel-subtitle">{text(selected.description, 'Параметры запроса не сохраняются.')}</p><pre className="react-json">{text(selected.query)}</pre><div className="react-form">{params.map((name) => <label key={name}>{name}<input value={values[name] ?? ''} onChange={(event) => setValues({ ...values, [name]: event.target.value })} required /></label>)}<div className="react-actions"><button type="button" className="refresh" onClick={() => void run()} disabled={busy}>{busy ? 'Выполняю…' : 'Выполнить через MCP'}</button><button type="button" className="secondary-button" onClick={() => { window.sessionStorage.setItem('agentPublishedQueryDraft', JSON.stringify({ id: selected.id, title: selected.title, query: selected.query })); window.location.href = '/tools/queries'; }}>Открыть в отладчике</button></div></div>
        <div className="react-admin-inline"><label className="team-filter">Токен администратора<input type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} /></label><button className="secondary-button" onClick={() => void remove()}>Удалить из каталога</button></div>
        {result !== null && <pre className="react-json">{JSON.stringify(result, null, 2)}</pre>}</> : <p className="empty-state">Выберите запрос слева. Все выборки повторно проверяются MCP и действующей политикой.</p>}</Panel></div>
  </Layout>;
}
