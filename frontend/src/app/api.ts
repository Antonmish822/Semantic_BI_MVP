export type ApiErrorKind = 'network' | 'http' | 'session' | 'invalid-response';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly kind: ApiErrorKind,
    readonly status?: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}
export type Category = { id: string; name: string };
export type PortfolioEntry = {
  uri?: string; name?: string; active_deals?: number; activities_24h?: number;
  activities_7d?: number; activities_30d?: number; inactive_deals_7d?: number;
};
export type PulseResponse = {
  available: boolean;
  backend?: string;
  as_of?: string;
  summary: {
    active_deals: number;
    created_24h: number;
    created_7d: number;
    created_30d: number;
    red_deals_count: number;
  };
  employees?: PortfolioEntry[];
  categories: Category[];
};
export type ViewStateResponse = {
  available: boolean;
  active_situations: Array<{ id?: string; title?: string; description?: string; priority?: number }>;
  view_profile: {
    block_order: string[];
    highlights: string[];
    default_filters?: { project?: string };
  };
  categories: Category[];
};

const projectUrl = (path: string, project: string) =>
  `${path}?project=${encodeURIComponent(project)}`;

type ActivityField = 'at' | 'subject' | 'responsible' | 'uri';
export type RedDeal = {
  uri?: string; title?: string; manager?: string; created_at?: string; last_activity_time?: string;
} & Partial<Record<`${'binding' | 'owner'}_activity_${ActivityField}`, string>>;

export type RecentActivity = {
  uri?: string; created_at?: string; subject?: string; deal_classes?: string;
  deal_title?: string; deal_uri?: string; employee_name?: string; employee_uri?: string;
};

export type ActivityDistribution = {
  total_deals?: number;
  buckets?: Array<{ key: number | string; label: string; deal_count: number }>;
};

export const dealsApi = {
  pulse: (project: string, signal?: AbortSignal) =>
    apiJson<PulseResponse>(projectUrl('/api/pulse', project), { signal }),
  viewState: (project: string, signal?: AbortSignal) =>
    apiJson<ViewStateResponse>(`/api/view-state?page=deals&project=${encodeURIComponent(project)}`, { signal }),
  redDeals: (project: string, signal?: AbortSignal) =>
    apiJson<{ red_deals?: RedDeal[] }>(projectUrl('/api/red-deals', project), { signal }),
  recentActivities: (project: string, signal?: AbortSignal) =>
    apiJson<{ activities?: RecentActivity[] }>(projectUrl('/api/recent-activities', project), { signal }),
  distribution: (project: string, signal?: AbortSignal) =>
    apiJson<ActivityDistribution>(projectUrl('/api/deal-activity-distribution', project), { signal }),
};

/** Fetch JSON from the existing Gateway while preserving its method and URL contract. */
export async function apiJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, { cache: 'no-store', redirect: 'manual', ...init });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause;
    throw new ApiError('Не удалось связаться с сервером.', 'network');
  }

  const loginRedirect = response.redirected && response.url &&
    new URL(response.url).pathname === '/auth/login';
  if (response.type === 'opaqueredirect' || loginRedirect) {
    throw new ApiError('Сессия завершена. Войдите снова.', 'session', response.status);
  }
  if (!response.ok) {
    let detail = '';
    let code: string | undefined;
    try {
      const body: unknown = await response.json();
      if (body && typeof body === 'object' && 'error' in body && typeof body.error === 'string') {
        detail = body.error;
      }
      if (body && typeof body === 'object' && 'code' in body && typeof body.code === 'string') code = body.code;
    } catch {
      // Some Gateway errors are plain text or have no body.
    }
    if (!detail && response.status === 401) throw new ApiError('Сессия завершена. Войдите снова.', 'session', response.status);
    throw new ApiError(detail || `Ошибка API (${response.status}).`, 'http', response.status, code);
  }
  try {
    return await response.json() as T;
  } catch {
    throw new ApiError('Сервер вернул некорректный ответ.', 'invalid-response', response.status);
  }
}
