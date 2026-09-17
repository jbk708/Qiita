<script module lang="ts">
  import type { Biosample } from '$lib/api';
  const bsCache = new Map<number, Biosample>();
</script>

<script lang="ts">
  import { page } from '$app/state';
  import { auth } from '$lib/auth.svelte';
  import { api, type ApiResult } from '$lib/api';
  import { addRecent } from '$lib/recents';
  import PageHeading from '$lib/ui/PageHeading.svelte';
  import Card from '$lib/ui/Card.svelte';
  import GeoMap from '$lib/ui/GeoMap.svelte';
  import Modal from '$lib/ui/Modal.svelte';

  const CONC = 12;
  const PAGE = 50;

  const studyIdx = $derived(Number(page.params.study_idx));
  let loading = $state(false);
  let record = $state<ApiResult<Record<string, unknown>> | null>(null);
  let total = $state(0);
  let samples = $state<Biosample[]>([]);
  let sampleErr = $state('');
  let filter = $state('');
  let pageNo = $state(0);
  let mapOpen = $state(false);
  let loadSeq = 0;
  let loadedFor = -1;

  function num(v: unknown): number | null {
    if (typeof v === 'number') return v;
    if (typeof v === 'string' && v.trim() !== '' && Number.isFinite(Number(v))) return Number(v);
    return null;
  }

  // qiita_sample_type is the controlled vocab that's reliable across studies —
  // use only its terminology-term label; anything else is 'unspecified'.
  function sampleType(s: Biosample): string {
    const v = s.global_metadata?.qiita_sample_type?.value;
    if (v && typeof v === 'object' && (v as { kind?: string }).kind === 'terminology_term') {
      return String((v as { label?: string }).label ?? 'unspecified');
    }
    return 'unspecified';
  }

  const geoPoints = $derived(
    samples.flatMap((s) => {
      const lat = num(s.global_metadata?.geographic_location_latitude?.value);
      const lon = num(s.global_metadata?.geographic_location_longitude?.value);
      return lat != null && lon != null
        ? [{ lat, lon, type: sampleType(s), label: s.biosample_accession ?? String(s.biosample_idx) }]
        : [];
    })
  );

  const columns = $derived.by(() => {
    const seen = new Map<string, string>();
    for (const s of samples)
      for (const [k, f] of Object.entries(s.global_metadata ?? {}))
        if (!seen.has(k)) seen.set(k, f.display_name || k);
    return [...seen.entries()].map(([key, label]) => ({ key, label }));
  });

  function cell(s: Biosample, key: string): string {
    const f = s.global_metadata?.[key];
    if (!f || f.value == null) return '—';
    const v = f.value;
    if (typeof v === 'object') {
      const o = v as Record<string, unknown>;
      if (o.kind === 'missing_reason') return String(o.name ?? '(missing)');
      if (o.kind === 'terminology_term') return String(o.label ?? o.term_id ?? '(term)');
      return JSON.stringify(v);
    }
    return String(v);
  }

  const shown = $derived(
    filter.trim()
      ? samples.filter((s) =>
          `${s.biosample_accession} ${columns.map((c) => cell(s, c.key)).join(' ')}`
            .toLowerCase()
            .includes(filter.toLowerCase())
        )
      : samples
  );
  const pageCount = $derived(Math.max(1, Math.ceil(shown.length / PAGE)));
  const clampedPage = $derived(Math.min(pageNo, pageCount - 1));
  const paged = $derived(shown.slice(clampedPage * PAGE, clampedPage * PAGE + PAGE));
  $effect(() => {
    filter;
    pageNo = 0;
  });

  // Auto-load when the route idx (or auth) changes.
  $effect(() => {
    const i = studyIdx;
    if (auth.isSet && i && loadedFor !== i) {
      loadedFor = i;
      load(i);
    }
  });

  async function load(i: number) {
    const seq = ++loadSeq;
    loading = true;
    samples = [];
    sampleErr = '';
    pageNo = 0;
    record = await api.studyRecord(i);
    const list = await api.biosampleIdxs(i);
    if (seq !== loadSeq) return;
    if (!list.ok) {
      sampleErr = `samples: HTTP ${list.status} — ${list.detail}`;
      total = 0;
      loading = false;
      addRecent({ idx: i, accessible: false, biosampleCount: null, lastOpened: Date.now() });
      return;
    }
    const idxs = list.data.idxs;
    total = idxs.length;
    addRecent({ idx: i, accessible: true, biosampleCount: total, lastOpened: Date.now() });
    samples = idxs.map((x) => bsCache.get(x)).filter((b): b is Biosample => !!b);
    const missing = idxs.filter((x) => !bsCache.has(x));
    for (let k = 0; k < missing.length; k += CONC) {
      const rs = await Promise.all(missing.slice(k, k + CONC).map((x) => api.biosample(x)));
      if (seq !== loadSeq) return;
      const got = rs.flatMap((r) => (r.ok ? [r.data] : []));
      for (const b of got) bsCache.set(b.biosample_idx, b);
      samples = [...samples, ...got];
    }
    loading = false;
  }
</script>

<div class="mb-3">
  <a href="/studies" class="text-sm text-teal-700 hover:underline">← Studies</a>
</div>

<PageHeading title="Study {studyIdx}" subtitle="Samples and metadata." />

{#if !auth.isSet}
  <Card><p class="py-8 text-center text-gray-500">Log in to view a study.</p></Card>
{/if}

{#if record}
  <div class="mb-4">
    <Card title="Study record">
      {#if record.ok}
        <dl class="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
          {#each Object.entries(record.data) as [k, v]}
            <div class="flex gap-2 text-sm">
              <dt class="w-44 shrink-0 font-medium text-gray-500">{k}</dt>
              <dd class="text-gray-900">{v == null ? '—' : String(v)}</dd>
            </div>
          {/each}
        </dl>
      {:else if record.status === 403}
        <div class="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800 ring-1 ring-amber-600/20">
          Study record restricted at your tier — {record.detail}. Sample metadata below is still readable.
        </div>
      {:else}
        <p class="text-sm text-red-700">HTTP {record.status} — {record.detail}</p>
      {/if}
    </Card>
  </div>
{/if}

{#if sampleErr}
  <Card><p class="py-6 text-center text-amber-800">{sampleErr}</p></Card>
{:else if samples.length || loading}
  <Card title="Sample metadata" bodyClass="">
    {#snippet actions()}
      <div class="flex items-center gap-3">
        <input
          placeholder="filter…"
          class="w-56 rounded-md border border-gray-300 px-2.5 py-1 text-sm"
          bind:value={filter}
        />
        <button
          class="rounded-md bg-white px-3 py-1 text-sm font-medium text-teal-700 ring-1 ring-teal-600/30 ring-inset hover:bg-teal-50 disabled:opacity-40"
          onclick={() => (mapOpen = true)}
          disabled={!geoPoints.length}>Map ({geoPoints.length})</button
        >
      </div>
    {/snippet}

    <div class="overflow-x-auto">
      <table class="min-w-full divide-y divide-gray-200 text-sm">
        <thead class="bg-gray-50">
          <tr>
            <th class="sticky left-0 bg-gray-50 px-3 py-2 text-left text-xs font-semibold tracking-wide text-gray-500 uppercase">Accession</th>
            {#each columns as c}
              <th class="px-3 py-2 text-left text-xs font-semibold tracking-wide whitespace-nowrap text-gray-500 uppercase">{c.label}</th>
            {/each}
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          {#each paged as s (s.biosample_idx)}
            <tr class="hover:bg-gray-50">
              <td class="sticky left-0 z-10 bg-white px-3 py-2 font-mono text-xs whitespace-nowrap text-gray-700">{s.biosample_accession ?? s.biosample_idx}</td>
              {#each columns as c}
                <td class="px-3 py-2 whitespace-nowrap text-gray-600">{cell(s, c.key)}</td>
              {/each}
            </tr>
          {/each}
        </tbody>
      </table>
    </div>

    <div class="flex items-center justify-between border-t border-gray-100 px-4 py-2 text-xs text-gray-500">
      <span>
        {#if loading}Loading {samples.length} of {total}…{:else}
          {shown.length ? clampedPage * PAGE + 1 : 0}–{Math.min((clampedPage + 1) * PAGE, shown.length)}
          of {shown.length}{filter ? ` (filtered from ${samples.length})` : ''}
        {/if}
      </span>
      <span class="flex items-center gap-2">
        <button class="rounded border border-gray-300 px-2 py-0.5 hover:bg-gray-50 disabled:opacity-40" onclick={() => (pageNo = Math.max(0, clampedPage - 1))} disabled={clampedPage === 0}>Prev</button>
        <span>Page {clampedPage + 1} / {pageCount}</span>
        <button class="rounded border border-gray-300 px-2 py-0.5 hover:bg-gray-50 disabled:opacity-40" onclick={() => (pageNo = Math.min(pageCount - 1, clampedPage + 1))} disabled={clampedPage >= pageCount - 1}>Next</button>
      </span>
    </div>
  </Card>
{/if}

<Modal bind:open={mapOpen} title="Geographic distribution — {geoPoints.length} samples">
  <GeoMap points={geoPoints} />
</Modal>
