<script lang="ts" module>
  // Every change made in the sheet — typing, paste, delete, fill, undo, redo — reaches
  // the parent as CellEdits, so the parent decides where each value lives.
  export type { CellEdit, GridColumn } from '$lib/table/model';
  // Per-instance id, so the generated column-tint rule targets only this grid.
  let sheetSeq = 0;
  export const nextSheetId = () => `qsheet-${++sheetSeq}`;
</script>

<script lang="ts">
  import type { CellEdit, GridColumn, Row } from '$lib/table/model';
  // A jspreadsheet worksheet kept in step with rows the parent owns.
  //
  // jspreadsheet holds its own copy of the data, so syncing is two one-way paths:
  //   sheet -> parent: every user change (onafterchanges, and onundo/onredo, which
  //     jspreadsheet applies with events off) becomes `onedit`, never a write to `rows`;
  //   parent -> sheet: when `rows` changes, only the cells that differ are written back,
  //     with events and history off, so a sync is neither re-reported nor undoable.
  // Sorting and filtering reorder jspreadsheet's rows, so each row carries its index in
  // the parent's array in a hidden first column, and every lookup goes through it.
  import { onDestroy, onMount } from 'svelte';
  import 'jsuites/dist/jsuites.css';
  import 'jspreadsheet-ce/dist/jspreadsheet.css';

  let {
    columns,
    rows,
    issues = new Map(),
    onedit,
    readOnly = false,
    height = '65vh',
    externalSearch = false,
    pinFirst = false
  }: {
    columns: GridColumn[];
    rows: Row[];
    /** `row:column` -> message, as `issueIndex` builds it. */
    issues?: Map<string, string>;
    onedit?: (edits: CellEdit[]) => void;
    readOnly?: boolean;
    height?: string;
    /** Suppress jspreadsheet's own search box (its toolbar bar is never rendered); the
     *  parent drives filtering via the exported `search()` (e.g. an input in its own
     *  toolbar). Filtering still happens inside the sheet, so the issue overlay stays
     *  aligned. */
    externalSearch?: boolean;
    /** Keep the first data column (and the row gutter) stuck to the left on horizontal
     *  scroll — the identity column, so a wide sheet stays readable. */
    pinFirst?: boolean;
  } = $props();

  const uid = nextSheetId();

  // jspreadsheet's types are loose; this is the slice of a worksheet used here.
  type Worksheet = {
    getData: () => unknown[][];
    getValueFromCoords: (x: number, y: number) => unknown;
    setValueFromCoords: (x: number, y: number, v: string, force?: boolean) => void;
    setData: (d: string[][]) => void;
    setStyle: (o: Record<string, string>, k?: null, v?: null, force?: boolean) => void;
    resetStyle: (o: Record<string, string>, ignore?: boolean) => void;
    setComments: (o: Record<string, string>) => void;
    ignoreHistory: boolean;
    parent: { ignoreEvents?: boolean };
    options: { editable?: boolean };
    updateSelectionFromCoords: (x1: number, y1: number, x2: number, y2: number) => void;
    getCellFromCoords: (x: number, y: number) => HTMLTableCellElement | undefined;
    edition: null | [HTMLTableCellElement, string, string, string];
    getSelection: () => [number, number, number, number] | undefined;
    redo: () => void;
    search: (query: string) => void;
    closeEditor: (cell: HTMLTableCellElement, save: boolean) => void;
  };

  let el: HTMLDivElement;
  let ws: Worksheet | null = null;
  let destroy: (() => void) | null = null;
  let onRedoKey: ((e: KeyboardEvent) => void) | null = null;
  const KEY = 0; // the hidden column holding each row's index in `rows`

  const matrix = (rs: Row[], cols: GridColumn[]) =>
    rs.map((r, i) => [String(i), ...cols.map((c) => r[c.name] ?? '')]);

  /** jspreadsheet's cell name (A1-style) for a display position. */
  const cellName = (x: number, y: number) => {
    let n = '';
    for (let i = x; i >= 0; i = Math.floor(i / 26) - 1) n = String.fromCharCode(65 + (i % 26)) + n;
    return `${n}${y + 1}`;
  };

  /** Run `fn` with the worksheet's events and history off: for writes that mirror the
   *  parent's state rather than a user's action. */
  function quietly(fn: (w: Worksheet) => void) {
    const w = ws;
    if (!w) return;
    const events = w.parent.ignoreEvents;
    const history = w.ignoreHistory;
    w.parent.ignoreEvents = true;
    w.ignoreHistory = true;
    try {
      fn(w);
    } finally {
      w.parent.ignoreEvents = events;
      w.ignoreHistory = history;
    }
  }

  /** The parent's row index for a display row. */
  const rowAt = (y: number) => Number(ws?.getValueFromCoords(KEY, y));

  /** Select one cell, by the parent's row index and a column name, and scroll it into
   *  view — wherever a sort has put it. Returns false when it isn't showing (a filter
   *  hides its row, or the sheet hasn't loaded). */
  export function focusCell(row: number, column: string): boolean {
    if (!ws) return false;
    const y = ws.getData().findIndex((cells) => Number(cells[KEY]) === row);
    const x = columns.findIndex((c) => c.name === column) + 1;
    if (y < 0 || x < 1) return false;
    const td = ws.getCellFromCoords(x, y);
    if (!td || td.offsetParent === null) return false;
    ws.updateSelectionFromCoords(x, y, x, y);
    td.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' });
    return true;
  }

  /** Filter the grid to rows matching `query` (jspreadsheet's own search, driven from
   *  the parent's toolbar when `externalSearch` hides the built-in box). */
  export function search(query: string): void {
    ws?.search(query);
  }

  /** Turn changed display cells into edits on the parent's rows. */
  function report(cellsXY: { x: number | string; y: number | string }[]) {
    if (!ws || !onedit) return;
    const edits: CellEdit[] = [];
    for (const { x, y } of cellsXY) {
      const col = columns[Number(x) - 1];
      if (!col) continue; // the hidden key column
      edits.push({ row: rowAt(Number(y)), column: col.name, value: String(ws.getValueFromCoords(Number(x), Number(y)) ?? '') });
    }
    if (edits.length) onedit(edits);
  }

  /** The column set the grid was built with. jspreadsheet can't change its columns in
   *  place, so a different set rebuilds the grid (and starts a new undo history). */
  let builtFor = '';
  const columnsKey = (cols: GridColumn[]) => JSON.stringify(cols.map((c) => [c.name, c.label, c.locked, c.width, c.hint]));
  const loadLib = async () => (await import('jspreadsheet-ce')).default;
  let lib: Awaited<ReturnType<typeof loadLib>> | null = null;

  function build() {
    const jspreadsheet = lib;
    if (!jspreadsheet) return;
    builtFor = columnsKey(columns);
    {
      const sheets = jspreadsheet(el, {
        tabs: false,
        toolbar: false,
        // Every cell is text. jspreadsheet's formula engine hands a cell starting with
        // "=" to `new Function`, so a value from a loaded file or another user's
        // metadata would run as script in the app, with the viewer's token.
        parseFormulas: false,
        worksheets: [
          {
            data: matrix(rows, columns),
            columns: [
              // Read-only so a select-all, paste or fill can never rewrite a row's key.
              { type: 'hidden', title: '#', readOnly: true },
              ...columns.map((c) => ({
                type: 'text' as const,
                align: 'left' as const,
                title: c.label ?? c.name,
                width: (c.width ?? 8) * 16,
                readOnly: !!c.locked,
                tooltip: c.locked ?? c.hint
              }))
            ],
            // The lock is flipped in place (below), so the grid and its undo history survive it.
            editable: !readOnly,
            // Scrolling is the wrapper's, not jspreadsheet's: with its own overflow it
            // closes (and commits) the cell editor on every scroll, so typing near the
            // visible edge, which scrolls to the caret, committed after one character.
            tableOverflow: false,
            // Every row is in the DOM, so a problem can always be scrolled to.
            lazyLoading: false,
            filters: true,
            // When the parent owns search (externalSearch), don't render jspreadsheet's
            // own box at all: with `search` off its whole toolbar bar is never attached,
            // yet the `search()` method (driven from the parent) still filters, because
            // that path depends on `filters`, not this flag.
            search: !externalSearch,
            columnSorting: true,
            columnDrag: false,
            autoIncrement: false,
            allowInsertRow: false,
            allowManualInsertRow: false,
            allowInsertColumn: false,
            allowManualInsertColumn: false,
            allowDeleteRow: false,
            allowDeleteColumn: false,
            allowRenameColumn: false,
            wordWrap: false
          }
        ],
        onafterchanges: (_ws: unknown, changes: { x: string; y: string }[]) => report(changes),
        // One value pasted onto a selected range fills the range, as a spreadsheet does;
        // jspreadsheet alone would write only its first cell.
        onbeforepaste: (_ws: unknown, copied: { value: unknown }[][]) => {
          const sel = ws?.getSelection();
          if (!sel || copied.length !== 1 || copied[0].length !== 1) return undefined;
          const [x1, y1, x2, y2] = sel;
          const w = Math.abs(x2 - x1) + 1;
          const h = Math.abs(y2 - y1) + 1;
          if (w * h === 1) return undefined;
          const v = String(copied[0][0].value ?? '');
          return Array.from({ length: h }, () => Array(w).fill(v).join('\t')).join('\n');
        },
        onundo: (_ws: unknown, record: { action: string; records?: { x: number; y: number }[] }) => {
          if (record?.action === 'setValue' && record.records) report(record.records);
        },
        onredo: (_ws: unknown, record: { action: string; records?: { x: number; y: number }[] }) => {
          if (record?.action === 'setValue' && record.records) report(record.records);
        },
        // A sort moves rows, so the issue marks have to follow them.
        onsort: () => paintIssues(issues)
      } as never) as unknown as Worksheet[];
      ws = sheets[0];
      if (import.meta.env.DEV) (window as unknown as { __sheet: unknown }).__sheet = ws; // debugging aid
      destroy = () => {
        // jspreadsheet's keyboard handlers are global and act on `jspreadsheet.current`;
        // left pointing at a destroyed sheet, the next key press throws inside them.
        const w = ws;
        if (w?.edition) w.closeEditor(w.edition[0], false);
        const current = jspreadsheet as unknown as { current: unknown };
        if (current.current === w) current.current = null;
        jspreadsheet.destroy(el as never, true);
        ws = null;
        painted = new Set();
      };
      paintIssues(issues);
    }
  }

  onMount(() => {
    let cancelled = false;
    (async () => {
      const jspreadsheet = await loadLib();
      if (cancelled) return;
      lib = jspreadsheet;
      // Redo on Cmd/Ctrl+Shift+Z as well as Ctrl+Y. jspreadsheet's own key handler reads
      // any Ctrl+Z as undo, so this runs first (capture) and stops it.
      const current = jspreadsheet as unknown as { current: unknown };
      onRedoKey = (e: KeyboardEvent) => {
        if (!ws || current.current !== ws || !(e.metaKey || e.ctrlKey) || !e.shiftKey || e.key.toLowerCase() !== 'z') return;
        e.preventDefault();
        e.stopImmediatePropagation();
        ws.redo();
      };
      window.addEventListener('keydown', onRedoKey, true);
      build();
    })();
    return () => {
      cancelled = true;
    };
  });
  onDestroy(() => {
    if (onRedoKey) window.removeEventListener('keydown', onRedoKey, true);
    destroy?.();
  });

  // Lock and unlock without rebuilding: jspreadsheet checks `editable` on every edit.
  $effect(() => {
    const editable = !readOnly;
    if (ws?.edition && !editable) ws.closeEditor(ws.edition[0], false);
    if (ws) ws.options.editable = editable;
  });

  // parent -> sheet: write back only the cells that differ from the parent's rows.
  $effect(() => {
    const want = matrix(rows, columns);
    if (!ws) return;
    if (columnsKey(columns) !== builtFor) {
      destroy?.();
      build();
      return;
    }
    const have = ws.getData();
    if (have.length !== want.length) {
      quietly((w) => {
        if (w.edition) w.closeEditor(w.edition[0], false);
        w.setData(want);
      });
      paintIssues(issues);
      return;
    }
    quietly((w) => {
      // Never write under an open editor: its cell is the user's until they leave it.
      const ex = w.edition ? Number(w.edition[2]) : -1;
      const ey = w.edition ? Number(w.edition[3]) : -1;
      have.forEach((cells, y) => {
        const target = want[Number(cells[KEY])];
        if (!target) return;
        for (let x = 1; x < target.length; x++)
          if (String(cells[x] ?? '') !== target[x] && !(x === ex && y === ey)) w.setValueFromCoords(x, y, target[x], true);
      });
    });
  });

  // Issue marks: a red cell with the reason as its comment, placed by display row.
  let painted = new Set<string>();
  function paintIssues(current: Map<string, string>) {
    if (!ws) return;
    const at = new Map<number, number>();
    ws.getData().forEach((cells, y) => at.set(Number(cells[KEY]), y));
    const colIdx = new Map(columns.map((c, i) => [c.name, i + 1]));
    const styles: Record<string, string> = {};
    const comments: Record<string, string> = {};
    for (const [key, message] of current) {
      const sep = key.indexOf(':');
      const y = at.get(Number(key.slice(0, sep)));
      const x = colIdx.get(key.slice(sep + 1));
      if (y === undefined || x === undefined) continue;
      const name = cellName(x, y);
      styles[name] = 'background-color: #fef2f2;'; // a tint; the comment marker and the list below carry the detail
      comments[name] = message;
    }
    quietly((w) => {
      const cleared = [...painted].filter((n) => !(n in styles));
      if (cleared.length) {
        w.resetStyle(Object.fromEntries(cleared.map((n) => [n, ''])), true);
        w.setComments(Object.fromEntries(cleared.map((n) => [n, ''])));
      }
      if (Object.keys(styles).length) {
        w.setStyle(styles, null, null, true);
        w.setComments(comments);
      }
    });
    painted = new Set(Object.keys(styles));
  }
  $effect(() => {
    const current = issues;
    void rows; // a row edit can move an issue without changing the map's identity
    paintIssues(current);
  });

  // Tint `scope: 'local'` columns a shade darker than global ones, so a study's own
  // (unpublished) fields read apart from the shared global ones. Local columns are
  // contiguous at the right (global-first), so one nth-child range covers them. A
  // generated rule (scoped to this grid's uid) is the only way to target a column
  // index jspreadsheet renders; issue paint is inline, so it still wins over this.
  let tintEl: HTMLStyleElement | undefined;
  $effect(() => {
    const firstLocal = columns.findIndex((c) => c.scope === 'local');
    const k = 3 + firstLocal; // gutter(1) + hidden key(1) + columns[0..]
    const css =
      firstLocal < 0
        ? ''
        : `.qiita-sheet.${uid} .jss_worksheet > tbody > tr > td:nth-child(n+${k}){background-color:#f9fafb;}` +
          `.qiita-sheet.${uid} .jss_worksheet > thead > tr > td:nth-child(n+${k}){background-color:#eceff2;}`;
    if (!tintEl) {
      tintEl = document.createElement('style');
      document.head.appendChild(tintEl);
    }
    tintEl.textContent = css;
  });
  onDestroy(() => tintEl?.remove());

  // Pin: the identity column's left offset is the gutter's rendered width, which depends
  // on the row count's digits — measure it after each (re)build and feed the CSS.
  $effect(() => {
    void columns;
    void rows;
    if (!pinFirst) return;
    requestAnimationFrame(() => {
      const g = el?.querySelector('.jss_worksheet > tbody > tr > td:first-child') as HTMLElement | null;
      if (g) el.style.setProperty('--gutter-w', `${g.offsetWidth}px`);
    });
  });

</script>

<div class="flex min-w-0 flex-col gap-2">
  <p class="text-xs text-gray-600">{rows.length} {rows.length === 1 ? 'row' : 'rows'} · {columns.length} columns</p>
  <div class="qiita-sheet-scroll min-w-0 overflow-auto rounded-md ring-1 ring-gray-200" style:max-height={height}>
    <div bind:this={el} class="qiita-sheet {uid} min-w-0 text-xs" class:locked={readOnly} class:pin-first={pinFirst}></div>
  </div>
</div>

<style>
  /* jspreadsheet, themed to read like DataTable: grey-50 headers in small uppercase,
     light dividers, sans cells with the first column in mono, teal for selection.
     Colours are Tailwind's gray / teal / red scales. */
  .qiita-sheet :global(.jss_worksheet) {
    border: 0;
    font-family: inherit;
    font-size: 0.8125rem;
    color: rgb(55 65 81); /* gray-700 */
  }

  /* header row */
  .qiita-sheet :global(.jss_worksheet > thead > tr > td) {
    background-color: rgb(249 250 251); /* gray-50 */
    border: 0;
    border-right: 1px solid rgb(229 231 235);
    border-bottom: 1px solid rgb(229 231 235);
    padding: 0.5rem 0.75rem;
    text-align: left;
    /* DataTable's header, but in mono and as written: these are the file's own column
       names, so they are shown exactly as the TSV header will carry them. */
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.75rem;
    font-weight: 600;
    color: rgb(75 85 99); /* gray-600 */
  }
  .qiita-sheet :global(.jss_worksheet > thead > tr > td.selected) {
    background-color: rgb(240 253 250); /* teal-50 */
    color: rgb(15 118 110); /* teal-700 */
  }
  /* Both header rows stick: the names at the top, the filters just under them. */
  .qiita-sheet :global(.jss_worksheet > thead > tr:first-child > td) {
    height: 2.125rem;
    top: 0;
  }
  .qiita-sheet :global(.jss_worksheet > thead > tr:nth-child(2) > td) {
    background-color: #fff;
    padding: 0.125rem 0.5rem;
    top: 2.125rem;
  }

  /* body cells */
  .qiita-sheet :global(.jss_worksheet > tbody > tr > td) {
    border: 0;
    border-right: 1px solid rgb(243 244 246); /* gray-100 */
    border-bottom: 1px solid rgb(243 244 246);
    padding: 0.25rem 0.75rem;
    line-height: 1.25rem;
  }
  .qiita-sheet :global(.jss_worksheet > tbody > tr:hover > td:not(:first-child)) {
    background-color: rgb(249 250 251);
  }
  /* row numbers */
  .qiita-sheet :global(.jss_worksheet > tbody > tr > td:first-child) {
    background-color: rgb(249 250 251);
    border-right: 1px solid rgb(229 231 235);
    color: rgb(156 163 175); /* gray-400 */
    font-size: 0.75rem;
    text-align: right;
  }
  .qiita-sheet :global(.jss_worksheet > tbody > tr.selected > td:first-child) {
    background-color: rgb(240 253 250);
    color: rgb(15 118 110);
  }
  /* the first visible data column (after row numbers and the hidden key): IDs, in mono */
  .qiita-sheet :global(.jss_worksheet > tbody > tr > td:nth-child(3)) {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.75rem;
    color: rgb(31 41 55); /* gray-800 */
  }
  /* locked columns read as set elsewhere — unless the whole sheet is read-only, when
     greying every cell would only make it hard to read */
  .qiita-sheet:not(.locked) :global(.jss_worksheet > tbody > tr > td.readonly) {
    color: rgb(156 163 175);
  }

  /* selection: teal, not jspreadsheet's black */
  .qiita-sheet :global(.jss_worksheet .highlight) {
    background-color: rgb(240 253 250 / 0.8);
  }
  .qiita-sheet :global(.jss_worksheet .highlight-selected) {
    background-color: transparent;
  }
  .qiita-sheet :global(.jss_worksheet .highlight-top) {
    border-top: 1px solid rgb(13 148 136);
    box-shadow: none;
  }
  .qiita-sheet :global(.jss_worksheet .highlight-bottom) {
    border-bottom: 1px solid rgb(13 148 136);
  }
  .qiita-sheet :global(.jss_worksheet .highlight-left) {
    border-left: 1px solid rgb(13 148 136);
    box-shadow: none;
  }
  .qiita-sheet :global(.jss_worksheet .highlight-right) {
    border-right: 1px solid rgb(13 148 136);
  }
  .qiita-sheet :global(.jss_worksheet .selection-top) {
    border-top: 1px dashed rgb(13 148 136);
  }
  .qiita-sheet :global(.jss_worksheet .selection-bottom) {
    border-bottom: 1px dashed rgb(13 148 136);
  }
  .qiita-sheet :global(.jss_worksheet .selection-left) {
    border-left: 1px dashed rgb(13 148 136);
  }
  .qiita-sheet :global(.jss_worksheet .selection-right) {
    border-right: 1px dashed rgb(13 148 136);
  }
  /* the fill handle */
  .qiita-sheet :global(.jss_corner) {
    background-color: rgb(13 148 136);
    border-color: #fff;
    width: 5px;
    height: 5px;
  }

  /* search box, as DataTable's (shown only when this Sheet owns its own search —
     externalSearch suppresses it at the config level, so no hiding rule is needed). */
  .qiita-sheet :global(.jss_search) {
    width: 14rem;
    margin-left: 0.5rem;
    border-radius: 0.375rem;
    border: 1px solid rgb(209 213 219);
    padding: 0.25rem 0.625rem;
    font-size: 0.875rem;
  }

  /* Pin the row gutter and the first data column (td:nth-child(3): after the gutter and
     the width-0 hidden key column) to the left on horizontal scroll. --gutter-w is the
     measured gutter width; the identity column sits just right of it. Sticky resolves
     against the .qiita-sheet-scroll wrapper (tableOverflow is off, so jspreadsheet's own
     content doesn't scroll).

     z-index: jspreadsheet already makes header cells sticky-top at z-index 2, so a pinned
     HEADER cell must sit ABOVE that (4/5) or the scrolling header cells paint over it;
     body cells have no z-index, so the pinned ones just need to be above 0. */
  .qiita-sheet.pin-first :global(.jss_worksheet > tbody > tr > td:first-child) {
    position: sticky;
    left: 0;
    z-index: 3;
  }
  .qiita-sheet.pin-first :global(.jss_worksheet > tbody > tr > td:nth-child(3)) {
    position: sticky;
    left: var(--gutter-w, 2.25rem);
    z-index: 2;
    background-color: #fff; /* opaque, so scrolled cells don't show through */
  }
  .qiita-sheet.pin-first :global(.jss_worksheet > thead > tr > td:first-child) {
    position: sticky;
    left: 0;
    z-index: 5;
  }
  .qiita-sheet.pin-first :global(.jss_worksheet > thead > tr > td:nth-child(3)) {
    position: sticky;
    left: var(--gutter-w, 2.25rem);
    z-index: 4;
  }
</style>
