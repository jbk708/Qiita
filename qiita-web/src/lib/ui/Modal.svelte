<script lang="ts">
  import type { Snippet } from 'svelte';
  let {
    open = $bindable(false),
    title = '',
    children
  }: { open?: boolean; title?: string; children: Snippet } = $props();

  function onkey(e: KeyboardEvent) {
    if (e.key === 'Escape') open = false;
  }
</script>

<svelte:window onkeydown={onkey} />

{#if open}
  <div class="fixed inset-0 z-50 flex items-center justify-center p-4">
    <button
      type="button"
      class="absolute inset-0 bg-gray-900/50"
      aria-label="Close"
      onclick={() => (open = false)}
    ></button>
    <div
      class="relative z-10 flex max-h-[85vh] w-full max-w-5xl flex-col overflow-hidden rounded-lg bg-white shadow-xl"
    >
      <div class="flex items-center justify-between border-b border-gray-200 px-4 py-3">
        <h3 class="text-sm font-semibold text-gray-900">{title}</h3>
        <button
          class="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
          onclick={() => (open = false)}
          aria-label="Close">✕</button
        >
      </div>
      <div class="overflow-auto p-4">
        {@render children()}
      </div>
    </div>
  </div>
{/if}
