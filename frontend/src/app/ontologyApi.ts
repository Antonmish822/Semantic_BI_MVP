import { apiJson } from './api';
import { toolsAuthorization } from './queryTools';

export type OntologyFile = {
  slug: string; filename: string; uri: string; sha256: string; text: string;
  publication_status?: string;
  publication_history?: Array<{ sha256: string; at: string; status: string }>;
};
export type Quarantined = {
  slug: string; operation_id: string; revision_count: number; state: string; expires_at: string;
};
export type DeletionPreview = {
  graph_fingerprint?: string; revision_count: number; graph_present: boolean; quarantine_days: number;
};
export type AssistantConfig = { enabled: boolean; model: string };
export type Suggestion = {
  slug: string; model: string; editor_sha256: string; file_sha256: string | null;
  published_schema_fingerprint?: string; published_schema_status: string;
  summary?: string; question?: string; proposed_ttl?: string; applicable: boolean;
  impact?: { estimated_subjects?: number; possible_cross_type_matches?: string[]; limits?: string };
  evidence?: Array<{ source: string; reference: string; finding: string }>;
  warnings?: string[]; assumptions?: string[];
  rdf_diff?: { added?: string[][]; removed?: string[][] }; diff?: string;
};
export type Statistics = {
  namespace: string; class_count: number;
  classes: Array<{ uri: string; name: string; label_ru?: string; comment_ru?: string; instance_count: number;
    features: Array<{ uri: string; name: string; label_ru?: string; comment_ru?: string; instances_with_value: number }> }>;
  relations?: Array<{ domain: string; range: string; property_label_ru?: string; property_name: string }>;
};

export const ontologyPath = (slug: string) => `/api/ontologies/${encodeURIComponent(slug)}`;
export function ontologyApi<T>(url: string, token: string, signal: AbortSignal,
  body?: object, method = body === undefined ? 'GET' : 'POST',
) {
  return apiJson<T>(url, {
    method, signal, headers: { 'Content-Type': 'application/json', ...toolsAuthorization(token) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export async function sha256(text: string) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}
