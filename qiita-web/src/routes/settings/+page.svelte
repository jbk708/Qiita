<script lang="ts">
  import { auth } from '$lib/auth.svelte';
  import { api, type ApiResult, type UserProfile, type ApiToken } from '$lib/api';
  import PageHeading from '$lib/ui/PageHeading.svelte';
  import Card from '$lib/ui/Card.svelte';

  let profile = $state<ApiResult<UserProfile> | null>(null);
  let tokens = $state<ApiResult<ApiToken[]> | null>(null);
  let saving = $state(false);
  let saveMsg = $state('');
  let saveErr = $state('');

  // editable form (seeded from the loaded profile)
  let form = $state({ affiliation: '', address: '', phone: '', orcid: '', receive_processing_emails: true });

  async function loadAll() {
    if (!auth.isSet) return;
    [profile, tokens] = await Promise.all([api.getProfile(), api.listTokens()]);
    if (profile.ok) {
      const p = profile.data;
      form = {
        affiliation: p.affiliation ?? '',
        address: p.address ?? '',
        phone: p.phone ?? '',
        orcid: p.orcid ?? '',
        receive_processing_emails: p.receive_processing_emails
      };
    }
  }
  $effect(() => {
    if (auth.isSet && profile === null) loadAll();
    else if (!auth.isSet) {
      profile = null;
      tokens = null;
    }
  });

  async function save() {
    saving = true;
    saveMsg = '';
    saveErr = '';
    const r = await api.updateProfile({
      affiliation: form.affiliation,
      address: form.address,
      phone: form.phone,
      orcid: form.orcid.trim() === '' ? null : form.orcid.trim(),
      receive_processing_emails: form.receive_processing_emails
    });
    if (r.ok) {
      profile = { ok: true, status: r.status, data: r.data };
      saveMsg = 'Saved.';
    } else {
      saveErr = `HTTP ${r.status} — ${r.detail}`;
    }
    saving = false;
  }

  async function revoke(idx: number) {
    const r = await api.revokeToken(idx);
    if (r.ok) tokens = await api.listTokens();
    else saveErr = `revoke failed: HTTP ${r.status} — ${r.detail}`;
  }

  const complete = $derived(profile?.ok ? profile.data.profile_complete : false);
</script>

<PageHeading title="Settings" subtitle="Your profile and API tokens." />

{#if !auth.isSet}
  <Card><p class="py-8 text-center text-gray-500">Log in to manage your account.</p></Card>
{:else if profile && !profile.ok}
  <Card><p class="py-8 text-center text-red-700">HTTP {profile.status} — {profile.detail}</p></Card>
{:else if profile?.ok}
  <div class="space-y-6">
    <Card title="Profile">
      <div class="grid grid-cols-1 gap-x-8 gap-y-6 md:grid-cols-3">
        <div class="md:col-span-1">
          <p class="text-sm text-gray-500">Signed in as</p>
          <p class="text-sm font-medium text-gray-900">{profile.data.email}</p>
          <p class="mt-1 text-xs text-gray-400">principal #{profile.data.principal_idx}</p>
          <div class="mt-3">
            {#if complete}
              <span class="inline-flex items-center rounded-md bg-green-50 px-2 py-0.5 text-xs font-medium text-green-700 ring-1 ring-green-600/20 ring-inset">profile complete</span>
            {:else}
              <span class="inline-flex items-center rounded-md bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800 ring-1 ring-amber-600/20 ring-inset">profile incomplete</span>
              <p class="mt-2 text-xs text-gray-500">Fill affiliation, address, and phone to enable creating studies & biosamples.</p>
            {/if}
          </div>
        </div>

        <div class="space-y-4 md:col-span-2">
          {#each [['affiliation', 'Affiliation'], ['address', 'Address'], ['phone', 'Phone'], ['orcid', 'ORCID (optional)']] as [key, label]}
            <div>
              <label class="mb-1 block text-sm font-medium text-gray-700" for={key}>{label}</label>
              <input
                id={key}
                class="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm shadow-sm focus:border-teal-500 focus:ring-teal-500"
                bind:value={form[key as 'affiliation' | 'address' | 'phone' | 'orcid']}
              />
            </div>
          {/each}
          <label class="flex items-center gap-2 text-sm text-gray-700">
            <input type="checkbox" class="rounded border-gray-300 text-teal-600 focus:ring-teal-500" bind:checked={form.receive_processing_emails} />
            Receive processing emails
          </label>

          <div class="flex items-center gap-3 pt-2">
            <button
              class="rounded-md bg-teal-700 px-4 py-1.5 text-sm font-semibold text-white shadow-sm hover:bg-teal-600 disabled:opacity-50"
              onclick={save}
              disabled={saving}>{saving ? 'Saving…' : 'Save profile'}</button
            >
            {#if saveMsg}<span class="text-sm text-green-700">{saveMsg}</span>{/if}
            {#if saveErr}<span class="text-sm text-red-700">{saveErr}</span>{/if}
          </div>
        </div>
      </div>
    </Card>

    <Card title="API tokens" bodyClass="">
      {#if tokens && !tokens.ok}
        <p class="px-4 py-6 text-center text-red-700">HTTP {tokens.status} — {tokens.detail}</p>
      {:else if tokens?.ok}
        <table class="min-w-full divide-y divide-gray-200 text-sm">
          <thead class="bg-gray-50">
            <tr>
              {#each ['Label', 'Scopes', 'Created', 'Last used', 'Expires', ''] as h}
                <th class="px-4 py-2 text-left text-xs font-semibold tracking-wide text-gray-500 uppercase">{h}</th>
              {/each}
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-100">
            {#each tokens.data as t (t.token_idx)}
              <tr class="hover:bg-gray-50 {t.revoked_at ? 'opacity-50' : ''}">
                <td class="px-4 py-2 font-medium text-gray-900">{t.label}</td>
                <td class="px-4 py-2 text-xs text-gray-500">{t.scopes.length} scopes</td>
                <td class="px-4 py-2 text-gray-500">{t.created_at.slice(0, 10)}</td>
                <td class="px-4 py-2 text-gray-500">{t.last_used_at ? t.last_used_at.slice(0, 10) : '—'}</td>
                <td class="px-4 py-2 text-gray-500">{t.expires_at ? t.expires_at.slice(0, 10) : 'never'}</td>
                <td class="px-4 py-2 text-right">
                  {#if t.revoked_at}
                    <span class="text-xs text-gray-400">revoked</span>
                  {:else}
                    <button class="text-xs font-medium text-red-600 hover:text-red-700" onclick={() => revoke(t.token_idx)}>Revoke</button>
                  {/if}
                </td>
              </tr>
            {:else}
              <tr><td colspan="6" class="px-4 py-6 text-center text-gray-500">No tokens.</td></tr>
            {/each}
          </tbody>
        </table>
        <p class="border-t border-gray-100 px-4 py-2 text-xs text-gray-400">
          Revoking the token this session is using will sign you out.
        </p>
      {/if}
    </Card>
  </div>
{/if}
