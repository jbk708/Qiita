<script lang="ts">
  import { auth } from '$lib/auth.svelte';
  import { api, type ApiResult, type PrepProtocol } from '$lib/api';
  import PageHeading from '$lib/ui/PageHeading.svelte';
  import Card from '$lib/ui/Card.svelte';

  let res = $state<ApiResult<PrepProtocol[]> | null>(null);
  let loading = $state(false);

  async function load() {
    loading = true;
    res = await api.prepProtocols();
    loading = false;
  }
  $effect(() => {
    if (auth.isSet && res === null && !loading) load();
    else if (!auth.isSet) res = null;
  });

  const rows = $derived(res?.ok ? res.data : []);
</script>

<PageHeading title="Prep protocols" subtitle="Library-preparation protocols configured on this deployment." />

{#if !auth.isSet}
  <Card><p class="py-8 text-center text-gray-500">Log in to view prep protocols.</p></Card>
{:else if res && !res.ok}
  <Card><p class="py-8 text-center text-red-700">HTTP {res.status} — {res.detail}</p></Card>
{:else}
  <Card bodyClass="">
    <table class="min-w-full divide-y divide-gray-200">
      <thead class="bg-gray-50">
        <tr>
          {#each ['#', 'Name', 'Description', 'Retired'] as h}
            <th class="px-4 py-2.5 text-left text-xs font-semibold tracking-wide text-gray-500 uppercase"
              >{h}</th
            >
          {/each}
        </tr>
      </thead>
      <tbody class="divide-y divide-gray-100">
        {#each rows as p (p.prep_protocol_idx)}
          <tr class="hover:bg-gray-50">
            <td class="px-4 py-2.5 text-sm text-gray-400">{p.prep_protocol_idx}</td>
            <td class="px-4 py-2.5 text-sm font-medium text-gray-900">{p.name}</td>
            <td class="px-4 py-2.5 text-sm text-gray-500">{p.description ?? '—'}</td>
            <td class="px-4 py-2.5 text-sm text-gray-500">{p.retired ? 'yes' : 'no'}</td>
          </tr>
        {:else}
          <tr><td colspan="4" class="px-4 py-8 text-center text-gray-500">
            {loading ? 'Loading…' : 'No prep protocols.'}
          </td></tr>
        {/each}
      </tbody>
    </table>
  </Card>
{/if}
