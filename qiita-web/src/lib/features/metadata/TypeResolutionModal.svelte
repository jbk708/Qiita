<script lang="ts">
  // metameq validates against host-type- and sample-type-specific rules, so every
  // row needs a host type and a sample type. When neither a known column nor a value
  // sniff found them (result.needsTypeResolution), this asks: for each axis, read an
  // existing column, or apply one type to every row. Its answer is a TypeResolution
  // the caller passes back to `metameq.validate`.
  import type { TypeResolution, TypeTaxonomy, ResolvedAxisContext } from '$lib/pyodide/types';
  import Modal from '$lib/ui/Modal.svelte';
  import Select from '$lib/ui/Select.svelte';

  let {
    open = $bindable(false),
    taxonomy,
    columns,
    context = null,
    onresolve,
    oncancel
  }: {
    open?: boolean;
    taxonomy: TypeTaxonomy;
    /** The metadata's own columns, for the "read a column" option. */
    columns: string[];
    /** What inference already found, to pre-fill a detected column. */
    context?: { host: ResolvedAxisContext | null; sample: ResolvedAxisContext | null } | null;
    onresolve: (resolution: TypeResolution) => void;
    oncancel?: () => void;
  } = $props();

  type Mode = 'column' | 'value';
  type Axis = { mode: Mode; column: string; value: string };

  // Pre-fill from inference: a detected column starts in column mode; an unresolved
  // axis starts in value mode (nothing sensible to read yet).
  function seed(ctx: ResolvedAxisContext | null | undefined): Axis {
    if (ctx && (ctx.mode === 'inferred' || ctx.mode === 'column' || ctx.mode === 'present') && ctx.column) {
      return { mode: 'column', column: ctx.column, value: '' };
    }
    return { mode: columns.length ? 'column' : 'value', column: columns[0] ?? '', value: '' };
  }

  let host = $state<Axis>({ mode: 'value', column: '', value: '' });
  let sample = $state<Axis>({ mode: 'value', column: '', value: '' });
  // Seed from inference each time the modal opens (reading the props here, inside the
  // effect, keeps them reactive and re-seeds a reused instance on the next prompt).
  let seeded = false;
  $effect(() => {
    if (open && !seeded) {
      host = seed(context?.host);
      sample = seed(context?.sample);
      seeded = true;
    } else if (!open) {
      seeded = false;
    }
  });

  const allSampleTypes = $derived(Object.keys(taxonomy.hostOfSampleType).sort());
  // When one host value is chosen, the sample value list narrows to that host's
  // types; otherwise (a column, or no host value yet) offer every sample type.
  const sampleOptions = $derived(
    host.mode === 'value' && host.value ? (taxonomy.sampleTypesByHost[host.value] ?? []) : allSampleTypes
  );

  const axisReady = (a: Axis) => (a.mode === 'column' ? !!a.column : !!a.value);
  const ready = $derived(axisReady(host) && axisReady(sample));

  function pick(a: Axis) {
    return a.mode === 'column' ? { column: a.column } : { value: a.value };
  }

  function apply() {
    if (!ready) return;
    onresolve({ host: pick(host), sample: pick(sample) });
    open = false;
  }

  function cancel() {
    oncancel?.();
    open = false;
  }
</script>

<Modal bind:open title="What are these samples?">
  <div class="space-y-5 text-sm">
    <p class="text-gray-600">
      metameq checks each sample against rules for its <strong>host</strong> and
      <strong>sample type</strong>. This metadata doesn't name them in a way it recognized —
      point it at a column that holds each, or apply one value to every sample.
    </p>

    {#snippet axisField(label: string, a: Axis, valueOptions: string[])}
      <div class="rounded-md border border-gray-200 p-3">
        <div class="mb-2 flex items-center justify-between">
          <span class="font-medium text-gray-900">{label}</span>
          <div class="inline-flex overflow-hidden rounded-md border border-gray-300 text-xs">
            <button
              type="button"
              class="px-2 py-1 {a.mode === 'column' ? 'bg-teal-600 text-white' : 'bg-white text-gray-600'}"
              disabled={!columns.length}
              onclick={() => (a.mode = 'column')}>A column</button
            >
            <button
              type="button"
              class="px-2 py-1 {a.mode === 'value' ? 'bg-teal-600 text-white' : 'bg-white text-gray-600'}"
              onclick={() => (a.mode = 'value')}>One value for all</button
            >
          </div>
        </div>

        {#if a.mode === 'column'}
          {#if columns.length}
            <Select bind:value={a.column} class="w-full">
              {#each columns as col (col)}
                <option value={col}>{col}</option>
              {/each}
            </Select>
          {:else}
            <p class="text-xs text-gray-500">This metadata has no columns to read from.</p>
          {/if}
        {:else}
          <Select bind:value={a.value} class="w-full">
            <option value="" disabled>Select a {label.toLowerCase()}…</option>
            {#each valueOptions as opt (opt)}
              <option value={opt}>{opt}</option>
            {/each}
          </Select>
          {#if valueOptions.length === 0}
            <p class="mt-1 text-xs text-gray-500">
              No specific sample types for this host — pick a host above first.
            </p>
          {/if}
        {/if}
      </div>
    {/snippet}

    {@render axisField('Host type', host, taxonomy.hostTypes)}
    {@render axisField('Sample type', sample, sampleOptions)}

    <div class="flex justify-end gap-2 border-t border-gray-100 pt-3">
      <button
        type="button"
        class="rounded-md border border-gray-300 px-3 py-1.5 text-gray-700 hover:bg-gray-50"
        onclick={cancel}>Cancel</button
      >
      <button
        type="button"
        class="rounded-md bg-teal-600 px-3 py-1.5 font-medium text-white hover:bg-teal-700 disabled:opacity-40"
        disabled={!ready}
        onclick={apply}>Validate</button
      >
    </div>
  </div>
</Modal>
