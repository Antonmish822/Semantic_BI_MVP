import { Fragment, useState, type FormEvent } from 'react';
import { ontologyApi, type Statistics } from './ontologyApi';

function Relations({ data }: { data: Statistics }) {
  const relations = data.relations ?? [];
  const labels = new Map(data.classes.map((item) => [item.uri, item.label_ru || item.name]));
  return <section className="class-relations">
    <h3>Связи классов по domain / range</h3>
    {!relations.length ? <p className="empty-state">Для классов этого пространства не найдены свойства с domain и range, ведущими к классам в этом же пространстве.</p>
      : <div className="class-relations-scroll">
        <svg viewBox={`0 0 850 ${Math.max(100, relations.length * 76 + 24)}`} role="img"
          aria-label="Диаграмма связей классов по domain и range" className="class-relations-svg">
          <defs><marker id="class-relation-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" className="class-relation-arrow"/>
          </marker></defs>
          {relations.map((relation, index) => {
            const y = 14 + index * 76;
            const center = y + 28;
            return <Fragment key={index}>
              <line x1="240" y1={center} x2="610" y2={center} markerEnd="url(#class-relation-arrow)" className="class-relation-line"/>
              {([[14, relation.domain], [620, relation.range]] as const).map(([x, uri]) => <Fragment key={x}>
                <rect x={x} y={y} width="216" height="48" rx="8" className="class-relation-node"/>
                <text x={x + 108} y={center + 4} textAnchor="middle" className="class-relation-node-label">{labels.get(uri) || uri.split(/[\/#]/).pop()}</text>
              </Fragment>)}
              <text x="425" y={center - 7} textAnchor="middle" className="class-relation-property">{relation.property_label_ru || relation.property_name}</text>
            </Fragment>;
          })}
        </svg>
      </div>}
  </section>;
}

export function ClassStatistics({ token, signal }: { token: string; signal: () => AbortSignal }) {
  const [namespace, setNamespace] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [data, setData] = useState<Statistics>();
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!namespace.trim() || loading) return;
    const current = signal();
    setLoading(true);
    setError('');
    setData(undefined);
    try {
      const result = await ontologyApi<Statistics>('/api/ontologies/class-statistics', token, current, { namespace: namespace.trim() });
      if (!current.aborted) setData(result);
    } catch (cause) {
      if (!current.aborted) setError(cause instanceof Error ? cause.message : 'Не удалось получить статистику.');
    } finally {
      if (!current.aborted) setLoading(false);
    }
  }
  return <section className="panel ontology-stats-panel" aria-labelledby="class-statistics-title">
    <div className="panel-heading"><div>
      <p className="eyebrow">RDF-ХРАНИЛИЩЕ</p><h2 id="class-statistics-title">Статистика классов и признаков</h2>
      <p className="panel-subtitle">Показываются классы и признаки с непустыми значениями. Запрос выполняется только после нажатия кнопки.</p>
    </div></div>
    <form id="class-statistics-form" className="class-statistics-form" onSubmit={(event) => void submit(event)}>
      <label htmlFor="class-statistics-namespace">Пространство имён</label>
      <input id="class-statistics-namespace" name="namespace" type="text" required maxLength={512} spellCheck={false}
        placeholder="https://example.org/bitrix24#" value={namespace} onChange={(event) => setNamespace(event.target.value)}/>
      <button id="class-statistics-submit" className="secondary-button" type="submit" disabled={loading}>{loading ? 'Запрашиваю…' : 'Показать статистику'}</button>
    </form>
    <div id="class-statistics" className="class-statistics-results" aria-live="polite">
      {error ? <p className="assistant-error">{error}</p> : !data ? <p className="empty-state">
        {loading ? 'Запрашиваю статистику из RDF-хранилища…' : 'Введите пространство имён и нажмите «Показать статистику».'}
      </p> : <>
        <p className="panel-subtitle">{`Классов найдено: ${data.class_count}. Пространство: ${data.namespace}`}</p>
        {!data.classes.length ? <p className="empty-state">В этом пространстве имён классы не найдены.</p> : <Relations data={data}/>}
        {data.classes.map((item) => <article className="class-stat-card" key={item.uri}>
          <h3>{item.label_ru || item.name}</h3><code className="class-stat-uri">{item.uri}</code>
          {item.comment_ru && <p className="class-stat-comment">{item.comment_ru}</p>}
          <p className="class-stat-count">{`Экземпляров класса: ${item.instance_count}`}</p>
          {!item.features.length ? <p className="empty-state">Непустые признаки не найдены.</p> :
            <details className="class-feature-disclosure">
              <summary>{`Признаки (${item.features.length})`}</summary>
              <table className="class-feature-table">
                <thead><tr><th>Признак</th><th>Label</th><th>Экземпляров с непустым значением</th><th>Комментарий</th></tr></thead>
                <tbody>{item.features.map((feature) => <tr key={feature.uri}>
                  <td title={feature.uri}>{feature.name}</td><td>{feature.label_ru || '—'}</td>
                  <td>{feature.instances_with_value}</td><td>{feature.comment_ru || '—'}</td>
                </tr>)}</tbody>
              </table>
            </details>}
        </article>)}
      </>}
    </div>
  </section>;
}
