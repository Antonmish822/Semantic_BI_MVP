import { asRecord, type ParameterType, type QueryParameter, type QueryValues } from './queryTools';
import { TableDownloads } from './TableDownloads';

export type ResultMode = 'table' | 'json';

export function ResultSwitch({ mode, select, hidden = false, idPrefix }: {
  mode: ResultMode; select: (mode: ResultMode) => void; hidden?: boolean; idPrefix?: string;
}) {
  return <div className="query-result-view-switch" role="group" aria-label="Формат результата" hidden={hidden}>
    {(['table', 'json'] as const).map((value) => <button type="button" key={value}
      id={idPrefix ? `${idPrefix}-${value}-mode` : undefined}
      className={`query-result-view-button${mode === value ? ' active' : ''}`} aria-pressed={mode === value}
      onClick={() => select(value)}>{value === 'table' ? 'Таблица' : 'JSON'}</button>)}
  </div>;
}

export function QueryResult({ result, mode, filename, onError }: {
  result: unknown; mode: ResultMode; filename: string; onError: (message: string) => void;
}) {
  const record = asRecord(result);
  const json = (value: unknown) => <pre className="query-result-fallback">{JSON.stringify(value, null, 2)}</pre>;
  if (mode === 'json') return json(result);
  if (!record) return typeof result === 'string' ? result : JSON.stringify(result, null, 2);
  const bindings = Array.isArray(record.bindings) ? record.bindings as unknown[] : null;
  const metadata = [
    typeof record.status === 'string' && `Статус: ${record.status}`,
    bindings && `Строк: ${bindings.length}`,
    typeof record.effective_limit === 'number' && `Лимит: ${record.effective_limit}`,
    typeof record.truncated === 'boolean' && (record.truncated ? 'Результат усечён' : 'Результат полный'),
    typeof record.snapshot_id === 'string' && `Снимок: ${record.snapshot_id}`,
    typeof record.entailment === 'string' && `Вывод: ${record.entailment}`,
    typeof record.mode === 'string' && `Режим: ${record.mode}`,
  ].filter(Boolean).join(' · ');
  const rows = bindings?.map(asRecord) ?? [];
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row ?? {})))];
  return <>
    <p className="query-result-metadata">{metadata}</p>
    {!bindings ? json(result) : !bindings.length
      ? <p className="query-result-empty">Запрос выполнен, строк не найдено.</p>
      : !columns.length ? json(bindings) : <TableDownloads queryResult prefix={filename} onError={onError}>
        <thead><tr>{columns.map((column) => <th scope="col" key={column}>{column}</th>)}</tr></thead>
        <tbody>{rows.map((row, index) => <tr key={index}>{columns.map((column) => {
          const raw = row && Object.hasOwn(row, column) ? row[column] : undefined;
          const term = asRecord(raw);
          const value = term && 'value' in term ? String(term.value ?? '') :
            raw == null ? '—' : typeof raw === 'object' ? JSON.stringify(raw) : String(raw);
          const detail = term && 'value' in term ? [term.type, term['xml:lang'], term.datatype].filter(Boolean).join(' · ') : '';
          return <td key={column} data-export-value={value}>
            <span className="query-result-cell-value">{value}</span>
            {detail && <small className="query-result-cell-detail">{detail}</small>}
          </td>;
        })}</tr>)}</tbody>
      </TableDownloads>}
  </>;
}

const inputTypes: Partial<Record<ParameterType, string>> = {
  dateTime: 'datetime-local', date: 'date', time: 'time', integer: 'number', decimal: 'number', number: 'number',
};

export function ParameterFields({ fields, values, setValue, prefix }: {
  fields: QueryParameter[]; values: QueryValues; setValue: (name: string, value: string) => void; prefix: string;
}) {
  return <div className="query-parameter-fields">
    {fields.map(({ name, type }, index) => {
      const id = `${prefix}-${index}`;
      const value = Object.hasOwn(values, name) ? values[name] : '';
      return <label className="query-parameter-field" key={name} htmlFor={id}>
        <span>{name}</span>
        {type === 'boolean' ? <select id={id} value={value} required autoComplete="off"
          onChange={(event) => setValue(name, event.target.value)}>
          <option value="">Выберите значение</option><option value="true">true</option><option value="false">false</option>
        </select> : <input id={id} type={inputTypes[type] ?? 'text'} value={value} required autoComplete="off"
          step={type === 'integer' ? '1' : type === 'number' || type === 'decimal' ? 'any' : undefined}
          onChange={(event) => setValue(name, event.target.value)}/>}
      </label>;
    })}
  </div>;
}
