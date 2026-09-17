<script lang="ts">
  import type { Snippet } from 'svelte';
  // Kit "in-card" container. Pass bodyClass="" for flush content like tables.
  let {
    title = '',
    bodyClass = 'px-4 py-4 sm:px-6',
    actions,
    children
  }: {
    title?: string;
    bodyClass?: string;
    actions?: Snippet;
    children: Snippet;
  } = $props();
</script>

<div class="overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-200">
  {#if title || actions}
    <div class="flex items-center justify-between gap-3 border-b border-gray-200 px-4 py-3 sm:px-6">
      <h3 class="text-sm font-semibold text-gray-900">{title}</h3>
      {#if actions}{@render actions()}{/if}
    </div>
  {/if}
  <div class={bodyClass}>{@render children()}</div>
</div>
