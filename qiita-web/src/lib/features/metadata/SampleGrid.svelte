<script lang="ts">
  // The study's sample metadata, shown through the shared Sheet (jspreadsheet) grid —
  // the same component the library-prep flow edits its pre-prep files in, here READ-ONLY.
  // Sort and filter come from Sheet; search is in this component's toolbar, above the grid
  // (jspreadsheet's own in-grid box is hidden). This component owns the Card shell, the
  // global/local column toggle, the geo Map, and metameq validation — which runs
  // automatically when the samples load, and can be re-run from the toolbar to pick
  // host/sample types. Editing (write-back via the metadata upsert API) is deliberately
  // not wired yet — this is the viewer.
  import type { Biosample } from '$lib/api';
  import { humanize } from '$lib/utils';
  import { settings } from '$lib/settings.svelte';
  import {
    cellText,
    geoPoint,
    sampleIdentity,
    availableIdentities,
    IDENTITY_LABEL,
    type Column,
    type GeoPoint,
    type IdentitySource
  } from '$lib/features/metadata/samples';
  import type { Problem, Row } from '$lib/table/model';
  import Card from '$lib/ui/Card.svelte';
  import GeoMap from '$lib/ui/GeoMap.svelte';
  import Modal from '$lib/ui/Modal.svelte';
  import Select from '$lib/ui/Select.svelte';
  import ProblemList from '$lib/ui/ProblemList.svelte';
  import Sheet, { type GridColumn } from '$lib/ui/Sheet.svelte';
  import { metameq } from '$lib/pyodide/pyodide.svelte';
  import type { MetameqValidationResult, TypeResolution } from '$lib/pyodide/types';
  import { tick, untrack } from 'svelte';
  import type { Review } from './review';
  import { metameqInput, placeReview, type Placed } from './validate';
  import {
    collectRepairTargets,
    proposeRepairs,
    mergeRepairs,
    proposedFixes,
    type ProposedFix
  } from './repair';
  import TypeResolutionModal from './TypeResolutionModal.svelte';
  import { validateWithResolution, contextSummary, type ResolutionPrompt } from './typeResolution';

  // `total` and `loading` let the grid render while samples stream in. `bare`
  // drops the Card wrapper when the grid is already inside one (e.g. a tab).
  let {
    samples,
    columns,
    total = samples.length,
    loading = false,
    bare = false
  }: {
    samples: Biosample[];
    columns: Column[];
    total?: number;
    loading?: boolean;
    bare?: boolean;
  } = $props();

  // The pinned first column: which identity handle names each row. Only handles the
  // data carries are offered; reset if the current choice stops resolving.
  const identities = $derived(availableIdentities(samples));
  let identity = $state<IdentitySource>('biosample_accession');
  $effect(() => {
    if (identities.length && !identities.includes(identity)) identity = identities[0];
  });

  // Global fields only, or also the study's own (local) fields. The identity column is
  // first (left); global fields come before local — `columns` is already global-first.
  let showLocal = $state(true);
  const visibleColumns = $derived(showLocal ? columns : columns.filter((c) => c.scope === 'global'));
  const allCols = $derived<Column[]>([{ key: 'accession', label: 'Accession', scope: 'global' }, ...visibleColumns]);

  // The Sheet's view of the same data: the chosen identity first, then each field as a
  // plain text column; one row per biosample keyed by column name.
  const gridColumns = $derived<GridColumn[]>(
    allCols.map((c) => ({
      name: c.key,
      label: c.key === 'accession' ? IDENTITY_LABEL[identity] : settings.humanizeLabels ? humanize(c.label) : c.label,
      locked: 'read-only',
      scope: c.scope
    }))
  );
  const gridRows = $derived<Row[]>(
    samples.map((s) => {
      const cells: Row = { accession: sampleIdentity(s, identity) };
      for (const c of columns) cells[c.key] = cellText(s, c.key);
      return cells;
    })
  );

  let mapOpen = $state(false);
  // Every located sample's point — the Map shows the study's geography.
  const points = $derived<GeoPoint[]>(samples.flatMap((s) => geoPoint(s) ?? []));

  // metameq validation of the loaded metadata (advisory, read-only): runs the same
  // engine the upload flow uses, paints findings on the grid, and lists them below.
  // Every field is validated, shown or not; picking a finding on a hidden local field
  // shows the local fields first.
  type Outcome = Placed & {
    errors: string[];
    reviewed: boolean;
    summary?: MetameqValidationResult['summary'];
    context?: string | null;
    /** Proposed fixes — metameq's confident ones and (if enabled) the LLM's guesses. */
    fixes?: ProposedFix[];
    /** The user dismissed the host/sample-type prompt, so nothing was checked. */
    cancelled?: boolean;
  };
  let validating = $state(false);
  let outcome = $state<Outcome | null>(null);

  // The host/sample-type prompt (TypeResolutionModal), shown only when metameq can't
  // tell the types. `typeResolver` is the promise the validate loop awaits.
  let typeModalOpen = $state(false);
  let typePrompt = $state<ResolutionPrompt | null>(null);
  let typeResolver: ((r: TypeResolution | null) => void) | null = null;
  function promptForResolution(p: ResolutionPrompt): Promise<TypeResolution | null> {
    typePrompt = p;
    typeModalOpen = true;
    return new Promise((resolve) => (typeResolver = resolve));
  }
  function onTypeResolve(r: TypeResolution) {
    typeResolver?.(r);
    typeResolver = null;
  }
  function onTypeCancel() {
    typeResolver?.(null);
    typeResolver = null;
  }
  // The modal can also close via Esc, its ✕, or the backdrop, which don't fire the
  // callbacks; treat any close with a promise still pending as a cancel so the
  // validate loop never hangs.
  $effect(() => {
    if (!typeModalOpen && typeResolver) onTypeCancel();
  });
  let sheet = $state<{ focusCell: (row: number, column: string) => boolean; search: (q: string) => void } | undefined>();

  // Search lives in this toolbar, not in the grid: the Sheet hides jspreadsheet's own box
  // (externalSearch) and we drive its filter. Filtering stays inside the sheet, so a
  // validation overlay keeps lining up.
  let query = $state('');
  // A column change (local toggle) rebuilds the grid and drops the filter; re-apply it.
  $effect(() => {
    void gridColumns;
    const q = untrack(() => query);
    if (q) tick().then(() => sheet?.search(q));
  });
  // A new/growing sample set makes a prior validation stale (row indices shift), so drop it.
  $effect(() => {
    void samples;
    outcome = null;
  });
  // Validate automatically once the study's samples have loaded, so the person sees
  // findings and fix candidates without clicking. Keyed on the sample set's identity so
  // it runs once per study (and again if the set is replaced), never on every effect pass.
  // If metameq can't infer the host/sample type, this opens the type modal on load — the
  // metadata genuinely can't be checked without it, so prompting beats a dead-end note.
  let autoValidatedFor: Biosample[] | null = null;
  $effect(() => {
    if (loading || validating || !samples.length || autoValidatedFor === samples) return;
    autoValidatedFor = samples;
    runValidate();
  });
  async function runValidate() {
    if (!samples.length || validating) return;
    validating = true;
    try {
      const input = metameqInput(samples, columns);
      const file = new File([input.tsv], 'metadata.tsv', { type: 'text/tab-separated-values' });
      const result = (await validateWithResolution(
        (resolution) => metameq.validate(file, resolution),
        promptForResolution
      )) as MetameqValidationResult & { review?: Review };
      if (result.needsTypeResolution) {
        // The user dismissed the prompt without choosing: nothing was checked.
        outcome = { issues: new Map(), problems: [], errors: [], reviewed: false, cancelled: true };
        return;
      }
      const review = result?.review;
      const errors = (review?.errors ?? result?.errors ?? []).map((e) => e.message);
      if (review && settings.llm.enabled) {
        // Ask the user's endpoint to fill the gaps metameq's deterministic rules left,
        // and fold its guesses into the same issue suggestions (advisory — a failure here
        // just leaves metameq's own findings untouched).
        try {
          const targets = collectRepairTargets(review, result.taxonomy);
          if (targets.length) mergeRepairs(review, await proposeRepairs(targets, settings.llm));
        } catch {
          /* normalizer is advisory; ignore */
        }
      }
      outcome = review
        ? {
            ...placeReview(review, input, 'accession'),
            errors,
            reviewed: true,
            summary: result.summary,
            context: contextSummary(result.resolvedContext),
            fixes: proposedFixes(review)
          }
        : { issues: new Map(), problems: [], errors, reviewed: false };
    } catch (e) {
      outcome = { issues: new Map(), problems: [], errors: [e instanceof Error ? e.message : String(e)], reviewed: false };
    } finally {
      validating = false;
    }
  }
  async function pick(p: Problem) {
    if (!p.at) return;
    if (!gridColumns.some((c) => c.name === p.at!.column)) showLocal = true;
    // A grid given new columns rebuilds; retry until it can take the focus.
    for (let attempt = 0; attempt < 40; attempt++) {
      await tick();
      if (sheet?.focusCell(p.at.row, p.at.column)) return;
      await new Promise((r) => setTimeout(r, 50));
    }
  }
</script>

{#snippet toolbar()}
  <!-- Wraps onto further lines on a narrow screen rather than overflowing. -->
  <div class="flex flex-wrap items-center gap-x-3 gap-y-2">
    <!-- Which identity names the (first, left) column. -->
    <label class="inline-flex items-center gap-1.5 text-sm text-gray-700">
      <span class="text-gray-500">ID</span>
      <Select bind:value={identity} class="w-44">
        {#each identities as src (src)}<option value={src}>{IDENTITY_LABEL[src]}</option>{/each}
      </Select>
    </label>
    <label class="inline-flex cursor-pointer items-center gap-2 text-sm text-gray-700 select-none">
      <button
        type="button"
        role="switch"
        aria-checked={showLocal}
        aria-label="Show local metadata"
        onclick={() => (showLocal = !showLocal)}
        class="relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors {showLocal
          ? 'bg-teal-600'
          : 'bg-gray-200'}"
      >
        <span
          class="absolute top-0.5 left-0.5 size-4 rounded-full bg-white shadow ring-1 ring-gray-900/5 transition-transform {showLocal
            ? 'translate-x-4'
            : ''}"
        ></span>
      </button>
      Show local metadata
    </label>
    <button
      class="shrink-0 rounded-md bg-white px-3 py-1 text-sm font-medium text-teal-700 ring-1 ring-teal-600/30 ring-inset hover:bg-teal-50 disabled:opacity-40"
      onclick={() => (mapOpen = true)}
      disabled={!points.length}>Map ({points.length})</button
    >
    <!-- Run metameq over the loaded metadata (first run loads the checker). -->
    <button
      class="shrink-0 rounded-md bg-white px-3 py-1 text-sm font-medium text-teal-700 ring-1 ring-teal-600/30 ring-inset hover:bg-teal-50 disabled:opacity-40"
      onclick={() => runValidate()}
      disabled={loading || validating || !samples.length}
      title={metameq.has('validation') ? 'Validate this metadata against the metameq standards' : 'Validate metadata — the first run loads the checker'}
    >
      {validating ? (metameq.has('validation') ? 'Validating…' : `Loading checker ${metameq.loadProgress}%…`) : 'Validate'}
    </button>
    {#if outcome && !validating}
      {@const n = outcome.problems.length}
      <span class="shrink-0 text-sm {n ? 'text-amber-700' : 'text-green-700'}">
        {n ? `${n} ${n === 1 ? 'issue' : 'issues'}` : 'all valid'}
      </span>
    {/if}
    <input
      type="search"
      placeholder="Search…"
      aria-label="Search samples"
      bind:value={query}
      oninput={() => sheet?.search(query)}
      class="ml-auto w-44 shrink-0 rounded-md border border-gray-300 px-2.5 py-1 text-sm focus:border-teal-500 focus:ring-1 focus:ring-teal-500 focus:outline-none"
    />
  </div>
{/snippet}

{#snippet grid()}
  <div class="flex flex-col gap-2 px-4 py-3 {bare ? 'border-t border-gray-200' : ''}">
    {#if loading}
      <p class="text-xs text-gray-500">Loading {samples.length} of {total}…</p>
    {/if}
    {#if outcome?.errors.length}
      <p class="rounded-md bg-red-50 px-3 py-2 text-xs text-red-800">
        Couldn't validate: {outcome.errors.join('; ')}
      </p>
    {/if}
    {#if outcome?.cancelled}
      <p class="rounded-md bg-gray-50 px-3 py-2 text-xs text-gray-600">
        Not checked — metameq needs a host and sample type for each sample. Validate again to choose them.
      </p>
    {/if}
    {#if outcome?.reviewed && outcome.summary}
      {@const s = outcome.summary}
      <div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-600">
        <span class="font-medium {s.errorCount ? 'text-amber-700' : 'text-green-700'}">
          {s.errorCount ? `${s.validRows} of ${s.totalRows} samples valid` : `All ${s.totalRows} samples valid`}
        </span>
        {#if outcome.context}
          <span class="text-gray-400">·</span>
          <span>validated as {outcome.context}</span>
        {/if}
      </div>
    {/if}
    <Sheet bind:this={sheet} readOnly externalSearch pinFirst columns={gridColumns} rows={gridRows} issues={outcome?.issues ?? new Map()} height="60vh" />
    {#if outcome?.reviewed}
      <ProblemList problems={outcome.problems} onpick={pick} />
    {/if}
    {#if outcome?.fixes?.length}
      <!-- Proposed fixes, read-only for now: metameq's confident ones and (if enabled)
           the LLM's guesses. Applying them is the next step; this is the candidate view. -->
      <div class="flex flex-col gap-1 rounded-md border border-gray-200 px-3 py-2">
        <p class="text-xs font-medium text-gray-700">Proposed fixes ({outcome.fixes.length})</p>
        <ul class="flex flex-col gap-0.5 text-xs">
          {#each outcome.fixes as f (f.field + f.value + f.suggestion)}
            <li class="flex flex-wrap items-center gap-x-1.5 text-gray-600">
              <span class="font-mono text-gray-400">{f.field}</span>
              <span class="text-gray-400 line-through">{f.value}</span>
              <span class="text-gray-400">→</span>
              <span class="font-medium {f.deterministic ? 'text-green-700' : 'text-violet-700'}">{f.suggestion}</span>
              <span class="rounded px-1 text-[10px] {f.deterministic ? 'bg-green-50 text-green-700' : 'bg-violet-50 text-violet-700'}">
                {f.deterministic ? 'auto' : 'LLM'}
              </span>
              {#if f.samples > 1}<span class="text-gray-400">×{f.samples}</span>{/if}
            </li>
          {/each}
        </ul>
      </div>
    {/if}
  </div>
{/snippet}

{#if bare}
  <div class="flex justify-end px-4 py-3 sm:px-6">{@render toolbar()}</div>
  {@render grid()}
{:else}
  <Card title="Sample metadata" bodyClass="">
    {#snippet actions()}{@render toolbar()}{/snippet}
    {@render grid()}
  </Card>
{/if}

<Modal
  bind:open={mapOpen}
  title="Geographic distribution — {points.length} samples"
>
  <GeoMap {points} />
</Modal>

{#if typePrompt}
  <TypeResolutionModal
    bind:open={typeModalOpen}
    taxonomy={typePrompt.taxonomy}
    columns={typePrompt.columns}
    context={typePrompt.context}
    onresolve={onTypeResolve}
    oncancel={onTypeCancel}
  />
{/if}
