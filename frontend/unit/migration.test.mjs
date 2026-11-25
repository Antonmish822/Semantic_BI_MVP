import assert from 'node:assert/strict';
import test from 'node:test';
import { ApiError, apiJson } from '../src/app/api.ts';
import { clearChat, readChat, readPublishedDraft, readSavedPrompts, writeChat } from '../src/app/browserStorage.ts';
import { collectParameters, describeParameters, parseQueryResult } from '../src/app/queryTools.ts';

function memoryStorage(entries) {
  const data = new Map(Object.entries(entries));
  return {
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => data.set(key, value),
    removeItem: (key) => data.delete(key),
  };
}

test('restores the earlier React session once and keeps its reset across navigation', () => {
  const messages = [{ role: 'user', content: 'Сделки' }, { role: 'assistant', content: 'Есть данные' }];
  const storage = memoryStorage({ reactAgentSession: 'previous-id', reactAgentHistory: JSON.stringify(messages) });
  const restored = readChat(storage);
  assert.deepEqual(restored, { sessionId: 'previous-id', history: messages, transcript: messages });
  writeChat(storage, restored);
  clearChat(storage);
  assert.deepEqual(readChat(storage), { sessionId: null, history: [], transcript: [] });
});

test('reads original saved prompt formats and a draft', () => {
  const draft = { id: 'demo', title: 'Сделки', query: 'SELECT * WHERE {}' };
  const storage = memoryStorage({
    agentSavedPrompts: JSON.stringify([' Старый ', { prompt: ' Новый ', sparql: draft.query }, null]),
    agentPublishedQueryDraft: JSON.stringify(draft),
  });
  assert.deepEqual(readSavedPrompts(storage), [
    { prompt: 'Старый', sparql: '' }, { prompt: 'Новый', sparql: draft.query },
  ]);
  assert.deepEqual(readPublishedDraft(storage), draft);
});

test('collects typed query parameters as strings and rejects inconsistent types', () => {
  const prefix = 'PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>';
  const fields = describeParameters(prefix + ' {{enabled}}^^xsd:boolean {{at}}^^xsd:time {{day}}^^xsd:date');
  assert.deepEqual(collectParameters(fields, { enabled: 'false', at: '14:25', day: '2026-11-24' }), {
    enabled: 'false', at: '14:25:00', day: '2026-11-24',
  });
  assert.throws(() => describeParameters(prefix + ' {{day}}^^xsd:date {{day}}^^xsd:boolean'), /разными типами/);
  assert.throws(() => collectParameters(fields, {}), /«enabled»/);
});

test('parses MCP envelopes as JSON, plain text or a direct result', () => {
  const direct = { status: 'ok', bindings: [] };
  assert.deepEqual(parseQueryResult({ tool_result: direct }), direct);
  assert.deepEqual(parseQueryResult({ tool_result: { content: [{ type: 'text', text: JSON.stringify(direct) }] } }), direct);
  assert.deepEqual(parseQueryResult({ tool_result: { content: [{ type: 'text', text: 'Недоступно' }] } }), { message: 'Недоступно' });
});

test('distinguishes Gateway redirects, API authorization errors, invalid responses and network failures', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch', async () => Response.json({ error: 'Требуется токен администратора' }, { status: 401 }));
  await assert.rejects(apiJson('/api/ontologies'), (error) =>
    error instanceof ApiError && error.kind === 'http' && error.status === 401 && /Требуется токен/.test(error.message));
  fetch.mock.mockImplementationOnce(async () => ({ type: 'opaqueredirect', redirected: false, status: 0 }));
  await assert.rejects(apiJson('/api/pulse'), (error) => error.kind === 'session');
  fetch.mock.mockImplementationOnce(async () => new Response('not json'));
  await assert.rejects(apiJson('/api/pulse'), (error) => error.kind === 'invalid-response');
  fetch.mock.mockImplementationOnce(async () => { throw new TypeError('offline'); });
  await assert.rejects(apiJson('/api/pulse'), (error) => error.kind === 'network');
  const aborted = new DOMException('aborted', 'AbortError');
  fetch.mock.mockImplementationOnce(async () => { throw aborted; });
  await assert.rejects(apiJson('/api/pulse'), (error) => error === aborted);
});
