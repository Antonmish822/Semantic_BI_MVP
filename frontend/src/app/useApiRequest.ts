import { useEffect, useState } from 'react';
import { ApiError } from './api';

export type RequestState<T> =
  | { status: 'idle' | 'loading'; data?: T; error?: string }
  | { status: 'success'; data: T }
  | { status: 'error'; data?: T; error: string; kind?: string };

/** Runs a request for the current key and ignores responses from older keys. */
export function useApiRequest<T>(
  key: string | null,
  load: (key: string, signal: AbortSignal) => Promise<T>,
  revision = 0,
): RequestState<T> {
  const [result, setResult] = useState<{ key: string; state: RequestState<T> }>();
  const [restored, setRestored] = useState(0);

  useEffect(() => {
    if (key === null) {
      setResult(undefined);
      return;
    }
    const controller = new AbortController();
    let settled = false;
    const abort = () => { if (!settled) controller.abort(); };
    const resume = (event: PageTransitionEvent) => {
      if (event.persisted && controller.signal.aborted) setRestored((value) => value + 1);
    };
    window.addEventListener('pagehide', abort);
    window.addEventListener('pageshow', resume);
    setResult((previous) => ({
      key,
      state: { status: 'loading', data: previous?.key === key ? previous.state.data : undefined },
    }));
    load(key, controller.signal).then(
      (data) => {
        settled = true;
        if (!controller.signal.aborted) setResult({ key, state: { status: 'success', data } });
      },
      (cause: unknown) => {
        settled = true;
        if (controller.signal.aborted) return;
        const error = cause instanceof Error ? cause : new Error('Неизвестная ошибка');
        setResult((previous) => ({ key, state: {
          status: 'error',
          data: previous?.key === key ? previous.state.data : undefined,
          error: error.message,
          kind: cause instanceof ApiError ? cause.kind : undefined,
        } }));
      },
    );
    return () => {
      abort();
      window.removeEventListener('pagehide', abort);
      window.removeEventListener('pageshow', resume);
    };
  }, [key, load, revision, restored]);

  if (key === null) return { status: 'idle' };
  return result?.key === key ? result.state : { status: 'loading' };
}
