<script lang="ts">
  // Metadata-sheet → bulk biosample import, scoped to ONE study. Embedded on the
  // study detail page (the study is the context, so there's no study-picker here).
  import { api, type BiosampleRow, type BulkImportResult, type ApiResult } from '$lib/api';
  import Select from '$lib/ui/Select.svelte';
  import DropZone from '$lib/ui/DropZone.svelte';
  import { metameq } from '$lib/pyodide/pyodide.svelte';
  import type { MetameqValidationResult, TypeResolution } from '$lib/pyodide/types';
  import MetadataReview from '$lib/features/metadata/MetadataReview.svelte';
  import type { Review } from '$lib/features/metadata/review';
  import TypeResolutionModal from '$lib/features/metadata/TypeResolutionModal.svelte';
  import {
    validateWithResolution,
    contextSummary,
    type ResolutionPrompt
  } from '$lib/features/metadata/typeResolution';

  let { studyIdx, onUploaded }: { studyIdx: number; onUploaded?: () => void } = $props();

  // Owner defaults to the logged-in principal.
  let ownerIdx = $state<number | null>(null);
  $effect(() => {
    if (ownerIdx == null) api.whoami().then((r) => r.ok && (ownerIdx = r.data.principal_idx));
  });

  // --- sheet parsing (client-side) ---
  let fileName = $state('');
  let headers = $state<string[]>([]);
  let rows = $state<string[][]>([]);
  let parseMsg = $state('');
  let picker = $state<HTMLInputElement>();

  // Rows are identified by `sample_name`: the column metameq keys every row by.
  const ID_COLUMN = 'sample_name';
  const idCol = $derived(headers.indexOf(ID_COLUMN));
  // The tube column, if the sheet has one under a name labs use.
  const TUBE_COLUMN = /^(matrix_)?tube_?(id|code)$|^tubecode$/i;
  const tubeCol = $derived(headers.findIndex((h) => TUBE_COLUMN.test(h)));

  // The study field the owner's sample ids are stored in. The importer counts an
  // id as a repeat only within one field, so a sheet stored under a second field
  // would duplicate every biosample: use the study's own (a unique text field)
  // and offer a name only for a study that has none yet.
  const NEW_ID_FIELD = 'owner sample name';
  let idFields = $state<string[] | null>(null);
  let idFieldName = $state(NEW_ID_FIELD);
  $effect(() => {
    api.biosampleFields(studyIdx).then((r) => {
      if (!r.ok) return;
      idFields = r.data.filter((f) => f.unique_in_study && f.data_type === 'text').map((f) => f.display_name);
      if (idFields.length) idFieldName = idFields.includes(ID_COLUMN) ? ID_COLUMN : idFields[0];
    });
  });

  function parse(text: string) {
    const lines = text.replace(/\r\n?/g, '\n').split('\n').filter((l) => l.trim() !== '');
    if (!lines.length) {
      parseMsg = 'The file is empty.';
      return;
    }
    const delim = lines[0].includes('\t') ? '\t' : ','; // lab prep templates are TSV
    headers = lines[0].split(delim).map((h) => h.trim());
    rows = lines.slice(1).map((l) => {
      const cells = l.split(delim).map((c) => c.trim());
      while (cells.length < headers.length) cells.push(''); // rectangular, for editing
      return cells;
    });
    parseMsg = '';
  }

  async function handleFile(f: File | undefined) {
    if (!f) return;
    fileName = f.name;
    result = null;
    submitErr = '';
    valResult = null;
    valErr = '';
    parse(await f.text());
    // Every sheet is checked: the review is where it gets fixed.
    if (idCol >= 0) validate();
  }

  // --- metameq validation (lazy Pyodide), advisory: upload is never blocked ---
  let validating = $state(false);
  let valResult = $state<MetameqValidationResult | null>(null);
  let valErr = $state('');

  // Validate the CURRENT (possibly edited) grid, so edits are always what's
  // checked. Re-serialized as TSV — metameq reads tabular by extension.
  function gridToFile(): File {
    const line = (cells: string[]) => headers.map((_h, i) => cells[i] ?? '').join('\t');
    const tsv = [headers.join('\t'), ...rows.map(line)].join('\n');
    return new File([tsv], 'metadata.tsv', { type: 'text/tab-separated-values' });
  }

  // Host/sample-type prompt (shown only when metameq can't tell the types).
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
  // A close via Esc / ✕ / backdrop skips the callbacks; treat a pending close as cancel.
  $effect(() => {
    if (!typeModalOpen && typeResolver) onTypeCancel();
  });

  async function validate() {
    if (!rows.length) return;
    validating = true;
    valErr = '';
    try {
      const file = gridToFile();
      valResult = await validateWithResolution(
        (resolution) => metameq.validate(file, resolution),
        promptForResolution
      );
    } catch (e) {
      valErr = String(e);
    } finally {
      validating = false;
    }
  }

  const typeContext = $derived(contextSummary(valResult?.resolvedContext ?? null));

  const review = $derived<Review | null>(
    (valResult as (MetameqValidationResult & { review?: Review }) | null)?.review ?? null
  );


  // --- submit ---
  let submitting = $state(false);
  let result = $state<BulkImportResult | null>(null);
  let submitErr = $state('');

  const samples = $derived<BiosampleRow[]>(
    rows.map((cells) => {
      const metadata: Record<string, string> = {};
      headers.forEach((h, i) => {
        if (i === idCol || i === tubeCol) return;
        const v = cells[i] ?? '';
        if (v) metadata[h] = v;
      });
      return {
        owner_idx: ownerIdx ?? 0,
        owner_biosample_id_field_name: idFieldName.trim(),
        owner_biosample_id_value: cells[idCol] ?? '',
        metadata,
        matrix_tube_id: tubeCol >= 0 ? cells[tubeCol] || null : null
      };
    })
  );

  const ready = $derived(!!ownerIdx && idCol >= 0 && rows.length > 0 && !submitting);
  const validatingLabel = $derived(
    metameq.has('validation')
      ? 'Checking against the metadata standard…'
      : `Loading the metadata checker, ${metameq.loadProgress}% (first time only)…`
  );

  async function upload() {
    submitting = true;
    result = null;
    submitErr = '';
    const r: ApiResult<BulkImportResult> = await api.bulkImportBiosamples(studyIdx, samples);
    submitting = false;
    if (r.ok) {
      result = r.data;
      onUploaded?.();
      // Back to the drop zone; the success banner reports the result.
      headers = [];
      rows = [];
      fileName = '';
      valResult = null;
      valErr = '';
    } else {
      submitErr = `HTTP ${r.status} — ${r.detail}`;
    }
  }
</script>

<div class="flex h-full flex-col">
  {#if !headers.length}
    <div class="space-y-3 p-4">
      <DropZone
        accept=".tsv / .csv — one row per sample, with a sample_name column"
        hint="Drop a metadata sheet here, or click to browse"
        onfile={handleFile}
      />
      {#if parseMsg}<p class="text-sm text-red-700">{parseMsg}</p>{/if}
      {#if result}
        <p class="text-sm text-green-700">
          ✓ Created {result.results.length} biosamples ({result.results[0]?.export_entity_id}–{result.results[result.results.length - 1]?.export_entity_id}).
        </p>
      {/if}
    </div>
  {:else}
    <!-- The sheet, how its rows are identified, and the one action. -->
    <div class="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-gray-200 px-4 py-2.5 text-sm">
      <span class="font-medium text-gray-900">{fileName}</span>
      <span class="text-gray-400">{rows.length} rows × {headers.length} columns</span>
      <button class="text-teal-700 hover:underline" onclick={() => picker?.click()}>Replace…</button>
      <input
        bind:this={picker}
        type="file"
        accept=".tsv,.txt,.csv"
        class="hidden"
        onchange={(e) => {
          const el = e.currentTarget as HTMLInputElement;
          handleFile(el.files?.[0]);
          el.value = '';
        }}
      />
      <span class="text-gray-500">
        {#if idCol >= 0}
          Samples by <code class="text-gray-800">{ID_COLUMN}</code>, stored as
          {#if idFields && idFields.length > 1}
            <Select bind:value={idFieldName} class="inline-block w-44">
              {#each idFields as f}<option value={f}>{f}</option>{/each}
            </Select>
          {:else}
            <code class="text-gray-800" title={idFields?.length ? "The study's sample id field" : 'A new field: the study has no sample id field yet'}>{idFieldName}</code>{idFields?.length ? '' : ' (new)'}
          {/if}
          · tubes {#if tubeCol >= 0}by <code class="text-gray-800">{headers[tubeCol]}</code>{:else}<span class="text-gray-400">none in sheet</span>{/if}
        {/if}
      </span>
      <button
        class="ml-auto rounded-md bg-teal-700 px-4 py-1.5 font-semibold text-white shadow-sm hover:bg-teal-600 disabled:opacity-40"
        onclick={upload}
        disabled={!ready}>{submitting ? 'Uploading…' : `Upload ${rows.length} sample${rows.length === 1 ? '' : 's'}`}</button
      >
    </div>
    {#if idCol < 0}
      <p class="border-b border-red-100 bg-red-50 px-4 py-2 text-sm text-red-700">
        The sheet has no <code>{ID_COLUMN}</code> column, so its rows can't be told apart. Add one and load it again.
      </p>
    {/if}
    {#if submitErr}
      <div class="border-b border-red-100 bg-red-50 px-4 py-2 text-sm text-red-700">
        {submitErr}
        <span class="text-xs text-red-600/80">Nothing was created: fix the named row and upload again.</span>
      </div>
    {/if}
    {#if valErr}
      <p class="border-b border-red-100 bg-red-50 px-4 py-2 text-sm text-red-700">{valErr}</p>
    {:else if valResult?.needsTypeResolution}
      <p class="flex flex-wrap items-center gap-2 border-b border-amber-100 bg-amber-50 px-4 py-2 text-sm text-amber-800">
        Not checked — metameq needs a host and sample type for each sample.
        <button class="font-medium text-teal-700 hover:underline" onclick={validate}>Choose types…</button>
      </p>
    {:else if valResult && !valResult.processed}
      <p class="border-b border-amber-100 bg-amber-50 px-4 py-2 text-sm text-amber-800">
        The checker couldn't process the sheet{valResult.errors?.[0]?.message ? `: ${valResult.errors[0].message.split('\n')[0]}` : '.'}
      </p>
    {:else if valResult?.processed && typeContext}
      <p class="border-b border-gray-100 bg-gray-50 px-4 py-1.5 text-xs text-gray-600">
        Validated as {typeContext}.
      </p>
    {/if}
    <MetadataReview
      bind:headers
      bind:rows
      {review}
      extended={valResult?.extendedMetadata ?? null}
      idCol={Math.max(idCol, 0)}
      {tubeCol}
      {validating}
      {validatingLabel}
      onRevalidate={validate}
    />
  {/if}
</div>

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
