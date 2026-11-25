import { useEffect, useRef, useState } from 'react';
import { apiJson } from './api';
import { publishedDraftKey, type PublishedDraft } from './browserStorage';
import { copyText } from './clipboard';
import { ParameterFields, QueryResult, ResultSwitch, type ResultMode } from './QueryResult';
import { collectParameters, describeParameters, ParameterError, parseQueryResult, runQuery,
  toolsAuthorization, type QueryParameter, type QueryValues } from './queryTools';
import { useApiRequest } from './useApiRequest';
import { useToolsToken } from './useToolsToken';

type PublishedQuery = PublishedDraft & { updated_at?: string; description?: string; parameters?: string[] };
type Catalog = { admin_enabled?: boolean; queries?: PublishedQuery[] };
type Notice = { message: string; error?: boolean };
const loadCatalog = (token: string, signal: AbortSignal) =>
  apiJson<Catalog>('/api/published-queries', { signal, headers: toolsAuthorization(token) });

function QueryCard({ item, canDelete, notice, remove }: {
  item: PublishedQuery; canDelete: boolean; notice: (notice: Notice) => void; remove: () => void;
}) {
  const [values, setValues] = useState<QueryValues>({});
  const [mode, setMode] = useState<ResultMode>('table');
  const [result, setResult] = useState<{ data: unknown } | null>(null);
  const [status, setStatus] = useState('');
  const [running, setRunning] = useState(false);
  const request = useRef<AbortController | null>(null);
  const params = Array.isArray(item.parameters) ? item.parameters : [];
  let fields: QueryParameter[] = [];
  let parameterError = '';
  if (params.length) {
    try { fields = describeParameters(item.query); }
    catch (cause) { parameterError = cause instanceof Error ? cause.message : 'Ошибка параметров'; }
  }
  useEffect(() => () => request.current?.abort(), []);

  async function run() {
    if (request.current || parameterError) return;
    let parameters: QueryValues;
    try { parameters = collectParameters(fields, values); }
    catch (error) {
      if (error instanceof ParameterError) {
        const index = fields.findIndex(({ name }) => name === error.parameter);
        document.getElementById(`published-query-${item.id}-param-${index}`)?.focus();
      }
      setStatus(error instanceof Error ? error.message : 'Ошибка параметров');
      return;
    }
    const controller = new AbortController();
    request.current = controller;
    setRunning(true);
    setStatus('Выполняю через MCP…');
    setResult(null);
    const id = globalThis.crypto?.randomUUID?.();
    try {
      const data = await runQuery(item.query, parameters, id, controller.signal);
      if (controller.signal.aborted) return;
      setResult({ data: parseQueryResult(data) });
      setStatus(`Завершено · request_id ${data.request_id || id || '—'}`);
    } catch (error) {
      if (!controller.signal.aborted) setStatus(error instanceof Error ? error.message : 'Не удалось выполнить запрос.');
    } finally {
      if (!controller.signal.aborted) {
        request.current = null;
        setRunning(false);
      }
    }
  }
  function openDebugger() {
    const { id, title, query } = item;
    try {
      sessionStorage.setItem(publishedDraftKey, JSON.stringify({ id, title, query }));
      window.location.href = '/tools/queries';
    } catch {
      notice({ message: 'Не удалось передать запрос в отладчик текущей вкладки.', error: true });
    }
  }

  return <details className="published-query-card">
    <summary className="published-query-card-heading">
      <h3>{item.title}</h3>
      <span className="published-query-date">{item.updated_at ? new Date(item.updated_at).toLocaleString() : ''}</span>
    </summary>
    {item.description && <p className="published-query-description">{item.description}</p>}
    <p className="published-query-params">{params.length
      ? `Параметры: ${params.map((name) => `{{${name}}}`).join(', ')}` : 'Без параметров'}</p>
    <section className="published-query-runbox">
      {parameterError && <p className="error-banner">{parameterError}</p>}
      {!!params.length && <ParameterFields fields={fields} values={values} prefix={`published-query-${item.id}-param`}
        setValue={(name, value) => setValues((current) => ({ ...current, [name]: value }))}/>}
      <div className="published-query-run-controls">
        <button className="refresh" type="button" disabled={running || !!parameterError} onClick={() => void run()}>Выполнить запрос</button>
        <span className="published-query-run-status">{status}</span>
      </div>
      <div className="query-result-heading published-query-result-heading">
        <h4>Результат выполнения</h4>
        <ResultSwitch mode={mode} select={setMode} hidden={!result}/>
      </div>
      <div className="query-result published-query-result" aria-live="polite">
        {result ? <QueryResult mode={mode} result={result.data} filename={`published-query-${item.id}`} onError={setStatus}/>
          : !status ? 'Запрос ещё не запускался.' : null}
      </div>
    </section>
    <details className="published-query-source">
      <summary>Показать SPARQL-шаблон</summary><pre className="published-query-code">{item.query}</pre>
    </details>
    <div className="published-query-actions">
      <button type="button" className="secondary-button" onClick={openDebugger}>Открыть в отладчике</button>
      <button type="button" className="secondary-button" onClick={() => {
        void copyText(item.query).then(
          () => notice({ message: 'Шаблон скопирован. Плейсхолдеры {{имя}} сохранены.' }),
          () => notice({ message: 'Не удалось скопировать запрос: проверьте разрешение браузера на буфер обмена.', error: true }),
        );
      }}>Копировать</button>
      {canDelete && <button type="button" className="secondary-button published-query-delete" onClick={remove}>Удалить</button>}
    </div>
  </details>;
}

export function QueryCatalogPage() {
  const [token, setToken] = useToolsToken();
  const [loadToken, setLoadToken] = useState(token);
  const [revision, setRevision] = useState(0);
  const [notice, setNotice] = useState<Notice>();
  const catalog = useApiRequest(loadToken, loadCatalog, revision);
  const deletion = useRef<AbortController | null>(null);
  useEffect(() => {
    document.title = 'Запросы — CRM Pulse';
    return () => deletion.current?.abort();
  }, []);

  function refresh() {
    setLoadToken(token);
    setRevision((value) => value + 1);
  }
  async function remove(item: PublishedQuery) {
    if (deletion.current || !window.confirm(`Удалить «${item.title}» из общего каталога?`)) return;
    const controller = new AbortController();
    deletion.current = controller;
    try {
      await apiJson(`/api/published-queries/${encodeURIComponent(item.id)}`, {
        method: 'DELETE', headers: toolsAuthorization(token), signal: controller.signal,
      });
      if (!controller.signal.aborted) {
        setNotice({ message: 'Запрос удалён из каталога.' });
        refresh();
      }
    } catch (error) {
      if (!controller.signal.aborted) setNotice({ message: error instanceof Error ? error.message : 'Не удалось удалить запрос.', error: true });
    } finally {
      deletion.current = null;
    }
  }
  const items = Array.isArray(catalog.data?.queries) ? catalog.data.queries : [];
  const enabled = !!catalog.data?.admin_enabled;
  return <main className="shell">
    <header className="topbar">
      <div className="brand"><div className="brand-mark" aria-hidden="true">P</div><div><p className="eyebrow">SEMANTIC BI · BITRIX24</p><h1>CRM Pulse</h1></div></div>
      <div className="header-actions published-query-admin">
        <label className="team-filter"><span>Токен администратора инструментов</span>
          <input id="published-query-token" type="password" autoComplete="off" placeholder="ONTOLOGY_ADMIN_TOKEN"
            value={token} onChange={(event) => setToken(event.target.value)}/>
        </label>
        <a className="auth-link" href="/auth/logout">Выйти</a>
      </div>
    </header>
    <div id="published-query-notice" className={notice && !notice.error ? 'error-banner query-notice-success' : 'error-banner'}
      role="status" hidden={!notice}>{notice?.message}</div>
    <section className="panel published-query-panel" aria-labelledby="published-query-title">
      <div className="panel-heading">
        <div><p className="eyebrow">ОБЩИЙ КАТАЛОГ</p><h2 id="published-query-title">Опубликованные запросы</h2>
          <p className="panel-subtitle">Каталог доступен участникам проекта. Параметры можно заполнить и выполнить прямо на этой странице; их значения не сохраняются.</p>
        </div>
        <button id="published-query-refresh" className="secondary-button" type="button" disabled={catalog.status === 'loading'} onClick={refresh}>Обновить</button>
      </div>
      <p id="published-query-admin-status" className="panel-subtitle" role="status" data-admin-enabled={catalog.data ? enabled : undefined}>
        {catalog.data && (enabled ? 'Публикация и удаление доступны администратору с ONTOLOGY_ADMIN_TOKEN.'
          : 'Публикация и удаление отключены: серверу не задан ONTOLOGY_ADMIN_TOKEN.')}
      </p>
      <div id="published-query-list" className="published-query-list">
        {catalog.status === 'error' ? <p className="assistant-error">{catalog.error}</p>
          : !catalog.data ? <p className="empty-state">Загружаю каталог…</p>
          : !items.length ? <p className="empty-state">Опубликованных запросов пока нет. Проверенный запрос можно опубликовать из Инструменты → Запросы.</p>
          : items.map((item) => <QueryCard key={JSON.stringify([revision, item.id, item.query])} item={item}
            canDelete={enabled} notice={setNotice} remove={() => void remove(item)}/>)}
      </div>
    </section>
  </main>;
}
