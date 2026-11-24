export type ApiErrorKind = 'network' | 'http' | 'session' | 'invalid-response';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly kind: ApiErrorKind,
    readonly status?: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/** Fetch JSON from the existing Gateway while preserving its method and URL contract. */
export async function apiJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, { cache: 'no-store', ...init });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause;
    throw new ApiError('Не удалось связаться с сервером.', 'network');
  }

  if (response.redirected || response.status === 401 || response.status === 403) {
    throw new ApiError('Сессия завершена. Войдите снова.', 'session', response.status);
  }
  if (!response.ok) {
    throw new ApiError(`Ошибка API (${response.status}).`, 'http', response.status);
  }
  try {
    return await response.json() as T;
  } catch {
    throw new ApiError('Сервер вернул некорректный ответ.', 'invalid-response', response.status);
  }
}

/** Abort the previous request whenever its filter key changes; abort on unmount. */
export function createLatestRequest() {
  let controller: AbortController | undefined;
  return {
    next(): AbortSignal {
      controller?.abort();
      controller = new AbortController();
      return controller.signal;
    },
    cancel() {
      controller?.abort();
      controller = undefined;
    },
  };
}
