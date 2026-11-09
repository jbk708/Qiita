// Barebones repair strategy: metameq finds the problems (and offers the confident,
// deterministic fixes); this asks the user's LLM endpoint to propose fixes ONLY for the
// findings metameq left unresolved — unknown host/sample types, and values that don't
// fit a field's type. Every proposal is a candidate a human still accepts; nothing here
// validates or overrides metameq. The split is by ambiguity: a deterministic auto-fix
// never reaches the LLM; what's left, the LLM takes a non-authoritative guess at.

import type { Review } from './review';
import { AUTO_FIX_RULES } from './review';
import type { TypeTaxonomy } from '$lib/pyodide/types';
import type { LlmSettings } from '$lib/settings.svelte';
import { normalizeValues, type Suggestion as LlmSuggestion } from '$lib/llm/normalize';

/** Distinct unknown values for one field, grouped so a single call covers them all. */
export type RepairTarget = {
  field: string;
  values: string[];
  allowed?: string[];
  instruction?: string;
};

/** The LLM's confident proposals, as `field → (value → suggestion)`. */
export type RepairSuggestions = Map<string, Map<string, LlmSuggestion>>;

// metameq's pre-cast, degraded to a cerberus finding, reads like this.
const SCALAR_TYPE_ERROR = /must be of (integer|number|float) type/i;

/** Which findings warrant an LLM guess: unknown host/sample types, and values that fail
 *  a field's scalar type — minus anything a deterministic rule already fixes confidently.
 *  Grouped into one target per field. */
export function collectRepairTargets(
  review: Review,
  taxonomy: TypeTaxonomy | null
): RepairTarget[] {
  const byField = new Map<string, RepairTarget>();
  const target = (field: string, base: Omit<RepairTarget, 'field' | 'values'>): RepairTarget => {
    let t = byField.get(field);
    if (!t) {
      t = { field, values: [], ...base };
      byField.set(field, t);
    }
    return t;
  };

  for (const issue of review.issues) {
    if (issue.value == null || issue.value === '') continue;
    // A confident deterministic fix already exists — don't spend an LLM call on it.
    if (issue.suggestions.some((s) => AUTO_FIX_RULES.has(s.rule))) continue;

    if (issue.kind === 'type' && taxonomy) {
      const allowed =
        issue.field === review.hostColumn
          ? taxonomy.hostTypes
          : issue.field === review.sampleTypeColumn
            ? Object.keys(taxonomy.hostOfSampleType)
            : null;
      if (!allowed?.length) continue;
      addValue(target(issue.field, { allowed }), issue.value);
    } else if (issue.kind === 'error' && SCALAR_TYPE_ERROR.test(issue.message)) {
      addValue(
        target(issue.field, {
          instruction:
            'Return the value as a number — e.g. the NCBI Taxonomy id for a taxon name — or null if unknown.'
        }),
        issue.value
      );
    }
  }
  return [...byField.values()].filter((t) => t.values.length > 0);
}

function addValue(t: RepairTarget, v: string): void {
  if (!t.values.includes(v)) t.values.push(v);
}

/** Run each target through the user's endpoint and keep the confident, non-null
 *  proposals. One field's failure doesn't sink the rest — the normalizer is advisory. */
export async function proposeRepairs(
  targets: RepairTarget[],
  llm: LlmSettings,
  signal?: AbortSignal
): Promise<RepairSuggestions> {
  const out: RepairSuggestions = new Map();
  for (const t of targets) {
    let suggestions: LlmSuggestion[];
    try {
      suggestions = await normalizeValues(t, llm, signal);
    } catch {
      continue;
    }
    const perValue = new Map<string, LlmSuggestion>();
    for (const s of suggestions) if (s.suggestion) perValue.set(s.value, s);
    if (perValue.size) out.set(t.field, perValue);
  }
  return out;
}

/** Merge the LLM's proposals onto the review's issues as `llm`-rule suggestions, so they
 *  flow through the same path as metameq's deterministic ones. Mutates `review`. */
export function mergeRepairs(review: Review, repairs: RepairSuggestions): void {
  for (const issue of review.issues) {
    if (issue.value == null) continue;
    const s = repairs.get(issue.field)?.get(issue.value);
    if (!s?.suggestion) continue;
    if (issue.suggestions.some((x) => x.value === s.suggestion)) continue;
    issue.suggestions.push({
      value: s.suggestion,
      label: `${s.suggestion} (LLM ${Math.round(s.confidence * 100)}%)`,
      rule: 'llm'
    });
  }
}

/** One proposed fix, de-duplicated across the samples it applies to — the compact view
 *  for the "proposed fixes" list. `deterministic` separates metameq's confident fixes
 *  from the LLM's guesses. */
export type ProposedFix = {
  field: string;
  value: string;
  suggestion: string;
  label: string;
  rule: string;
  deterministic: boolean;
  samples: number;
};

/** Collapse the per-sample issue suggestions into one row per (field, value, fix),
 *  confident deterministic fixes first. */
export function proposedFixes(review: Review): ProposedFix[] {
  const by = new Map<string, ProposedFix>();
  for (const issue of review.issues) {
    const top = issue.suggestions[0];
    if (!top || issue.value == null) continue;
    const key = `${issue.field}\u0000${issue.value}\u0000${top.value}`;
    const existing = by.get(key);
    if (existing) {
      existing.samples += 1;
    } else {
      by.set(key, {
        field: issue.field,
        value: String(issue.value),
        suggestion: top.value,
        label: top.label,
        rule: top.rule,
        deterministic: AUTO_FIX_RULES.has(top.rule),
        samples: 1
      });
    }
  }
  return [...by.values()].sort(
    (a, b) => Number(b.deterministic) - Number(a.deterministic) || b.samples - a.samples
  );
}
