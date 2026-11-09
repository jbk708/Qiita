import { describe, it, expect, vi } from 'vitest';
import { validateWithResolution, contextSummary } from './typeResolution';
import type { MetameqValidationResult, TypeTaxonomy } from '$lib/pyodide/types';

const TAXONOMY: TypeTaxonomy = {
  hostTypes: ['human', 'mouse'],
  sampleTypesByHost: { human: ['stool'], mouse: ['feces'] },
  hostOfSampleType: { stool: ['human'], feces: ['mouse'] },
  hostColumn: 'host_common_name',
  sampleColumn: 'sample_type'
};

function result(over: Partial<MetameqValidationResult> = {}): MetameqValidationResult {
  return {
    success: true,
    processed: true,
    errors: [],
    extendedMetadata: [],
    validationErrors: null,
    originalColumns: ['sample_name', 'ph'],
    needsTypeResolution: false,
    unresolved: [],
    taxonomy: null,
    resolvedContext: null,
    summary: { totalRows: 0, validRows: 0, errorCount: 0 },
    ...over
  };
}

describe('validateWithResolution', () => {
  it('returns the first result when no resolution is needed, without prompting', async () => {
    const validate = vi.fn().mockResolvedValue(result({ processed: true }));
    const prompt = vi.fn();
    const out = await validateWithResolution(validate, prompt);
    expect(out.processed).toBe(true);
    expect(validate).toHaveBeenCalledTimes(1);
    expect(validate).toHaveBeenCalledWith(); // first pass has no resolution
    expect(prompt).not.toHaveBeenCalled();
  });

  it('prompts, then re-validates with the chosen resolution', async () => {
    const validate = vi
      .fn()
      .mockResolvedValueOnce(
        result({ processed: false, needsTypeResolution: true, taxonomy: TAXONOMY, unresolved: ['host', 'sample'] })
      )
      .mockResolvedValueOnce(result({ processed: true }));
    const chosen = { host: { value: 'human' }, sample: { value: 'stool' } };
    const prompt = vi.fn().mockResolvedValue(chosen);

    const out = await validateWithResolution(validate, prompt);
    expect(out.processed).toBe(true);
    expect(prompt).toHaveBeenCalledWith({
      taxonomy: TAXONOMY,
      columns: ['sample_name', 'ph'],
      context: null
    });
    expect(validate).toHaveBeenNthCalledWith(2, chosen);
  });

  it('returns the needs-resolution result (and does not re-validate) when the user cancels', async () => {
    const validate = vi
      .fn()
      .mockResolvedValue(result({ processed: false, needsTypeResolution: true, taxonomy: TAXONOMY }));
    const prompt = vi.fn().mockResolvedValue(null);

    const out = await validateWithResolution(validate, prompt);
    expect(out.needsTypeResolution).toBe(true);
    expect(validate).toHaveBeenCalledTimes(1);
  });
});

describe('contextSummary', () => {
  it('summarises a column and a value', () => {
    expect(
      contextSummary({ host: { mode: 'inferred', column: 'host_common_name' }, sample: { mode: 'value', value: 'stool' } })
    ).toBe('host: host_common_name · sample type: stool');
  });

  it('is null when nothing resolved', () => {
    expect(contextSummary(null)).toBeNull();
    expect(contextSummary({ host: null, sample: null })).toBeNull();
  });
});
