// Client-side value→vocabulary normalizer: given a field, a handful of distinct
// values metameq flagged as unknown, and (optionally) the allowed vocabulary, ask the
// user's own OpenAI-compatible endpoint which allowed term each value most likely means.
//
// This is a client feature, like metameq: it calls the endpoint configured in Settings
// (never a Qiita backend), and it is deliberately NARROW — it proposes a mapping into a
// vocabulary the caller supplies, it does not validate and it is never authoritative.
// The human accepts or rejects what it returns. It also keeps the payload to a
// dictionary problem: a field name, the distinct values, and the allowed terms — no
// rows, no sample identifiers, no dataset.

import type { LlmSettings } from '$lib/settings.svelte';

export type NormalizeRequest = {
  /** The field whose values are being mapped (for the model's context only). */
  field: string;
  /** The distinct values to map. Keep these de-duplicated and small. */
  values: string[];
  /** The vocabulary to map into. Omit for a free transform (then give `instruction`). */
  allowed?: string[];
  /** Extra guidance, e.g. "Return the NCBI Taxonomy id as an integer, or null." */
  instruction?: string;
};

/** One proposed mapping. `confidence` is the model's own 0–1 estimate; `suggestion` is
 *  null when it has no confident answer (so the caller can leave the cell for a human). */
export type Suggestion = { value: string; suggestion: string | null; confidence: number };

export class LlmError extends Error {}

/** Map each value to its best allowed term (or per `instruction`) via the configured
 *  endpoint. Throws `LlmError` on a misconfiguration or a failed call — callers treat
 *  the normalizer as advisory and degrade to metameq-only on failure. */
export async function normalizeValues(
  req: NormalizeRequest,
  llm: LlmSettings,
  signal?: AbortSignal
): Promise<Suggestion[]> {
  if (!llm.enabled) throw new LlmError('The LLM normalizer is turned off in Settings.');
  if (!llm.baseUrl || !llm.model) throw new LlmError('Set the LLM base URL and model in Settings.');
  const values = [...new Set(req.values.map((v) => v.trim()).filter(Boolean))];
  if (!values.length) return [];

  const system =
    'You map messy metadata values to a controlled vocabulary for a microbiome study. ' +
    'For each input value, choose the single best match' +
    (req.allowed?.length ? ' from the allowed list' : '') +
    (req.instruction ? `. ${req.instruction}` : '') +
    '. If you are not confident, return null for that value rather than guessing. ' +
    'Reply with ONLY a JSON object of the form ' +
    '{"mappings":[{"value":<input>,"suggestion":<term or null>,"confidence":<0..1>}]}.';
  const user = JSON.stringify({
    field: req.field,
    values,
    ...(req.allowed?.length ? { allowed: req.allowed } : {})
  });

  let res: Response;
  try {
    res = await fetch(`${llm.baseUrl.replace(/\/$/, '')}/chat/completions`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(llm.apiKey ? { authorization: `Bearer ${llm.apiKey}` } : {})
      },
      body: JSON.stringify({
        model: llm.model,
        temperature: 0,
        response_format: { type: 'json_object' },
        messages: [
          { role: 'system', content: system },
          { role: 'user', content: user }
        ]
      }),
      signal
    });
  } catch (e) {
    throw new LlmError(`Couldn't reach the LLM endpoint: ${e instanceof Error ? e.message : String(e)}`);
  }
  if (!res.ok) throw new LlmError(`LLM endpoint returned ${res.status} ${res.statusText}`);

  const body = await res.json();
  const content: unknown = body?.choices?.[0]?.message?.content;
  if (typeof content !== 'string') throw new LlmError('LLM response had no message content.');
  return parseSuggestions(content, values);
}

/** Pull the `mappings` array out of the model's reply, tolerating prose around the JSON
 *  (endpoints that ignore `response_format` still answer with a JSON blob). Only values
 *  we asked about are kept; anything malformed is dropped rather than thrown. */
export function parseSuggestions(content: string, asked: string[]): Suggestion[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(content);
  } catch {
    const match = content.match(/\{[\s\S]*\}/);
    if (!match) throw new LlmError('LLM response was not JSON.');
    parsed = JSON.parse(match[0]);
  }
  const rows = (parsed as { mappings?: unknown })?.mappings;
  if (!Array.isArray(rows)) throw new LlmError('LLM response had no "mappings" array.');
  const want = new Set(asked);
  const out: Suggestion[] = [];
  for (const r of rows) {
    const value = (r as { value?: unknown })?.value;
    if (typeof value !== 'string' || !want.has(value)) continue;
    const s = (r as { suggestion?: unknown }).suggestion;
    const c = (r as { confidence?: unknown }).confidence;
    out.push({
      value,
      suggestion: typeof s === 'string' && s.trim() ? s.trim() : null,
      confidence: typeof c === 'number' && c >= 0 && c <= 1 ? c : 0
    });
  }
  return out;
}
