// The metadata review loop's model: metameq's findings (via static/metadata_review.py)
// mapped onto the upload grid, grouped for bulk fixes, and tracked across edits
// until the next validation.

export type IssueKind = 'error' | 'type' | 'curation';

export type Suggestion = { value: string; label: string; rule: string };

export type Issue = {
  sample: string;
  field: string;
  value: string | null;
  kind: IssueKind;
  message: string;
  suggestions: Suggestion[];
};

export type Extension = {
  added: { column: string; values: Record<string, number> }[];
  filled: { column: string; count: number; values: Record<string, number> }[];
  flags: { column: string; source: string; message: string }[];
};

export type Review = {
  processed: boolean;
  errors: { message: string }[];
  issues: Issue[];
  extension: Extension | null;
  summary: { cells: number; samples: number; rows: number; fixable: number };
  hostColumn?: string;
  sampleTypeColumn?: string;
};

/** Rules whose suggestion is safe to apply without a judgement call (mirrors
 *  AUTO_FIX_RULES in metadata_review.py). */
export const AUTO_FIX_RULES = new Set([
  'iso-date',
  'spelling',
  'host-from-sample-type',
  'host-synonym'
]);

export const KIND_STYLE: Record<IssueKind, { cell: string; chip: string; label: string }> = {
  error: { cell: 'bg-red-50 text-red-800', chip: 'bg-red-50 text-red-700 ring-red-600/20', label: 'error' },
  type: {
    cell: 'bg-violet-50 text-violet-800',
    chip: 'bg-violet-50 text-violet-700 ring-violet-600/20',
    label: 'unknown type'
  },
  curation: {
    cell: 'bg-amber-50 text-amber-800',
    chip: 'bg-amber-50 text-amber-700 ring-amber-600/20',
    label: 'curation'
  }
};

export const cellKey = (r: number, c: number) => `${r}:${c}`;

export type CellIssue = Issue & { r: number; c: number; group: string };

/** Each issue placed on the grid (row by the sample_name column, column by
 *  header), and the issues whose field is not a column of the sheet. */
export function placeIssues(
  review: Review,
  headers: string[],
  rows: string[][]
): { cells: Map<string, CellIssue>; unplaced: Issue[] } {
  const nameCol = headers.indexOf('sample_name');
  const rowOf = new Map<string, number>();
  if (nameCol >= 0) rows.forEach((cells, r) => rowOf.set(cells[nameCol] ?? '', r));
  const colOf = new Map(headers.map((h, i) => [h, i]));
  const cells = new Map<string, CellIssue>();
  const unplaced: Issue[] = [];
  for (const issue of review.issues) {
    const r = rowOf.get(issue.sample);
    const c = colOf.get(issue.field);
    if (r === undefined || c === undefined) {
      unplaced.push(issue);
      continue;
    }
    cells.set(cellKey(r, c), { ...issue, r, c, group: groupKey(issue) });
  }
  return { cells, unplaced };
}

// Rules whose suggested value is computed per cell (each date converts to its
// own ISO form): their cells group by field, not by value.
const PER_CELL_RULES = new Set(['iso-date']);

/** Issues that share a fix: same kind, field and value (and, for an unknown type,
 *  the same inferred candidates, which depend on the row's sample type) — or, for
 *  a per-cell rule, same kind, field and rule. */
export function groupKey(i: Issue): string {
  const rule = i.suggestions[0]?.rule;
  if (rule && PER_CELL_RULES.has(rule)) return `${i.kind}\u0000${i.field}\u0000\u0000${rule}`;
  const extra = i.kind === 'type' ? i.suggestions.map((s) => s.value).join('|') : '';
  return `${i.kind}\u0000${i.field}\u0000${i.value ?? ''}\u0000${extra}`;
}

/** The rule a group's cells are fixed by cell-by-cell, if any. */
export function perCellRule(g: IssueGroup): string | null {
  const rule = g.cells[0]?.suggestions[0]?.rule;
  return rule && PER_CELL_RULES.has(rule) ? rule : null;
}

export type IssueGroup = {
  key: string;
  kind: IssueKind;
  field: string;
  value: string | null;
  message: string;
  suggestions: Suggestion[];
  cells: CellIssue[];
};

/** Open issues grouped by shared fix, largest first. */
export function groupIssues(cells: Iterable<CellIssue>): IssueGroup[] {
  const groups = new Map<string, IssueGroup>();
  for (const ci of cells) {
    let g = groups.get(ci.group);
    if (!g) {
      g = {
        key: ci.group,
        kind: ci.kind,
        field: ci.field,
        value: ci.value,
        message: ci.message,
        suggestions: ci.suggestions,
        cells: []
      };
      groups.set(ci.group, g);
    }
    g.cells.push(ci);
  }
  return [...groups.values()].sort((a, b) => b.cells.length - a.cells.length || a.field.localeCompare(b.field));
}
