<script lang="ts">
  import { settings, saveSettings } from '$lib/settings.svelte';
  import PageHeading from '$lib/ui/PageHeading.svelte';
  import Card from '$lib/ui/Card.svelte';
</script>

<PageHeading title="Settings" subtitle="Client-side preferences — stored in this browser only." />

<Card title="Display">
  <div class="divide-y divide-gray-100">
    <label class="flex cursor-pointer items-start justify-between gap-4 py-3">
      <span>
        <span class="block text-sm font-medium text-gray-900">Humanize field labels</span>
        <span class="block text-xs text-gray-500">Title-case metadata field names (host_taxon_id → Host Taxon Id).</span>
      </span>
      <input
        type="checkbox"
        bind:checked={settings.humanizeLabels}
        onchange={saveSettings}
        class="mt-0.5 size-4 rounded border-gray-300 text-teal-600 focus:ring-teal-500"
      />
    </label>
    <label class="flex cursor-pointer items-start justify-between gap-4 py-3">
      <span>
        <span class="block text-sm font-medium text-gray-900">Remember recent studies</span>
        <span class="block text-xs text-gray-500">List studies you visit or create under "Recent studies".</span>
      </span>
      <input
        type="checkbox"
        bind:checked={settings.rememberRecents}
        onchange={saveSettings}
        class="mt-0.5 size-4 rounded border-gray-300 text-teal-600 focus:ring-teal-500"
      />
    </label>
  </div>
</Card>

<Card title="Metadata assistant (LLM)">
  <div class="flex flex-col gap-3">
    <p class="text-xs text-gray-500">
      Optional. When metameq flags a value it doesn't recognize (an unknown host or sample
      type, a value that doesn't fit a field's type), your endpoint is asked to suggest a
      fix. It only ever sees the field name, the distinct values, and the allowed terms —
      never sample rows. Any OpenAI-compatible endpoint works; the key stays in this browser.
    </p>
    <label class="flex cursor-pointer items-start justify-between gap-4">
      <span>
        <span class="block text-sm font-medium text-gray-900">Enable LLM suggestions</span>
        <span class="block text-xs text-gray-500">Off by default. No calls are made unless this is on.</span>
      </span>
      <input
        type="checkbox"
        bind:checked={settings.llm.enabled}
        onchange={saveSettings}
        class="mt-0.5 size-4 rounded border-gray-300 text-teal-600 focus:ring-teal-500"
      />
    </label>
    <label class="flex flex-col gap-1">
      <span class="text-sm font-medium text-gray-900">Base URL</span>
      <input
        type="url"
        placeholder="https://api.openai.com/v1"
        bind:value={settings.llm.baseUrl}
        onchange={saveSettings}
        class="rounded-md border border-gray-300 px-2.5 py-1 text-sm focus:border-teal-500 focus:ring-1 focus:ring-teal-500 focus:outline-none"
      />
    </label>
    <label class="flex flex-col gap-1">
      <span class="text-sm font-medium text-gray-900">Model</span>
      <input
        type="text"
        placeholder="gpt-4o-mini"
        bind:value={settings.llm.model}
        onchange={saveSettings}
        class="rounded-md border border-gray-300 px-2.5 py-1 text-sm focus:border-teal-500 focus:ring-1 focus:ring-teal-500 focus:outline-none"
      />
    </label>
    <label class="flex flex-col gap-1">
      <span class="text-sm font-medium text-gray-900">API key</span>
      <input
        type="password"
        autocomplete="off"
        placeholder="sk-…"
        bind:value={settings.llm.apiKey}
        onchange={saveSettings}
        class="rounded-md border border-gray-300 px-2.5 py-1 text-sm focus:border-teal-500 focus:ring-1 focus:ring-teal-500 focus:outline-none"
      />
    </label>
  </div>
</Card>
