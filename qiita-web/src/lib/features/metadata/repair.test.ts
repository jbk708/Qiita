import { describe, it, expect } from 'vitest';
import { collectRepairTargets, mergeRepairs, proposedFixes, type RepairSuggestions } from './repair';
import { parseSuggestions } from '$lib/llm/normalize';
import type { Review, Issue } from './review';
import type { TypeTaxonomy } from '$lib/pyodide/types';

const TAXONOMY: TypeTaxonomy = {
  hostTypes: ['human', 'mouse', 'control'],
  sampleTypesByHost: { human: ['stool'], mouse: ['feces'] },
  hostOfSampleType: { stool: ['human'], feces: ['mouse'] },
  hostColumn: 'host_taxon_id',
  sampleColumn: 'sample_type'
};

function issue(over: Partial<Issue>): Issue {
  return { sample: 's', field: 'f', value: 'v', kind: 'error', message: '', suggestions: [], ...over };
}

function review(issues: Issue[]): Review {
  return {
    processed: true,
    errors: [],
    issues,
    extension: null,
    summary: { cells: issues.length, samples: 1, rows: 1, fixable: 0 },
    hostColumn: 'host_taxon_id',
    sampleTypeColumn: 'sample_type'
  };
}

describe('collectRepairTargets', () => {
  it('targets unknown host values against the host vocabulary', () => {
    const r = review([issue({ field: 'host_taxon_id', value: 'Gallus gallus', kind: 'type' })]);
    const targets = collectRepairTargets(r, TAXONOMY);
    expect(targets).toHaveLength(1);
    expect(targets[0]).toMatchObject({ field: 'host_taxon_id', values: ['Gallus gallus'] });
    expect(targets[0].allowed).toEqual(TAXONOMY.hostTypes);
  });

  it('skips values a deterministic rule already fixes', () => {
    const r = review([
      issue({
        field: 'host_taxon_id',
        value: 'Mus musculus',
        kind: 'type',
        suggestions: [{ value: 'mouse', label: 'mouse', rule: 'host-synonym' }]
      })
    ]);
    expect(collectRepairTargets(r, TAXONOMY)).toHaveLength(0);
  });

  it('targets uncastable scalar values with a transform instruction', () => {
    const r = review([
      issue({ field: 'taxon_id', value: 'metagenome', kind: 'error', message: 'must be of integer type' })
    ]);
    const targets = collectRepairTargets(r, TAXONOMY);
    expect(targets).toHaveLength(1);
    expect(targets[0].field).toBe('taxon_id');
    expect(targets[0].instruction).toMatch(/number|NCBI/i);
    expect(targets[0].allowed).toBeUndefined();
  });

  it('de-duplicates values per field across samples', () => {
    const r = review([
      issue({ sample: 'a', field: 'host_taxon_id', value: 'Gallus gallus', kind: 'type' }),
      issue({ sample: 'b', field: 'host_taxon_id', value: 'Gallus gallus', kind: 'type' })
    ]);
    expect(collectRepairTargets(r, TAXONOMY)[0].values).toEqual(['Gallus gallus']);
  });
});

describe('mergeRepairs + proposedFixes', () => {
  it('folds LLM proposals onto issues and collapses them per (field,value,fix)', () => {
    const r = review([
      issue({ sample: 'a', field: 'taxon_id', value: 'metagenome', kind: 'error', message: 'must be of integer type' }),
      issue({ sample: 'b', field: 'taxon_id', value: 'metagenome', kind: 'error', message: 'must be of integer type' })
    ]);
    const repairs: RepairSuggestions = new Map([
      ['taxon_id', new Map([['metagenome', { value: 'metagenome', suggestion: '256318', confidence: 0.9 }]])]
    ]);
    mergeRepairs(r, repairs);
    expect(r.issues[0].suggestions[0]).toMatchObject({ value: '256318', rule: 'llm' });

    const fixes = proposedFixes(r);
    expect(fixes).toHaveLength(1);
    expect(fixes[0]).toMatchObject({ field: 'taxon_id', suggestion: '256318', deterministic: false, samples: 2 });
  });

  it('orders deterministic fixes before LLM guesses', () => {
    const r = review([
      issue({ field: 'host_taxon_id', value: 'Mus musculus', kind: 'type', suggestions: [{ value: 'mouse', label: 'mouse', rule: 'host-synonym' }] }),
      issue({ field: 'taxon_id', value: 'metagenome', kind: 'error', suggestions: [{ value: '256318', label: '256318 (LLM)', rule: 'llm' }] })
    ]);
    const fixes = proposedFixes(r);
    expect(fixes[0].deterministic).toBe(true);
    expect(fixes[1].deterministic).toBe(false);
  });
});

describe('parseSuggestions', () => {
  it('keeps only asked-for values and clamps confidence', () => {
    const content = JSON.stringify({
      mappings: [
        { value: 'metagenome', suggestion: '256318', confidence: 0.9 },
        { value: 'nope', suggestion: 'x', confidence: 0.5 }
      ]
    });
    const out = parseSuggestions(content, ['metagenome']);
    expect(out).toEqual([{ value: 'metagenome', suggestion: '256318', confidence: 0.9 }]);
  });

  it('tolerates prose around the JSON and null suggestions', () => {
    const content = 'Sure!\n{"mappings":[{"value":"xyz","suggestion":null,"confidence":2}]}\nHope that helps';
    expect(parseSuggestions(content, ['xyz'])).toEqual([{ value: 'xyz', suggestion: null, confidence: 0 }]);
  });
});
