export type Row = Record<string, unknown>;

export async function api<T extends Row = Row>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    cache: 'no-store',
    ...init,
    headers: { ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...(init.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof payload.error === 'string' ? payload.error : `HTTP ${response.status}`);
  return payload as T;
}

export function rows(value: unknown): Row[] {
  return Array.isArray(value) ? value.filter((item): item is Row => !!item && typeof item === 'object') : [];
}

export function text(value: unknown, fallback = '—'): string {
  return typeof value === 'string' && value ? value : fallback;
}

export function number(value: unknown): string {
  return typeof value === 'number' ? new Intl.NumberFormat('ru-RU').format(value) : '—';
}

export function adminHeaders(): HeadersInit {
  const token = sessionStorage.getItem('toolAdminToken') || '';
  if (!token) return {};
  const bytes = new TextEncoder().encode(token);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  const encoded = btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
  return { Authorization: `Bearer-UTF8 ${encoded}` };
}
