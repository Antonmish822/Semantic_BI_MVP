import { useEffect, useReducer, useRef, useState } from 'react';
import { apiJson } from './api';
import { readPublishedDraft, readSavedPrompts, savedPromptsKey, type SavedPrompt } from './browserStorage';
import { copyText } from './clipboard';
import { DashboardHeader } from './DashboardChrome';
import { ParameterFields, QueryResult, ResultSwitch, type ResultMode } from './QueryResult';
import { asRecord, collectParameters, describeParameters, ParameterError, parseQueryResult, runQuery,
  toolsAuthorization, type QueryParameter, type QueryValues } from './queryTools';
import { useToolsToken } from './useToolsToken';

type Entry = SavedPrompt & { draftTitle?: string };
type Verification = { data: unknown; passed: boolean };
type Editor = {
  entry: Entry | null; text: string; values: QueryValues; dirty: boolean;
  running: boolean; status: string; result: Verification | null; initial: boolean;
};
type Action =
  | { type: 'select'; entry: Entry }
  | { type: 'text'; value: string }
  | { type: 'parameter'; name: string; value: string }
  | { type: 'run' }
  | { type: 'finish'; status: string; result?: Verification }
  | { type: 'saved' }
  | { type: 'status'; message: string };
const blank: Editor = {
  entry: null, text: '', values: {}, dirty: false, running: false, status: '', result: null, initial: true,
};
function reducer(state: Editor, action: Action): Editor {
  switch (action.type) {
    case 'select': return { ...blank, entry: action.entry, text: action.entry.sparql };
    case 'text': return { ...state, text: action.value, dirty: true, running: false,
      status: 'Есть несохранённые изменения.', result: state.result && { ...state.result, passed: false } };
    case 'parameter': return { ...state, values: { ...state.values, [action.name]: action.value }, running: false,
      result: state.result && { ...state.result, passed: false }, status: state.running ? '' : state.status };
    case 'run': return { ...state, running: true, status: 'Выполняю через MCP…', result: null, initial: false };
    case 'finish': return { ...state, running: false, status: action.status, result: action.result ?? null };
    case 'saved': return { ...state, dirty: false, status: '' };
    case 'status': return { ...state, status: action.message };
  }
}

function queryEntries(): Entry[] {
  const prompts: Entry[] = readSavedPrompts(localStorage).filter((item) => item.sparql.trim());
  const draft = readPublishedDraft(sessionStorage);
  if (draft?.query.trim()) prompts.push({
    prompt: `Опубликованный запрос: ${draft.title} · ${draft.id}`, draftTitle: draft.title, sparql: draft.query,
  });
  return prompts;
}

export function QueryDebuggerPage() {
  const [entries, setEntries] = useState(queryEntries);
  const [editor, dispatch] = useReducer(reducer, entries, (items) => {
    const draft = items.find((item) => item.draftTitle !== undefined);
    return draft ? reducer(blank, { type: 'select', entry: draft }) : blank;
  });
  const [mode, setMode] = useState<ResultMode>('table');
  const [notice, setNotice] = useState<{ message: string; error?: boolean }>();
  const [token, setToken] = useToolsToken();
  const [title, setTitle] = useState(editor.entry?.draftTitle ?? '');
  const [description, setDescription] = useState('');
  const [publishStatus, setPublishStatus] = useState('');
  const [published, setPublished] = useState(false);
  const titleField = useRef<HTMLInputElement>(null);
  const tokenField = useRef<HTMLInputElement>(null);
  const running = useRef<AbortController | null>(null);
  const publishing = useRef<AbortController | null>(null);
  const [publishBusy, setPublishBusy] = useState(false);
  let fields: QueryParameter[] = [];
  let parameterError = '';
  try { fields = describeParameters(editor.text); }
  catch (cause) { parameterError = cause instanceof Error ? cause.message : 'Ошибка параметров'; }

  useEffect(() => {
    document.title = 'Инструменты — Запросы — CRM Pulse';
    return () => { running.current?.abort(); publishing.current?.abort(); };
  }, []);
  useEffect(() => {
    if (!editor.dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [editor.dirty]);

  function abortVerification() {
    running.current?.abort();
    running.current = null;
  }
  function choose(entry: Entry) {
    if (editor.dirty && !window.confirm('Отбросить несохранённые изменения текущего запроса?')) return;
    abortVerification();
    if (entry.draftTitle !== undefined) setTitle(entry.draftTitle);
    setPublishStatus('');
    setPublished(false);
    setNotice(undefined);
    dispatch({ type: 'select', entry });
  }
  function save() {
    if (!editor.entry || editor.entry.draftTitle !== undefined) return;
    const source = readSavedPrompts(localStorage);
    const saved = source.find((item) => item.prompt === editor.entry?.prompt);
    if (!saved) {
      setNotice({ message: 'Связанный промпт больше не найден в хранилище браузера.', error: true });
      return;
    }
    saved.sparql = editor.text;
    try {
      localStorage.setItem(savedPromptsKey, JSON.stringify(source));
      setEntries(queryEntries());
      dispatch({ type: 'saved' });
      setNotice({ message: 'Изменения запроса сохранены вместе с промптом.' });
    } catch {
      setNotice({ message: 'Не удалось записать изменения в хранилище браузера.', error: true });
    }
  }

  async function verify() {
    const query = editor.text.trim();
    if (!query || parameterError) {
      dispatch({ type: 'status', message: parameterError || 'Введите SPARQL перед запуском.' });
      return;
    }
    if (running.current) return;
    dispatch({ type: 'run' });
    setPublishStatus('');
    setPublished(false);
    let parameters: QueryValues;
    try { parameters = collectParameters(fields, editor.values); }
    catch (cause) {
      if (cause instanceof ParameterError) {
        const index = fields.findIndex(({ name }) => name === cause.parameter);
        document.getElementById(`query-parameter-${index}`)?.focus();
      }
      dispatch({ type: 'finish', status: cause instanceof Error ? cause.message : 'Ошибка параметров' });
      return;
    }
    const controller = new AbortController();
    running.current = controller;
    const id = globalThis.crypto?.randomUUID?.();
    try {
      const response = await runQuery(query, parameters, id, controller.signal);
      if (controller.signal.aborted) return;
      const data = parseQueryResult(response);
      dispatch({ type: 'finish', status: `Проверка завершена · request_id ${response.request_id || id || '—'}`,
        result: { data, passed: asRecord(data)?.status === 'ok' } });
    } catch (error) {
      if (!controller.signal.aborted) dispatch({ type: 'finish', status: error instanceof Error ? error.message : 'Не удалось выполнить запрос.' });
    } finally {
      if (running.current === controller) running.current = null;
    }
  }

  async function publish() {
    if (!editor.result?.passed || publishing.current) return;
    if (!title.trim()) {
      titleField.current?.focus();
      setPublishStatus('Укажите название запроса.');
      return;
    }
    if (!token) {
      tokenField.current?.focus();
      setPublishStatus('Введите ONTOLOGY_ADMIN_TOKEN администратора инструментов.');
      return;
    }
    const controller = new AbortController();
    publishing.current = controller;
    setPublishBusy(true);
    setPublishStatus('Публикую шаблон…');
    try {
      await apiJson('/api/published-queries', {
        method: 'POST', signal: controller.signal,
        headers: { 'Content-Type': 'application/json', ...toolsAuthorization(token) },
        body: JSON.stringify({ title: title.trim(), description: description.trim(), query: editor.text.trim() }),
      });
      if (controller.signal.aborted) return;
      setPublishStatus('Запрос опубликован в общем каталоге. Открыть вкладку «Запросы».');
      setNotice({ message: 'Параметризованный шаблон опубликован. Он доступен на вкладке «Запросы».' });
      setPublished(true);
    } catch (error) {
      if (!controller.signal.aborted) setPublishStatus(error instanceof Error ? error.message : 'Не удалось опубликовать запрос.');
    } finally {
      publishing.current = null;
      if (!controller.signal.aborted) setPublishBusy(false);
    }
  }

  return <main className="shell">
    <DashboardHeader title="CRM Pulse">{null}</DashboardHeader>
    <div id="query-tool-notice" className={notice && !notice.error ? 'error-banner query-notice-success' : 'error-banner'}
      role="status" hidden={!notice}>{notice?.message}</div>
    <section className="panel query-tool-panel" aria-labelledby="query-tool-title">
      <div className="panel-heading"><div>
        <p className="eyebrow">РЕДАКТОР И ОТЛАДКА</p><h2 id="query-tool-title">Сохранённые SPARQL-запросы</h2>
      </div></div>
      <div className="query-tool-layout">
        <aside className="query-tool-list-panel" aria-label="Список сохранённых запросов">
          <div className="query-tool-list-heading"><h3>Запросы</h3><span id="saved-query-count" className="count-badge">{entries.length}</span></div>
          <div id="saved-query-list" className="saved-query-list">
            {!entries.length && <p className="empty-state">Сохранённых запросов пока нет.</p>}
            {entries.map((item, index) => {
              const selected = item.prompt === editor.entry?.prompt;
              return <button key={index} type="button" className={`saved-query-select${selected ? ' active' : ''}`}
                aria-pressed={selected} onClick={() => choose(item)}>
                <span className="saved-query-select-prompt">{item.prompt}</span>
                <span className="saved-query-select-detail">{`${item.sparql.split(/\r?\n/).length} строк SPARQL`}</span>
              </button>;
            })}
          </div>
        </aside>
        <div className="query-tool-editor">
          <p id="selected-query-prompt" className={editor.entry ? 'query-linked-prompt' : 'query-linked-prompt empty-state'}>
            {editor.entry ? editor.entry.draftTitle !== undefined ? editor.entry.prompt : `Промпт: ${editor.entry.prompt}`
              : 'Выберите запрос слева. Сохранённые запросы появятся после сохранения промпта с успешным вызовом sparql_select на вкладке AI Agent.'}
          </p>
          <label htmlFor="saved-query-editor">SPARQL</label>
          <textarea id="saved-query-editor" className="saved-query-editor" spellCheck={false} autoComplete="off"
            aria-label="Редактор SPARQL" disabled={!editor.entry} value={editor.text} onChange={(event) => {
              abortVerification();
              dispatch({ type: 'text', value: event.target.value });
            }}/>
          <div id="query-parameters" className="query-parameters" hidden={!fields.length && !parameterError}>
            {parameterError || (!!fields.length && <>
              <h3>Параметры запроса</h3>
              <p className="query-parameter-hint">Тип поля определяется по xsd-типу рядом с параметром. Дата и время отправляются с часовым поясом UTC; значения экранируются перед подстановкой.</p>
              <ParameterFields fields={fields} values={editor.values} prefix="query-parameter" setValue={(name, value) => {
                abortVerification();
                dispatch({ type: 'parameter', name, value });
              }}/>
            </>)}
          </div>
          <div className="query-tool-actions">
            <button id="query-run" className="refresh" type="button" disabled={!editor.entry || editor.running || publishBusy}
              onClick={() => void verify()}>Проверить через MCP</button>
            <button id="query-save" className="secondary-button" type="button"
              disabled={!editor.dirty || editor.entry?.draftTitle !== undefined || editor.running} onClick={save}>Сохранить изменения</button>
            <button id="query-copy" className="secondary-button" type="button" disabled={!editor.entry} onClick={() => {
              void copyText(editor.text).then(
                () => dispatch({ type: 'status', message: 'SPARQL скопирован.' }),
                () => dispatch({ type: 'status', message: 'Не удалось скопировать запрос. Проверьте разрешение браузера на буфер обмена.' }),
              );
            }}>Копировать</button>
          </div>
          <details id="query-publish-panel" className="query-publish-panel" hidden={!editor.result?.passed}>
            <summary>Опубликовать проверенный запрос в каталоге</summary>
            <label htmlFor="query-publish-title">Название<input id="query-publish-title" maxLength={120} autoComplete="off"
              ref={titleField} value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Например, Сделки за период"/></label>
            <label htmlFor="query-publish-description">Описание<textarea id="query-publish-description" maxLength={500} rows={2}
              value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Что показывает запрос"/></label>
            <label htmlFor="query-admin-token">Токен администратора инструментов<input id="query-admin-token" type="password"
              ref={tokenField} autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} placeholder="ключ администратора"/></label>
            <button id="query-publish" className="secondary-button" type="button"
              disabled={!editor.result?.passed || published || publishBusy} onClick={() => void publish()}>Опубликовать</button>
            <p id="query-publish-status" className="panel-subtitle" role="status" aria-live="polite">{publishStatus}</p>
          </details>
          <p id="query-editor-status" className="panel-subtitle" role="status" aria-live="polite">{editor.status}</p>
          <section className="query-result-panel" aria-labelledby="query-result-title">
            <div className="query-result-heading"><h3 id="query-result-title">Результат отладки</h3>
              <ResultSwitch mode={mode} select={setMode} idPrefix="query-result"/>
            </div>
            <div id="query-result" className="query-result" aria-live="polite">
              {editor.result ? <QueryResult mode={mode} result={editor.result.data} filename="query-debug-results"
                onError={(message) => dispatch({ type: 'status', message })}/> : editor.initial ? 'Запрос ещё не запускался.' : null}
            </div>
          </section>
        </div>
      </div>
      <p className="query-tool-policy-note">Инструмент не подключается к GraphDB напрямую. MCP повторно проверяет и ограничивает каждый запрос; в рабочем режиме допустимы только запросы, разрешённые текущей аналитической policy.</p>
    </section>
  </main>;
}
