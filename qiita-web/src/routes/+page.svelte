<script lang="ts">
  import { auth } from '$lib/auth.svelte';
  import { api, type ApiResult, type Reference } from '$lib/api';
  import PageHeading from '$lib/ui/PageHeading.svelte';
  import Card from '$lib/ui/Card.svelte';
  import StatusPill from '$lib/ui/StatusPill.svelte';

  let res = $state<ApiResult<Reference[]> | null>(null);
  let loading = $state(false);
  let q = $state('');

  async function load() {
    loading = true;
    res = await api.references();
    loading = false;
  }

  // Auto-load once signed in; reset when the token is cleared.
  $effect(() => {
    if (auth.isSet && res === null && !loading) load();
    else if (!auth.isSet) res = null;
  });

  const rows = $derived(res?.ok ? res.data : []);
  const shown = $derived(
    q.trim()
      ? rows.filter((r) => `${r.name} ${r.kind} ${r.status}`.toLowerCase().includes(q.toLowerCase()))
      : rows
  );
</script>

<PageHeading title="References" subtitle="Loaded reference databases and artifact sequence sets.">
  {#snippet actions()}
    <button
      class="rounded-md bg-white px-3 py-1.5 text-sm font-medium text-gray-700 shadow-sm ring-1 ring-gray-300 ring-inset hover:bg-gray-50 disabled:opacity-50"
      onclick={load}
      disabled={!auth.isSet || loading}>{loading ? 'Loading…' : 'Reload'}</button
    >
  {/snippet}
</PageHeading>

{#if !auth.isSet}
  <Card><p class="py-8 text-center text-gray-500">Log in (top right) to browse references.</p></Card>
{:else if res && !res.ok}
  <Card><p class="py-8 text-center text-red-700">HTTP {res.status} — {res.detail}</p></Card>
{:else}
  <Card bodyClass="">
    {#snippet actions()}
      <input
        placeholder="filter…"
        class="w-56 rounded-md border border-gray-300 px-2.5 py-1 text-sm"
        bind:value={q}
      />
    {/snippet}
    <table class="min-w-full divide-y divide-gray-200">
      <thead class="bg-gray-50">
        <tr>
          {#each ['Name', 'Version', 'Kind', 'Status', 'Host', 'Created'] as h}
            <th class="px-4 py-2.5 text-left text-xs font-semibold tracking-wide text-gray-500 uppercase"
              >{h}</th
            >
          {/each}
        </tr>
      </thead>
      <tbody class="divide-y divide-gray-100">
        {#each shown as r (r.reference_idx)}
          <tr class="hover:bg-gray-50">
            <td class="px-4 py-2.5 text-sm font-medium">
              <a href="/references/{r.reference_idx}" class="text-teal-700 hover:underline">{r.name}</a>
            </td>
            <td class="px-4 py-2.5 text-sm text-gray-500">{r.version}</td>
            <td class="px-4 py-2.5 text-sm text-gray-500">{r.kind.replace(/_/g, ' ')}</td>
            <td class="px-4 py-2.5 text-sm"><StatusPill status={r.status} /></td>
            <td class="px-4 py-2.5 text-sm text-gray-500">{r.is_host ? 'host' : '—'}</td>
            <td class="px-4 py-2.5 text-sm text-gray-500">{r.created_at.slice(0, 10)}</td>
          </tr>
        {:else}
          <tr><td colspan="6" class="px-4 py-8 text-center text-gray-500">
            {loading ? 'Loading…' : q ? 'No matches.' : 'No references.'}
          </td></tr>
        {/each}
      </tbody>
    </table>
  </Card>
  {#if res?.ok}
    <p class="mt-2 text-xs text-gray-400">{shown.length} of {rows.length} references</p>
  {/if}
{/if}
