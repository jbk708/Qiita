<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { auth } from '$lib/auth.svelte';
  import { getRecents, removeRecent, type RecentStudy } from '$lib/recents';
  import PageHeading from '$lib/ui/PageHeading.svelte';
  import Card from '$lib/ui/Card.svelte';

  let jumpIdx = $state<number | null>(null);
  let recents = $state<RecentStudy[]>([]);

  onMount(() => {
    recents = getRecents();
  });

  function open() {
    if (jumpIdx && jumpIdx > 0) goto(`/studies/${jumpIdx}`);
  }
  function drop(idx: number) {
    removeRecent(idx);
    recents = getRecents();
  }
</script>

<PageHeading
  title="Studies"
  subtitle="Qiita has no list-all-studies endpoint — open one directly by id, or pick a recent."
/>

{#if !auth.isSet}
  <Card><p class="py-8 text-center text-gray-500">Log in to open a study.</p></Card>
{:else}
  <div class="mb-6">
    <Card title="Jump to a study">
      <div class="flex items-end gap-2">
        <label class="text-sm">
          <span class="mb-1 block text-gray-500">study idx</span>
          <input
            type="number"
            min="1"
            placeholder="e.g. 25006"
            bind:value={jumpIdx}
            onkeydown={(e: KeyboardEvent) => e.key === 'Enter' && open()}
            class="w-40 rounded-md border border-gray-300 px-3 py-1.5 text-sm"
          />
        </label>
        <button
          class="rounded-md bg-teal-700 px-4 py-1.5 text-sm font-semibold text-white shadow-sm hover:bg-teal-600 disabled:opacity-50"
          onclick={open}
          disabled={!jumpIdx || jumpIdx <= 0}>Open</button
        >
      </div>
      <p class="mt-2 text-xs text-gray-400">
        A study you can't see returns a restricted record but may still expose sample metadata (viewer tier).
      </p>
    </Card>
  </div>

  <Card title="Recently opened" bodyClass="">
    {#if recents.length}
      <table class="min-w-full divide-y divide-gray-200 text-sm">
        <thead class="bg-gray-50">
          <tr>
            {#each ['Study', 'Access', 'Biosamples', 'Opened', ''] as h}
              <th class="px-4 py-2 text-left text-xs font-semibold tracking-wide text-gray-500 uppercase">{h}</th>
            {/each}
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          {#each recents as r (r.idx)}
            <tr class="hover:bg-gray-50">
              <td class="px-4 py-2 font-medium">
                <a href="/studies/{r.idx}" class="text-teal-700 hover:underline">Study {r.idx}</a>
              </td>
              <td class="px-4 py-2">
                {#if r.accessible}
                  <span class="inline-flex items-center rounded-md bg-green-50 px-2 py-0.5 text-xs font-medium text-green-700 ring-1 ring-green-600/20 ring-inset">accessible</span>
                {:else}
                  <span class="inline-flex items-center rounded-md bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800 ring-1 ring-amber-600/20 ring-inset">restricted</span>
                {/if}
              </td>
              <td class="px-4 py-2 text-gray-600">{r.biosampleCount ?? '—'}</td>
              <td class="px-4 py-2 text-gray-500">{new Date(r.lastOpened).toLocaleString()}</td>
              <td class="px-4 py-2 text-right">
                <button class="text-xs text-gray-400 hover:text-red-600" onclick={() => drop(r.idx)}>remove</button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    {:else}
      <p class="px-4 py-8 text-center text-gray-500">No studies opened yet — jump to one above.</p>
    {/if}
  </Card>
{/if}
