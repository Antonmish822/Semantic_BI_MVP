import { apiJson } from './api.ts';

export type ParameterType = 'dateTime' | 'date' | 'time' | 'boolean' | 'integer' | 'decimal' | 'number' | 'text';
export type QueryParameter = { name: string; type: ParameterType };
export type QueryValues = Record<string, string>;
const xsd = 'http://www.w3.org/2001/XMLSchema#';

function datatype(uri: string): ParameterType {
  if (!uri.startsWith(xsd)) return 'text';
  const name = uri.slice(xsd.length);
  if (name === 'dateTime' || name === 'dateTimeStamp') return 'dateTime';
  if (name === 'date' || name === 'time' || name === 'boolean' || name === 'decimal') return name;
  if (name === 'float' || name === 'double') return 'number';
  if (/^(?:integer|nonPositiveInteger|negativeInteger|long|int|short|byte|nonNegativeInteger|unsignedLong|unsignedInt|unsignedShort|unsignedByte|positiveInteger)$/.test(name)) return 'integer';
  return 'text';
}

export function describeParameters(query: string): QueryParameter[] {
  const prefixes = new Map(Array.from(query.matchAll(/\bPREFIX\s+([A-Za-z][\w-]*):\s*<([^<>\s]+)>/gi),
    (match) => [match[1], match[2]]));
  const details = new Map<string, ParameterType>();
  for (const match of query.matchAll(/\{\{([A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*)\}\}/g)) {
    const name = match[1];
    const suffix = query.slice(match.index + match[0].length).match(/^\s*\^\^\s*(?:([A-Za-z][\w-]*):([A-Za-z][\w.-]*)|<([^<>]+)>)/);
    const namespace = suffix && prefixes.get(suffix[1]);
    const type = datatype(suffix?.[3] || (namespace ? namespace + suffix[2] : ''));
    const known = details.get(name);
    if (known && known !== type) throw new Error(`Параметр «${name}» используется с разными типами данных.`);
    details.set(name, type);
  }
  return Array.from(details, ([name, type]) => ({ name, type }));
}

export class ParameterError extends Error {
  constructor(readonly parameter: string, message: string) { super(message); }
}

export function collectParameters(fields: QueryParameter[], values: QueryValues): QueryValues {
  return Object.fromEntries(fields.map(({ name, type }) => {
    let value = Object.hasOwn(values, name) ? values[name] : '';
    if (!value.trim()) throw new ParameterError(name, `Заполните параметр «${name}».`);
    if (type === 'dateTime') {
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) throw new ParameterError(name, `Проверьте дату и время параметра «${name}».`);
      value = date.toISOString();
    }
    if (type === 'time' && /^\d{2}:\d{2}$/.test(value)) value += ':00';
    return [name, value];
  }));
}

export function toolsAuthorization(token: string): Record<string, string> {
  if (!token) return {};
  const binary = Array.from(new TextEncoder().encode(token), (byte) => String.fromCharCode(byte)).join('');
  return { Authorization: `Bearer-UTF8 ${btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '')}` };
}

export type QueryReply = { request_id?: string; tool_result?: unknown };
export function runQuery(query: string, parameters: QueryValues, id: string | undefined, signal: AbortSignal) {
  return apiJson<QueryReply>('/api/agent/query', {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json', ...(id ? { 'X-Request-ID': id } : {}) },
    body: JSON.stringify({ query, ...(Object.keys(parameters).length ? { parameters } : {}) }),
  });
}

export function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

export function parseQueryResult(data: QueryReply): unknown {
  const content = asRecord(data.tool_result)?.content;
  const text = Array.isArray(content) && content.map(asRecord).find((item) => item?.type === 'text')?.text;
  if (typeof text !== 'string') return data.tool_result ?? data;
  try { return JSON.parse(text); } catch { return { message: text }; }
}
