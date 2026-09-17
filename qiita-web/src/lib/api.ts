// Thin typed client over the control-plane REST API.
//
// KEYSTONE (not yet wired): run `npm run gen:api` to generate
// `src/lib/api/schema.d.ts` from the live `/openapi.json`, then swap this
// hand-typed surface for `openapi-fetch` + those types so the UI can never
// drift from the API. For the POC we hand-type the three read endpoints a
// viewer can actually reach; everything goes through `get()` so the swap is
// mechanical.
import { auth } from './auth.svelte';

const BASE = '/api/v1'; // same-origin; Vite proxies to the control plane in dev

// Real control-plane origin for the full-page login redirect. It MUST be the
// real CP (not the dev proxy): /auth/login sets a freshness cookie on the CP's
// own domain, and AuthRocket then bounces back to the CP's /auth/handoff, which
// only sees that cookie if it was set there. Override with VITE_QIITA_BASE.
export const CP_ORIGIN =
  (import.meta.env.VITE_QIITA_BASE as string | undefined) ?? 'https://qiita-miint.ucsd.edu';

/**
 * Browser login via AuthRocket, reusing the CLI loopback flow: this SPA plays
 * the role of the CLI's local listener. Full-page nav to the CP's /auth/login
 * with cli=1&port=<our port>; after AuthRocket, /auth/handoff redirects the
 * browser back to http://127.0.0.1:<port>/?ot_code=<code>, which handleLoginReturn
 * redeems. Open the app at http://127.0.0.1:<port> so the origin matches the loopback.
 */
export function beginLogin(): void {
  const host = location.hostname;
  const isLocal = host === 'localhost' || host === '127.0.0.1' || host === '::1';
  if (isLocal) {
    // Seamless: the CP loops the one-time code back to 127.0.0.1:<port>, which
    // this SPA redeems on return.
    const port = location.port || '5173';
    location.href = `${CP_ORIGIN}${BASE}/auth/login?cli=1&port=${port}`;
  } else {
    // LAN/remote viewer: the CP's CLI loopback only ever targets 127.0.0.1, so it
    // can't return here. Use the browser login flow, which authenticates via
    // AuthRocket and then shows a PAT to copy into the "PAT" box.
    window.open(`${CP_ORIGIN}${BASE}/auth/login`, '_blank', 'noopener');
  }
}

/** True when the seamless loopback login can return to this origin. */
export function loginIsSeamless(): boolean {
  const h = location.hostname;
  return h === 'localhost' || h === '127.0.0.1' || h === '::1';
}

export type Ok<T> = { ok: true; status: number; data: T };
export type Err = { ok: false; status: number; detail: string };
export type ApiResult<T> = Ok<T> | Err;

async function send<T>(method: string, path: string, body?: unknown): Promise<ApiResult<T>> {
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      method,
      headers: {
        ...(auth.isSet ? { Authorization: `Bearer ${auth.token}` } : {}),
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {})
      },
      body: body !== undefined ? JSON.stringify(body) : undefined
    });
  } catch (e) {
    return { ok: false, status: 0, detail: `network error: ${String(e)}` };
  }
  if (res.status === 204) return { ok: true, status: 204, data: undefined as T };
  const b: unknown = await res.json().catch(() => ({}));
  if (res.ok) return { ok: true, status: res.status, data: b as T };
  const detail =
    b && typeof b === 'object' && 'detail' in b ? String((b as { detail: unknown }).detail) : res.statusText;
  return { ok: false, status: res.status, detail };
}

async function get<T>(path: string): Promise<ApiResult<T>> {
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      headers: auth.isSet ? { Authorization: `Bearer ${auth.token}` } : {}
    });
  } catch (e) {
    return { ok: false, status: 0, detail: `network error: ${String(e)}` };
  }
  const body: unknown = await res.json().catch(() => ({}));
  if (res.ok) return { ok: true, status: res.status, data: body as T };
  const detail =
    body && typeof body === 'object' && 'detail' in body
      ? String((body as { detail: unknown }).detail)
      : res.statusText;
  return { ok: false, status: res.status, detail };
}

export type Whoami = {
  kind: string;
  principal_idx: number;
  email: string;
  system_role: string;
  scopes: string[];
  profile_complete: boolean;
};

export type IdxList = { idxs: number[]; truncated?: boolean };

export type Reference = {
  reference_idx: number;
  name: string;
  version: string;
  kind: string;
  status: string;
  is_host: boolean;
  created_by_idx: number;
  created_at: string;
};

export type ReferenceIndex = {
  reference_index_idx: number;
  reference_idx: number;
  index_type: string;
  fs_path: string;
  params: Record<string, unknown>;
  created_at: string;
  shard_id: number | null;
};

export type ReferenceExclusion = {
  feature_idx: number;
  genome_idx: number | null;
  reason: string | null;
  source: string | null;
  source_id: string | null;
  accession: string | null;
  direct_block: boolean;
  via_genome: boolean;
  excluded_at: string | null;
};

export type PrepProtocol = {
  prep_protocol_idx: number;
  name: string;
  description: string | null;
  retired: boolean;
};

export type MetaField = {
  display_name: string;
  description: string | null;
  data_type: string;
  value: unknown; // scalar, or { kind: 'missing_reason', ... } for absent values
};

export type Biosample = {
  biosample_idx: number;
  biosample_accession: string | null;
  ena_sample_accession: string | null;
  metadata_checklist: { idx: number; name: string } | null;
  global_metadata: Record<string, MetaField>;
  retired: boolean;
};

/** Redeem the one-time ot_code from the loopback for the PAT plaintext. */
export async function cliExchange(otCode: string): Promise<ApiResult<{ token: string }>> {
  let res: Response;
  try {
    res = await fetch(BASE + '/auth/cli-exchange', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ot_code: otCode })
    });
  } catch (e) {
    return { ok: false, status: 0, detail: `network error: ${String(e)}` };
  }
  const body: unknown = await res.json().catch(() => ({}));
  if (res.ok) return { ok: true, status: res.status, data: body as { token: string } };
  const detail =
    body && typeof body === 'object' && 'detail' in body
      ? String((body as { detail: unknown }).detail)
      : res.statusText;
  return { ok: false, status: res.status, detail };
}

export type UserProfile = {
  principal_idx: number;
  display_name: string;
  email: string;
  affiliation: string;
  address: string;
  phone: string;
  orcid: string | null;
  receive_processing_emails: boolean;
  profile_complete: boolean;
  created_at: string;
  updated_at: string;
};

export type UserUpdate = {
  affiliation?: string;
  address?: string;
  phone?: string;
  orcid?: string | null;
  receive_processing_emails?: boolean;
};

export type ApiToken = {
  token_idx: number;
  label: string;
  scopes: string[];
  expires_at: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
  created_at: string;
};

export const api = {
  whoami: () => get<Whoami>('/auth/whoami'),
  getProfile: () => get<UserProfile>('/user/me'),
  updateProfile: (body: UserUpdate) => send<UserProfile>('PATCH', '/user/me', body),
  listTokens: () => get<ApiToken[]>('/auth/token'),
  revokeToken: (idx: number) => send<void>('DELETE', `/auth/token/${idx}`),
  /** Loaded reference databases + artifact sequence sets. Needs reference:read. */
  references: () => get<Reference[]>('/reference'),
  reference: (idx: number) => get<Reference>(`/reference/${idx}`),
  referenceIndices: (idx: number) => get<ReferenceIndex[]>(`/reference/${idx}/index`),
  referenceExclusions: (idx: number) => get<ReferenceExclusion[]>(`/reference/${idx}/exclusion`),
  referenceGenomeMap: (idx: number) =>
    get<{ reference_idx: number; entries: unknown[]; count?: number }>(`/reference/${idx}/genome-map`),
  /** Library-prep protocols (config catalog). */
  prepProtocols: () => get<PrepProtocol[]>('/prep-protocol'),
  /** The study record (title/owner/accessions). Needs tier >= member. */
  studyRecord: (studyIdx: number) => get<Record<string, unknown>>(`/study/${studyIdx}`),
  /** Sample idx set for a study. Needs tier >= viewer. */
  sequencedIdxs: (studyIdx: number) =>
    get<IdxList>(`/study/${studyIdx}/sequenced-sample/list-idxs`),
  biosampleIdxs: (studyIdx: number) =>
    get<IdxList>(`/study/${studyIdx}/biosample/list-idxs`),
  /** One biosample incl. its global_metadata values. Readable at viewer tier. */
  biosample: (idx: number) => get<Biosample>(`/biosample/${idx}`)
};
