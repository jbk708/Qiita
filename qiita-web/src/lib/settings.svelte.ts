// Client-side UI preferences, persisted to localStorage. Toggled from the Settings
// page. Mostly cosmetic; the `llm` block holds the user's own LLM endpoint (a client
// feature, like metameq — it talks to their endpoint, never to Qiita).
const KEY = 'qiita-web:settings';

/** A user-supplied, OpenAI-compatible chat endpoint (the `/chat/completions` wire
 *  shape — any provider that speaks it: OpenAI, Anthropic's compat endpoint, a local
 *  Ollama/LM Studio, a proxy). Used only to suggest value→vocabulary fixes for
 *  metadata metameq flags as unknown; off by default, and the key lives only in this
 *  browser. */
export type LlmSettings = {
  enabled: boolean;
  /** Base URL including the version segment, e.g. `https://api.openai.com/v1`. */
  baseUrl: string;
  apiKey: string;
  model: string;
};

export type Settings = {
  /** Title-case metadata field labels ("host_taxon_id" → "Host Taxon Id"). */
  humanizeLabels: boolean;
  /** Record visited/created studies under "Recent studies". */
  rememberRecents: boolean;
  llm: LlmSettings;
};

const defaults: Settings = {
  humanizeLabels: true,
  rememberRecents: true,
  // Default baseUrl + model to be filled in once chosen.
  llm: { enabled: false, baseUrl: '', apiKey: '', model: '' }
};

function load(): Settings {
  if (typeof localStorage === 'undefined') return { ...defaults };
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? '{}') as Partial<Settings>;
    // Merge nested llm so a saved blob from before this field still gets defaults.
    return { ...defaults, ...saved, llm: { ...defaults.llm, ...(saved.llm ?? {}) } };
  } catch {
    return { ...defaults };
  }
}

export const settings = $state<Settings>(load());

/** Persist the current settings. Call after any toggle. */
export function saveSettings(): void {
  if (typeof localStorage !== 'undefined') localStorage.setItem(KEY, JSON.stringify(settings));
}
