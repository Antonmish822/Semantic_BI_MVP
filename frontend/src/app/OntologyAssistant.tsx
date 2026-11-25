import { Fragment, useEffect, useRef, useState } from 'react';
import { ApiError } from './api';
import { ontologyApi, sha256, type AssistantConfig, type Suggestion } from './ontologyApi';

type Context = { slug: string; text: string; fileSha: string | null };
type History = { role: 'user' | 'assistant'; content: string };
const tasks = [
  ['Создать аксиому', 'Предложи минимальную аксиому для желаемой классификации. Сначала изучи подходящие признаки и профиль свойства.'],
  ['Проверить правило', 'Проверь текущий текст онтологии на широкие или противоречивые правила. Предложи исправленный полный TTL только если можешь обосновать его.'],
  ['Почему нет вывода?', 'Почему ожидаемый класс может не выводиться по текущей онтологии? Изучи сохранённый файл, опубликованный граф и агрегаты; отдели факты от гипотез.'],
  ['Оценить влияние', 'Оцени предварительное влияние текущих правил на типы ресурсов. Используй только агрегаты и укажи ограничения прогноза.'],
];
const unavailable = 'Задайте POLZA_API_KEY в окружении сервера; редактор останется доступен.';

function SuggestionCard({ result, apply, fix, recalculate, blocked }: {
  result: Suggestion; apply: () => void; fix: () => void; recalculate: () => void; blocked: boolean;
}) {
  return <article className="assistant-result">
    <h3>{result.summary || 'Ответ помощника'}</h3>
    {result.question && <p>{`Уточнение: ${result.question}`}</p>}
    <p className="assistant-provenance">{`Модель ${result.model} · редактор ${result.editor_sha256.slice(0, 12)} · файл ${result.file_sha256?.slice(0, 12) || 'новый'}`}</p>
    {result.impact && <>
      <h4>Предварительное влияние</h4>
      <p>{`Оценка субъектов: ${result.impact.estimated_subjects ?? 'нет оценки'}; возможные пересечения: ${result.impact.possible_cross_type_matches?.join(', ') || 'не заявлены'}. ${result.impact.limits || ''}`}</p>
    </>}
    {!!result.evidence?.length && <><h4>Основания</h4><ul>{result.evidence.map((item, index) =>
      <li key={index}>{`${item.source} · ${item.reference}: ${item.finding}`}</li>)}</ul></>}
    {!!result.warnings?.length && <><h4>Предупреждения</h4><ul>{result.warnings.map((item, index) => <li key={index}>{item}</li>)}</ul></>}
    {!!result.assumptions?.length && <p>{`Предположения: ${result.assumptions.join(' · ')}`}</p>}
    {result.rdf_diff && <>
      <h4>Изменения RDF</h4>
      {([['added', 'Добавлено'], ['removed', 'Удалено']] as const).map(([kind, label]) => {
        const rows = result.rdf_diff?.[kind] ?? [];
        return <Fragment key={kind}><p>{`${label}: ${rows.length} аксиом`}</p>
          {!!rows.length && <pre className="assistant-diff">{rows.map((row) => row.join(' ')).join('\n')}</pre>}
        </Fragment>;
      })}
    </>}
    {result.diff && <details><summary>Текстовое сравнение TTL</summary><pre className="assistant-diff">{result.diff}</pre></details>}
    {result.proposed_ttl && <>
      <details><summary>Полный предлагаемый Turtle</summary><pre className="assistant-diff">{result.proposed_ttl}</pre></details>
      <div className="assistant-result-actions">
        <button type="button" className="secondary-button" disabled={blocked || !result.applicable} onClick={apply}>Применить в редактор</button>
        <button type="button" className="secondary-button" onClick={fix}>Попросить исправить</button>
        <button type="button" className="secondary-button" onClick={recalculate}>Пересчитать по текущему тексту</button>
      </div>
    </>}
  </article>;
}

export function OntologyAssistant({ token, context, config, unavailableConfig, notice, applyText, editorBusy }: {
  token: string; context: Context; config?: AssistantConfig; unavailableConfig: boolean;
  notice: (message: string, error?: boolean) => void; applyText: (text: string) => void; editorBusy: boolean;
}) {
  const [prompt, setPrompt] = useState('');
  const [history, setHistory] = useState<History[]>([]);
  const [contextSlug, setContextSlug] = useState('');
  const [result, setResult] = useState<Suggestion>();
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [blocked, setBlocked] = useState(false);
  const host = useRef<HTMLDivElement>(null);
  const request = useRef<AbortController | null>(null);
  const application = useRef<AbortController | null>(null);
  const latest = useRef({ context, editorBusy });
  latest.current = { context, editorBusy };
  useEffect(() => () => { request.current?.abort(); application.current?.abort(); }, []);
  useEffect(() => { if (host.current) host.current.scrollTop = host.current.scrollHeight; }, [result, error]);

  async function ask(message: string, previousProposal: string | null = null) {
    if (request.current) return;
    if (!context.slug) { notice('Укажите slug онтологии, прежде чем обращаться к помощнику.', true); return; }
    if (!message.trim()) { notice('Введите задачу для помощника.', true); return; }
    if (!config?.enabled) { notice('ИИ-помощник не настроен. Установите POLZA_API_KEY на сервере.', true); return; }
    const controller = new AbortController();
    request.current = controller;
    const prior = contextSlug === context.slug ? history.slice(-8) : [];
    const next: History[] = [...prior, { role: 'user', content: message.trim() }];
    setContextSlug(context.slug);
    setHistory(next);
    setError('');
    setStatus('Работает…');
    try {
      const hash = await sha256(context.text);
      if (controller.signal.aborted) return;
      const reply = await ontologyApi<Suggestion>('/api/ontology-assistant/suggest', token, controller.signal, {
        slug: context.slug, editor_text: context.text, editor_sha256: hash, file_sha256: context.fileSha,
        message: message.trim(), history: prior, previous_proposal: previousProposal,
      });
      if (controller.signal.aborted) return;
      setResult(reply);
      setPrompt('');
      setHistory([...next, { role: 'assistant', content: (reply.summary ?? '') +
        (reply.question ? ` Уточнение: ${reply.question}` : '') +
        (reply.proposed_ttl ? ` Предложен TTL (${reply.proposed_ttl.length} символов).` : '') }]);
      setBlocked(false);
      setStatus(reply.applicable ? 'Предложение проверено' : reply.proposed_ttl ? 'Предложение требует проверки'
        : reply.question ? 'Нужны уточнения' : 'Анализ готов');
    } catch (cause) {
      if (controller.signal.aborted) return;
      const message = cause instanceof Error ? cause.message : 'Не удалось получить предложение.';
      setHistory([...next, { role: 'assistant', content: `Ошибка: ${message}` }]);
      setError(`${cause instanceof ApiError && cause.code ? cause.code : 'Ошибка'}: ${message}`);
      setStatus('Ошибка');
    } finally {
      if (request.current === controller) request.current = null;
    }
  }

  async function apply() {
    if (!result?.proposed_ttl || !result.applicable || application.current || editorBusy) return;
    const controller = new AbortController();
    application.current = controller;
    setBlocked(true);
    try {
      if (context.slug !== result.slug) throw new Error('Выбран другой slug. Пересчитайте предложение.');
      if (await sha256(context.text) !== result.editor_sha256) throw new Error('Текст редактора изменился. Пересчитайте предложение.');
      const version = await ontologyApi<{
        file_sha256: string | null; published_schema_fingerprint?: string; published_status: string;
      }>(`/api/ontology-assistant/version?slug=${encodeURIComponent(result.slug)}`, token, controller.signal);
      if (controller.signal.aborted) return;
      if (version.file_sha256 !== result.file_sha256) throw new Error('Сохранённый файл изменился. Пересчитайте предложение.');
      if (version.published_schema_fingerprint !== result.published_schema_fingerprint ||
        version.published_status !== result.published_schema_status) throw new Error('Опубликованная схема изменилась. Пересчитайте предложение.');
      const current = latest.current;
      if (current.editorBusy || current.context.slug !== context.slug || current.context.text !== context.text ||
        current.context.fileSha !== context.fileSha) throw new Error('Редактор изменился. Пересчитайте предложение.');
      applyText(result.proposed_ttl);
      notice('Предложение перенесено только в редактор. Проверьте и сохраните его отдельно.');
    } catch (cause) {
      if (!controller.signal.aborted) notice(`${cause instanceof Error ? cause.message : 'Ошибка.'} Старое предложение заблокировано.`, true);
    } finally {
      application.current = null;
    }
  }
  const working = status === 'Работает…';
  return <section className="panel assistant-panel" aria-labelledby="assistant-title">
    <div className="panel-heading assistant-heading">
      <div><p className="eyebrow">ПРЕДЛОЖЕНИЯ БЕЗ АВТОМАТИЧЕСКОЙ ЗАПИСИ</p><h2 id="assistant-title">ИИ-помощник по онтологиям</h2>
        <p id="assistant-model" className="panel-subtitle">{config ? `Модель: ${config.model} · смена через ONTOLOGY_AI_MODEL на сервере` : 'Проверяю конфигурацию…'}</p></div>
      <span id="assistant-state" className="assistant-state">{status || (config ? config.enabled ? 'Готов' : 'Не настроен'
        : unavailableConfig ? 'Конфигурация недоступна' : 'Загрузка')}</span>
    </div>
    <div className="assistant-layout">
      <div className="assistant-conversation">
        <div ref={host} id="assistant-history" className="assistant-history" aria-live="polite">
          {!result && <p className="empty-state">Опишите нужное правило или выберите быструю задачу.</p>}
          {config && !config.enabled && <p className="assistant-error">Ошибка: {unavailable}</p>}
          {result && <>
            {history.slice(-8).map((item, index) => <p key={index} className="assistant-history-line">
              {`${item.role === 'user' ? 'Вы' : 'Помощник'}: ${item.content.slice(0, 1200)}`}
            </p>)}
            <SuggestionCard result={result} apply={() => void apply()} blocked={blocked || editorBusy || working}
              fix={() => void ask(`Исправь предыдущее предложение${prompt.trim() ? `: ${prompt.trim()}` : '. Учти обнаруженные предупреждения и сохрани исходную цель.'}`, result.proposed_ttl)}
              recalculate={() => void ask(prompt || 'Пересчитай предложение по текущему содержимому редактора.')}/>
          </>}
          {error && <p className="assistant-error">{error}</p>}
        </div>
        <div className="assistant-composer">
          <label htmlFor="assistant-prompt">Задача помощнику</label>
          <textarea id="assistant-prompt" rows={4} maxLength={8000} value={prompt}
            placeholder="Например: проверьте, безопасно ли классифицировать сделки по categoryCode"
            onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                void ask(prompt);
              }
            }}/>
          <div className="assistant-composer-actions">
            <span>Контекст включает текущий редактор, сохранённый TTL и агрегаты RDF.</span>
            <button id="assistant-send" className="secondary-button" type="button" disabled={working} onClick={() => void ask(prompt)}>Спросить</button>
          </div>
        </div>
      </div>
      <aside className="assistant-quick-tasks"><h3>Быстрые задачи</h3>
        {tasks.map(([title, task]) => <button key={title} type="button" data-prompt={task} className="secondary-button"
          disabled={working} onClick={() => { setPrompt(task); void ask(task); }}>{title}</button>)}
      </aside>
    </div>
  </section>;
}
