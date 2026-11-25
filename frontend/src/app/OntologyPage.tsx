import { useEffect, useReducer, useRef, useState } from 'react';
import { ClassStatistics } from './ClassStatistics';
import { ontologyApi, ontologyPath, type AssistantConfig, type DeletionPreview, type OntologyFile, type Quarantined } from './ontologyApi';
import { OntologyAssistant } from './OntologyAssistant';
import { OntologyDeleteDialog } from './OntologyDeleteDialog';
import { useToolsToken } from './useToolsToken';

type Editor = {
  file: { slug: string; sha: string; text: string } | null;
  slug: string; text: string; meta: string; publication: string; imported: boolean;
};
type Action =
  | { type: 'open'; data: OntologyFile }
  | { type: 'save'; data: OntologyFile; text: string }
  | { type: 'import'; slug: string; text: string }
  | { type: 'rollback'; sha: string; text: string }
  | { type: 'slug'; slug: string }
  | { type: 'edit'; text: string }
  | { type: 'publication'; message: string }
  | { type: 'delete'; expires: string };
const initial: Editor = { file: null, slug: '', text: '', meta: 'Выберите файл или укажите slug нового файла.',
  publication: 'Статус: не опубликовано (пока не проверено)', imported: false };
const publicationTexts: Readonly<Record<string, string>> = {
  published: 'Статус: опубликовано · содержимое графа совпадает',
  unpublished: 'Статус: граф прочитан, но не совпадает с текущим файлом',
  diverged: 'Статус: расхождение текущего файла и опубликованного графа',
  unsupported: 'Статус: публикация доступна только в GraphDB',
  missing: 'Статус: граф отсутствует',
  unavailable: 'Статус: GraphDB недоступна или граф не удалось прочитать',
};
const unpublished = 'Статус: не опубликовано';
const isDirty = (editor: Editor) => editor.imported || editor.text !== (editor.file?.text ?? '');
function reducer(editor: Editor, action: Action): Editor {
  switch (action.type) {
    case 'open': {
      const { slug, sha256, text, filename, uri, publication_status, publication_history } = action.data;
      const latest = [...(publication_history ?? [])].reverse().find((row) => row.status === 'confirmed');
      const status = publication_status && Object.hasOwn(publicationTexts, publication_status)
        ? publicationTexts[publication_status] : unpublished;
      return { file: { slug, sha: sha256, text }, slug, text, imported: false,
        meta: `${filename} · граф ${uri} · SHA-256 ${sha256}`,
        publication: status + (latest ? ` · последняя подтверждённая публикация ${latest.sha256.slice(0, 12)} (${latest.at})` : '') };
    }
    case 'save': {
      const { slug, sha256, uri } = action.data;
      return { file: { slug, sha: sha256, text: action.text }, slug, text: action.text, imported: false,
        meta: `${slug}.ttl · граф ${uri}`, publication: unpublished };
    }
    case 'import':
      return { ...initial, slug: action.slug, text: action.text, imported: true,
        meta: 'Локальный файл · ещё не сохранён', publication: 'Статус: есть несохранённые изменения' };
    case 'rollback':
      return { ...editor, text: action.text,
        meta: `${editor.slug.trim()}.ttl · открыта опубликованная ревизия ${action.sha} · сохранённый файл ${editor.file?.sha}`,
        publication: `Статус: откат опубликован · в редакторе несохранённая ревизия ${action.sha}` };
    case 'slug':
      return { ...editor, slug: action.slug };
    case 'edit': {
      const next = { ...editor, text: action.text };
      return { ...next, publication: isDirty(next) ? 'Статус: есть несохранённые изменения' : unpublished };
    }
    case 'publication': return { ...editor, publication: action.message };
    case 'delete': return { ...initial, meta: 'Онтология помещена в карантин.',
      publication: `Удалено. Файл и ревизии будут окончательно очищены ${action.expires}.` };
  }
}

export function OntologyPage() {
  const [token, setToken] = useToolsToken(true);
  const [editor, dispatch] = useReducer(reducer, initial);
  const [files, setFiles] = useState<Array<Pick<OntologyFile, 'slug' | 'filename'>>>([]);
  const [quarantine, setQuarantine] = useState<Quarantined[]>([]);
  const [config, setConfig] = useState<AssistantConfig>();
  const [configUnavailable, setConfigUnavailable] = useState(false);
  const [notice, setNotice] = useState<{ message: string; error: boolean }>();
  const [busy, setBusy] = useState(false);
  const [deletion, setDeletion] = useState<{ slug: string; preview: DeletionPreview }>();
  const lifetime = useRef(new AbortController());
  const initialToken = useRef(token);
  const operation = useRef(false);
  const slug = editor.slug.trim();
  const fileSha = slug === editor.file?.slug ? editor.file.sha : null;
  const dirty = isDirty(editor);
  const meta = slug && slug !== editor.file?.slug && !editor.imported
    ? `${slug}.ttl · граф urn:bitrix24:ontology:${slug} · новый файл` : editor.meta;
  const feedback = (message: string, error = false) => setNotice({ message, error });

  async function refreshList(auth: string, signal: AbortSignal) {
    const listing = await ontologyApi<{ admin_enabled?: boolean; ontologies: typeof files }>('/api/ontologies', auth, signal);
    if (signal.aborted) return;
    setFiles(listing.ontologies);
    const removed = await ontologyApi<{ items?: Quarantined[] }>('/api/ontologies/quarantine', auth, signal);
    if (signal.aborted) return;
    setQuarantine(removed.items ?? []);
    if (!listing.admin_enabled) feedback('Изменение отключено: серверу не задан ONTOLOGY_ADMIN_TOKEN.', true);
  }

  useEffect(() => {
    document.title = 'Инструменты — Онтология — CRM Pulse';
    const controller = new AbortController();
    lifetime.current = controller;
    const { signal } = controller;
    void refreshList(initialToken.current, signal).catch((cause) => {
      if (!signal.aborted) feedback(cause instanceof Error ? cause.message : 'Не удалось загрузить список онтологий.', true);
    });
    void ontologyApi<AssistantConfig>('/api/ontology-assistant/config', initialToken.current, signal).then((data) => {
      if (!signal.aborted) setConfig(data);
    }, () => { if (!signal.aborted) setConfigUnavailable(true); });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  async function perform(action: (signal: AbortSignal) => Promise<void>) {
    if (operation.current) return;
    operation.current = true;
    setBusy(true);
    const { signal } = lifetime.current;
    try { await action(signal); }
    catch (cause) { if (!signal.aborted) feedback(cause instanceof Error ? cause.message : 'Ошибка операции.', true); }
    finally {
      operation.current = false;
      if (!signal.aborted) setBusy(false);
    }
  }

  function open(target: string) {
    if (dirty && !window.confirm('Есть несохранённые изменения. Переключиться без сохранения?')) return;
    void perform(async (signal) => {
      const data = await ontologyApi<OntologyFile>(ontologyPath(target), token, signal);
      if (signal.aborted) return;
      dispatch({ type: 'open', data });
      feedback('Файл загружен.');
    });
  }
  function save(create: boolean) {
    if (create && fileSha) { feedback('Файл уже существует. Используйте кнопку «Сохранить».', true); return; }
    void perform(async (signal) => {
      const text = editor.text;
      const data = fileSha
        ? await ontologyApi<OntologyFile>(ontologyPath(slug), token, signal, { text, expected_sha256: fileSha }, 'PUT')
        : await ontologyApi<OntologyFile>('/api/ontologies', token, signal, { slug, text });
      if (signal.aborted) return;
      dispatch({ type: 'save', data, text });
      feedback(create ? 'Новый файл сохранён. Граф не изменён.' : 'Файл сохранён. Граф не изменён.');
      await refreshList(token, signal);
    });
  }
  function validate(access = false) {
    void perform(async (signal) => {
      const data = await ontologyApi<{ uri: string }>('/api/ontologies/validate', token, signal, access ? {
        slug: 'access-check', text: '<urn:bitrix24:ontology:access-check> a <http://www.w3.org/2002/07/owl#Ontology> .',
      } : { slug, text: editor.text });
      if (!signal.aborted) feedback(access ? 'Доступ администратора подтверждён.' : `Turtle корректен. URI: ${data.uri}`);
    });
  }
  function publish() {
    if (dirty || !fileSha) { feedback(dirty ? 'Сначала сохраните текст.' : 'Сначала сохраните файл.', true); return; }
    void perform(async (signal) => {
      const data = await ontologyApi<{ sha256: string; message: string }>(`${ontologyPath(slug)}/publish`, token, signal, { expected_sha256: fileSha });
      if (signal.aborted) return;
      dispatch({ type: 'publication', message: `Статус: опубликовано · SHA-256 ${data.sha256} · ${data.message}` });
      feedback(data.message);
    });
  }
  function rollback() {
    if (!slug || dirty) {
      feedback('Сначала выберите сохранённый файл и сохраните текущие изменения.', true);
      return;
    }
    if (!window.confirm('Опубликовать предыдущую подтверждённую ревизию этого графа?')) return;
    void perform(async (signal) => {
      const data = await ontologyApi<{ sha256: string; text: string }>(`${ontologyPath(slug)}/rollback`, token, signal, {});
      if (signal.aborted) return;
      dispatch({ type: 'rollback', sha: data.sha256, text: data.text });
      feedback('Предыдущая ревизия опубликована и загружена в редактор. Она отличается от текущего сохранённого файла; нажмите «Сохранить», если нужно синхронизировать файл с откатом.');
    });
  }
  function previewDelete() {
    if (!fileSha || dirty) {
      feedback(!fileSha ? 'Выберите сохранённую онтологию.' : 'Сначала сохраните или отмените изменения редактора.', true);
      return;
    }
    void perform(async (signal) => {
      const preview = await ontologyApi<DeletionPreview>(`${ontologyPath(slug)}/delete-preview`, token, signal, {});
      if (!signal.aborted) setDeletion({ slug, preview });
    });
  }
  function confirmDelete() {
    if (!deletion) return;
    setDeletion(undefined);
    void perform(async (signal) => {
      const data = await ontologyApi<{ expires_at: string; graph_deleted: boolean }>(
        `${ontologyPath(deletion.slug)}/delete`, token, signal, deletion.preview);
      if (signal.aborted) return;
      const expires = new Date(data.expires_at).toLocaleString();
      dispatch({ type: 'delete', expires });
      feedback(`Онтология ${deletion.slug} удалена${data.graph_deleted ? ' вместе с именованным графом' : ''} и помещена в карантин до ${expires}.`);
      await refreshList(token, signal);
    });
  }
  function restore(item: Quarantined) {
    if (!window.confirm(`Восстановить ${item.slug}.ttl, архив ревизий и исходный опубликованный граф? Если в GraphDB уже находится другой граф с этим URI, восстановление остановится без перезаписи.`)) return;
    void perform(async (signal) => {
      const data = await ontologyApi<{ message: string }>(`${ontologyPath(item.slug)}/restore`, token, signal, { operation_id: item.operation_id });
      if (signal.aborted) return;
      feedback(data.message);
      await refreshList(token, signal);
    });
  }

  return <main className="shell">
    <header className="topbar">
      <div className="brand"><div className="brand-mark" aria-hidden="true">P</div><div><p className="eyebrow">SEMANTIC BI · BITRIX24</p><h1>CRM Pulse</h1></div></div>
      <div className="header-actions ontology-auth">
        <label className="team-filter"><span>Токен администратора инструментов</span>
          <input id="admin-token" type="password" autoComplete="off" placeholder="Сохраняется в этом браузере"
            value={token} onChange={(event) => setToken(event.target.value)}/></label>
        <button id="auth-check" className="secondary-button" type="button" disabled={busy} onClick={() => validate(true)}>Проверить доступ</button>
      </div>
    </header>
    <div id="notice" className={!notice || notice.error ? 'error-banner' : ''} role="status" hidden={!notice?.message}>{notice?.message}</div>
    {deletion && <OntologyDeleteDialog {...deletion} cancel={() => setDeletion(undefined)} confirm={confirmDelete}/>}
    <div className="ontology-admin-layout">
      <div className="ontology-left-column">
        <section className="panel ontology-file-panel" aria-labelledby="ontology-title">
          <div className="panel-heading"><div><p className="eyebrow">УПРАВЛЕНИЕ АКСИОМАМИ</p><h2 id="ontology-title">Файлы Turtle</h2>
            <p className="panel-subtitle">Помощник предлагает правки. Только вы применяете их, сохраняете файл и отдельно публикуете в граф.</p></div></div>
          <div className="ontology-workspace">
            <aside className="ontology-sidebar">
              <h3>Онтологии</h3><div id="file-list" className="ontology-list">{files.map((item) =>
                <button key={item.slug} type="button" className="secondary-button" disabled={busy} onClick={() => open(item.slug)}>{item.filename}</button>)}</div>
              <hr/><h3>Карантин</h3><p className="panel-subtitle">TTL, ревизии и снимок опубликованного графа хранятся ограниченный срок.</p>
              <div id="quarantine-list" className="ontology-list">{quarantine.map((item) => <div className="quarantine-item" key={item.operation_id}>
                <p>{`${item.slug} · ревизий: ${item.revision_count} · состояние: ${item.state} · до ${new Date(item.expires_at).toLocaleString()}`}</p>
                <button type="button" className="secondary-button" disabled={busy} onClick={() => restore(item)}>Восстановить файл</button>
              </div>)}</div>
              <hr/>
              <label>Новый slug<input id="new-slug" placeholder="foo-classification" value={editor.slug} disabled={busy}
                onChange={(event) => dispatch({ type: 'slug', slug: event.target.value })}/></label>
              <label>Загрузить локальный .ttl<input id="file-input" type="file" accept=".ttl,text/turtle" disabled={busy}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = '';
                  if (!file || (dirty && !window.confirm('Есть несохранённые изменения. Переключиться без сохранения?'))) return;
                  void perform(async (signal) => {
                    const text = await file.text();
                    if (!signal.aborted) dispatch({ type: 'import', slug: file.name.replace(/\.ttl$/i, ''), text });
                  });
                }}/></label>
              <button id="create" className="secondary-button" type="button" disabled={busy} onClick={() => save(true)}>Создать файл из редактора</button>
            </aside>
            <div className="ontology-editor-column">
              <div id="current-meta" className="empty-state">{meta}</div>
              <textarea id="editor" spellCheck={false} aria-label="Редактор Turtle" rows={24} value={editor.text} disabled={busy}
                onChange={(event) => dispatch({ type: 'edit', text: event.target.value })}/>
              <div className="header-actions ontology-actions">
                <button id="validate" className="secondary-button" type="button" disabled={busy} onClick={() => validate()}>Проверить</button>
                <button id="save" className="secondary-button" type="button" disabled={busy} onClick={() => save(false)}>Сохранить</button>
                <button id="publish" className="secondary-button" type="button" disabled={busy} onClick={publish}>Опубликовать сохранённую версию</button>
                <button id="rollback" className="secondary-button" type="button" disabled={busy} onClick={rollback}>Откатить публикацию</button>
                <button id="delete-ontology" className="secondary-button" type="button" disabled={busy} onClick={previewDelete}>Удалить в карантин</button>
              </div>
              <p id="publish-status" className="panel-subtitle">{editor.publication}</p>
            </div>
          </div>
        </section>
        <ClassStatistics token={token} signal={() => lifetime.current.signal}/>
      </div>
      <OntologyAssistant token={token} context={{ slug, text: editor.text, fileSha }} config={config}
        unavailableConfig={configUnavailable} notice={feedback}
        applyText={(text) => dispatch({ type: 'edit', text })} editorBusy={busy || !!deletion}/>
    </div>
    <footer><span>Источник истины — файл ontologies/*.ttl</span><span>ИИ не сохраняет файл и не публикует RDF-граф.</span></footer>
  </main>;
}
