export const savedPromptsKey = 'agentSavedPrompts';
export const publishedDraftKey = 'agentPublishedQueryDraft';
export const toolsTokenKey = 'crm-pulse-tools-admin-token';
export const maxSavedPrompts = 10;
export type PublishedDraft = { id: string; title: string; query: string };
export type SavedPrompt = { prompt: string; sparql: string };
export type ChatMessage = { role: 'user' | 'assistant'; content: string; saved_query?: string };
export type ChatSession = { sessionId: string | null; history: ChatMessage[]; transcript: ChatMessage[] };

function object(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' ? value as Record<string, unknown> : undefined;
}

function parseArray(raw: string | null): unknown[] {
  try {
    const value: unknown = JSON.parse(raw ?? '[]');
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

export function readPublishedDraft(storage: Storage): PublishedDraft | null {
  try {
    const value = object(JSON.parse(storage.getItem(publishedDraftKey) ?? 'null'));
    if (value && typeof value.id === 'string' && typeof value.title === 'string' && typeof value.query === 'string') {
      return { id: value.id, title: value.title, query: value.query };
    }
  } catch { /* A damaged or unavailable draft is treated as absent. */ }
  return null;
}

export function normalizeMessages(value: unknown[], includeQuery = false): ChatMessage[] {
  return value.flatMap((item): ChatMessage[] => {
    const entry = object(item);
    if (!entry || (entry.role !== 'user' && entry.role !== 'assistant') || typeof entry.content !== 'string') return [];
    const message: ChatMessage = { role: entry.role, content: entry.content };
    if (includeQuery && typeof entry.saved_query === 'string') message.saved_query = entry.saved_query;
    return [message];
  });
}

export function readSavedPrompts(storage: Storage): SavedPrompt[] {
  try {
    return parseArray(storage.getItem(savedPromptsKey)).flatMap((item) => {
      const entry = object(item);
      const prompt = typeof item === 'string' ? item : entry?.prompt;
      return typeof prompt === 'string' && prompt.trim()
        ? [{ prompt: prompt.trim(), sparql: typeof entry?.sparql === 'string' ? entry.sparql : '' }] : [];
    });
  } catch {
    return [];
  }
}

const sessionKey = 'agentSessionId';
const historyKey = 'agentHistory';
const transcriptKey = 'agentTranscript';
const migratedKey = 'agentReactChatMigrated';

export function readChat(storage: Storage): ChatSession {
  try {
    if (!storage.getItem(migratedKey)) {
      const previousId = storage.getItem('reactAgentSession');
      const previousHistory = storage.getItem('reactAgentHistory');
      if (previousId || previousHistory) {
        const hasChat = [sessionKey, historyKey, transcriptKey].some((key) => storage.getItem(key) !== null);
        if (!hasChat) {
          if (previousId) storage.setItem(sessionKey, previousId);
          if (previousHistory) {
            storage.setItem(historyKey, previousHistory);
            storage.setItem(transcriptKey, previousHistory);
          }
        }
        storage.setItem(migratedKey, '1');
      }
    }
    return {
      sessionId: storage.getItem(sessionKey),
      history: normalizeMessages(parseArray(storage.getItem(historyKey))),
      transcript: normalizeMessages(parseArray(storage.getItem(transcriptKey)), true),
    };
  } catch {
    return { sessionId: null, history: [], transcript: [] };
  }
}

export function writeChat(storage: Storage, chat: ChatSession) {
  try {
    if (chat.sessionId) storage.setItem(sessionKey, chat.sessionId);
    else storage.removeItem(sessionKey);
    storage.setItem(historyKey, JSON.stringify(chat.history));
    storage.setItem(transcriptKey, JSON.stringify(chat.transcript));
  } catch {
    // Browser storage can be disabled or full.
  }
}

export function clearChat(storage: Storage) {
  try {
    [sessionKey, historyKey, transcriptKey].forEach((key) => storage.removeItem(key));
  } catch {
    // Clearing React state is sufficient for the current view.
  }
}
