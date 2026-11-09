// Shared validate → prompt → re-validate loop for metameq, used by both the sample
// grid and the uploader. metameq needs each row's host and sample type; when neither
// a known column nor a value sniff found them, the first result is
// `needsTypeResolution`. This asks the caller to prompt (the TypeResolutionModal) and
// re-validates with the answer. The modal itself lives in the component; this owns
// only the loop, so it stays plain and unit-testable.

import type {
  MetameqValidationResult,
  TypeResolution,
  TypeTaxonomy,
  ResolvedAxisContext
} from '$lib/pyodide/types';

export type ResolutionPrompt = {
  taxonomy: TypeTaxonomy;
  columns: string[];
  context: { host: ResolvedAxisContext | null; sample: ResolvedAxisContext | null } | null;
};

/** Validate, and if metameq cannot tell the host/sample type, `prompt` the user and
 *  validate once more with their answer. Returns the final result — which is still a
 *  `needsTypeResolution` one if the user cancels (`prompt` resolves null), so callers
 *  render that as "not validated" rather than a hard error. */
export async function validateWithResolution(
  validate: (resolution?: TypeResolution | null) => Promise<MetameqValidationResult>,
  prompt: (p: ResolutionPrompt) => Promise<TypeResolution | null>
): Promise<MetameqValidationResult> {
  const first = await validate();
  if (!first.needsTypeResolution || !first.taxonomy) return first;

  const resolution = await prompt({
    taxonomy: first.taxonomy,
    columns: first.originalColumns ?? [],
    context: first.resolvedContext
  });
  if (!resolution) return first;

  return validate(resolution);
}

/** A one-line account of how the host and sample type were resolved, for the result
 *  header (e.g. "host: host_common_name · sample type: stool"). Null when nothing ran. */
export function contextSummary(
  context: { host: ResolvedAxisContext | null; sample: ResolvedAxisContext | null } | null
): string | null {
  if (!context) return null;
  const part = (axis: ResolvedAxisContext | null) => {
    if (!axis) return null;
    if (axis.mode === 'value') return axis.value ?? null;
    return axis.column ?? null;
  };
  const host = part(context.host);
  const sample = part(context.sample);
  const bits = [host && `host: ${host}`, sample && `sample type: ${sample}`].filter(Boolean);
  return bits.length ? bits.join(' · ') : null;
}
