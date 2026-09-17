<script lang="ts">
  let { status }: { status: string } = $props();
  const map: Record<string, string> = {
    active: 'bg-green-50 text-green-700 ring-green-600/20',
    failed: 'bg-red-50 text-red-700 ring-red-600/10',
    pending: 'bg-yellow-50 text-yellow-800 ring-yellow-600/20'
  };
  const cls = $derived(map[status] ?? 'bg-gray-50 text-gray-600 ring-gray-500/10');
</script>

<span
  class="inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium capitalize ring-1 ring-inset {cls}"
  >{status}</span
>
