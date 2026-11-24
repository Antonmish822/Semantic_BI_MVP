import { useEffect, useState, type ChangeEvent, type FormEvent } from 'react';
import { adminHeaders, api, rows, text, type Row } from '../../shared/api';
import { Layout, Notice, Panel } from '../../shared/Layout';

type OntologyFile = { slug: string; sha256: string; text: string; publication_status?: string; publication_history?: Row[] };

export function OntologyPage() {
  const [token, setToken] = useState(() => sessionStorage.getItem('toolAdminToken') || '');
  const [files, setFiles] = useState<Row[]>([]);
  const [quarantine, setQuarantine] = useState<Row[]>([]);
  const [slug, setSlug] = useState('');
  const [saved, setSaved] = useState<OntologyFile | null>(null);
  const [source, setSource] = useState('');
  const [namespace, setNamespace] = useState('');
  const [stats, setStats] = useState<Row | null>(null);
  const [assistantPrompt, setAssistantPrompt] = useState('');
  const [assistantResult, setAssistantResult] = useState<Row | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const headers = () => { sessionStorage.setItem('toolAdminToken', token); return adminHeaders(); };

  async function refresh() {
    try {
      const [list, archived] = await Promise.all([api<Row>('/api/ontologies'), api<Row>('/api/ontologies/quarantine')]);
      setFiles(rows(list.ontologies)); setQuarantine(rows(archived.items));
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось загрузить онтологии'); }
  }
  useEffect(() => { void refresh(); }, []);

  async function openFile(fileSlug: string) {
    setError(''); setNotice('');
    try {
      const data = await api<OntologyFile>(`/api/ontologies/${encodeURIComponent(fileSlug)}`);
      setSaved(data); setSlug(data.slug); setSource(data.text);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось открыть TTL'); }
  }

  function filePicked(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]; if (!file) return;
    setSlug(file.name.replace(/\.ttl$/i, ''));
    void file.text().then(setSource);
  }

  async function save() {
    setBusy(true); setError(''); setNotice('');
    try {
      const data = saved
        ? await api<OntologyFile>(`/api/ontologies/${encodeURIComponent(slug)}`, { method: 'PUT', headers: headers(), body: JSON.stringify({ text: source, expected_sha256: saved.sha256 }) })
        : await api<OntologyFile>('/api/ontologies', { method: 'POST', headers: headers(), body: JSON.stringify({ slug, text: source }) });
      setSaved({ ...data, text: source }); setNotice('Файл сохранён. Опубликованный граф не изменён.'); await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось сохранить файл'); }
    finally { setBusy(false); }
  }

  async function validate() {
    setError('');
    try { const result = await api<Row>('/api/ontologies/validate', { method: 'POST', headers: headers(), body: JSON.stringify({ slug, text: source }) }); setNotice(`Turtle корректен · ${text(result.uri)}`); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Ошибка проверки TTL'); }
  }

  async function publish() {
    if (!saved || source !== saved.text) { setError('Сначала сохраните изменения.'); return; }
    if (!window.confirm(`Опубликовать сохранённую версию «${slug}» в Graph Store?`)) return;
    setBusy(true); setError('');
    try { await api(`/api/ontologies/${encodeURIComponent(slug)}/publish`, { method: 'POST', headers: headers(), body: JSON.stringify({ expected_sha256: saved.sha256 }) }); setNotice('Публикация подтверждена Graph Store.'); await openFile(slug); await refresh(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Публикация не выполнена'); }
    finally { setBusy(false); }
  }

  async function rollback() {
    if (!saved || !window.confirm(`Откатить опубликованную онтологию «${slug}» на предыдущую подтверждённую ревизию?`)) return;
    setBusy(true); setError('');
    try {
      const result = await api<OntologyFile>(`/api/ontologies/${encodeURIComponent(slug)}/rollback`, { method: 'POST', headers: headers(), body: JSON.stringify({}) });
      setSaved(result); setSource(result.text); setNotice('Граф и файл восстановлены на предыдущую подтверждённую ревизию.'); await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Откат не выполнен'); }
    finally { setBusy(false); }
  }

  async function removeFile() {
    if (!saved) return;
    setError('');
    try {
      const preview = await api<Row>(`/api/ontologies/${encodeURIComponent(slug)}/delete-preview`, { method: 'POST', headers: headers(), body: JSON.stringify({}) });
      const fingerprint = text(preview.graph_fingerprint, 'нет опубликованного графа');
      if (!window.confirm(`Удалить ${slug} в карантин? Fingerprint графа: ${fingerprint}`)) return;
      const result = await api<Row>(`/api/ontologies/${encodeURIComponent(slug)}/delete`, { method: 'POST', headers: headers(), body: JSON.stringify(preview) });
      setNotice(text(result.message, 'Онтология помещена в карантин.')); setSaved(null); setSource(''); setSlug(''); await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Удаление не выполнено'); }
  }

  async function restore(operationId: string) {
    if (!window.confirm('Восстановить файлы, ревизии и снимок Graph Store из карантина?')) return;
    try { const result = await api<Row>(`/api/ontologies/${encodeURIComponent(text(quarantine.find((item) => item.operation_id === operationId)?.slug))}/restore`, { method: 'POST', headers: headers(), body: JSON.stringify({ operation_id: operationId }) }); setNotice(text(result.message, 'Онтология восстановлена.')); await refresh(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось восстановить онтологию'); }
  }

  async function classStats(event: FormEvent) {
    event.preventDefault(); setError('');
    try { setStats(await api<Row>('/api/ontologies/class-statistics', { method: 'POST', headers: headers(), body: JSON.stringify({ namespace }) })); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Статистика недоступна'); }
  }

  async function askAssistant(event: FormEvent) {
    event.preventDefault(); if (!assistantPrompt.trim()) return;
    setBusy(true); setError('');
    try {
      setAssistantResult(await api<Row>('/api/ontology-assistant/suggest', { method: 'POST', headers: headers(), body: JSON.stringify({ slug, editor_text: source, file_sha256: saved?.sha256 || null, message: assistantPrompt, history: [] }) }));
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Помощник не ответил'); }
    finally { setBusy(false); }
  }

  return <Layout title="Управление онтологиями"><nav className="section-tabs" aria-label="Инструменты"><a className="section-tab active" href="/tools/ontology">Онтология</a><a className="section-tab" href="/tools/queries">Отладчик запросов</a></nav><div className="react-admin-bar"><label className="team-filter">Токен администратора<input type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} /></label><button className="secondary-button" onClick={() => { sessionStorage.setItem('toolAdminToken', token); void refresh(); }}>Проверить доступ</button></div>
    {error && <Notice error>{error}</Notice>}{notice && <Notice>{notice}</Notice>}
    <div className="react-grid ontology-grid"><Panel title="Turtle-файлы и карантин" eyebrow="ИСТОЧНИК ИСТИНЫ — TTL"><div className="react-list">{files.map((file) => <button className="react-list-item" key={text(file.slug)} onClick={() => void openFile(text(file.slug))}><b>{text(file.slug)}.ttl</b><small>{text(file.publication_status, 'статус не проверен')}</small></button>)}{!files.length && <p className="empty-state">Файлы не загружены.</p>}</div><h3>Карантин</h3><div className="react-list">{quarantine.map((item, index) => <div className="react-list-item" key={text(item.operation_id, String(index))}><b>{text(item.slug)}.ttl</b><small>{text(item.deleted_at, 'архив')}</small><button className="secondary-button" onClick={() => void restore(text(item.operation_id))}>Восстановить</button></div>)}{!quarantine.length && <p className="empty-state">Карантин пуст.</p>}</div></Panel>
      <Panel title={saved ? `${saved.slug}.ttl` : 'Новая онтология'} eyebrow="РЕДАКТОР TURTLE"><div className="react-form"><label>Slug<input value={slug} onChange={(event) => setSlug(event.target.value)} placeholder="my-ontology" /></label><label>Загрузить локальный TTL<input type="file" accept=".ttl,text/turtle" onChange={filePicked} /></label><label>Текст Turtle<textarea rows={18} spellCheck={false} value={source} onChange={(event) => setSource(event.target.value)} /></label><div className="react-actions"><button className="secondary-button" onClick={() => void validate()}>Проверить</button><button className="refresh" disabled={busy || !slug || !source} onClick={() => void save()}>{busy ? 'Сохраняю…' : 'Сохранить'}</button><button className="secondary-button" disabled={!saved || busy} onClick={() => void publish()}>Опубликовать</button><button className="secondary-button" disabled={!saved || busy} onClick={() => void rollback()}>Откатить публикацию</button><button className="secondary-button" disabled={!saved || busy} onClick={() => void removeFile()}>Удалить в карантин</button></div></div><p className="panel-subtitle">SHA-256: {saved?.sha256 || 'ещё не сохранён'} · Статус Graph Store: {text(saved?.publication_status, 'не проверен')}</p></Panel></div>
    <div className="react-grid"><Panel title="Статистика классов" eyebrow="GRAPH STORE"><form className="react-form" onSubmit={classStats}><label>Пространство имён<input required maxLength={512} value={namespace} onChange={(event) => setNamespace(event.target.value)} placeholder="https://example.org/ontology#" /></label><button className="secondary-button">Показать статистику</button></form>{stats && <pre className="react-json">{JSON.stringify(stats, null, 2)}</pre>}</Panel><Panel title="Помощник онтологий" eyebrow="ПРЕДЛОЖЕНИЕ БЕЗ АВТОЗАПИСИ"><form className="react-form" onSubmit={askAssistant}><label>Задача<textarea rows={4} maxLength={8000} value={assistantPrompt} onChange={(event) => setAssistantPrompt(event.target.value)} placeholder="Опишите желаемое семантическое правило" /></label><button className="secondary-button" disabled={busy || !saved}>Предложить правку</button></form>{assistantResult && <div className="assistant-proposal"><p>{text(assistantResult.summary, 'Предложение готово.')}</p>{rows(assistantResult.warnings).map((warning, index) => <p className="panel-subtitle" key={index}>{text(warning)}</p>)}<pre className="react-json">{text(assistantResult.proposed_ttl, 'Помощник не предложил полный TTL.')}</pre><button className="secondary-button" onClick={() => { if (typeof assistantResult.proposed_ttl === 'string') setSource(assistantResult.proposed_ttl); }}>Применить к редактору</button></div>}</Panel></div>
  </Layout>;
}
