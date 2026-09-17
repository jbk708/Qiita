# qiita-web (POC)

A read-only, tabular study/sample browser — a thin client over the control-plane
REST API. Static SPA (SvelteKit + `adapter-static`, Svelte 5 runes); no server,
no business logic. Proves the spine: **token auth → generated-client reads →
tiered graceful degradation.**

## Run (dev)

```bash
cd qiita-web
npm install
# point at a stack (default: the remote deploy). Vite proxies /api → this host,
# so the browser talks same-origin and there is no CORS.
QIITA_BASE=https://qiita-miint.ucsd.edu npm run dev
```

Open the dev URL, paste your PAT (from `~/.qiita/token`), and load a study.

## What it shows

- **whoami** — your principal, role, scopes, profile status.
- **study record** — needs tier ≥ **member**; a viewer sees a `🔒 restricted`
  banner instead of a hard error.
- **samples** — the `list-idxs` sets (sequenced + biosample), viewer-readable,
  with a client-side idx filter.

## Known tier limitation (why the metadata table is empty on 25006)

On the current deploy, a **viewer** can read `list-idxs` but the per-sample
**record + metadata** (and the study field definitions) are **ADMIN-gated**
(marked "interim" in the CP). So a viewer sees the *idx set* but not the *fields*.
Point this at a study you **own/admin** to populate the metadata table.

## The keystone (next step)

The client in `src/lib/api.ts` is hand-typed for the three viewer endpoints. Swap
it for generated types:

```bash
QIITA_BASE=https://qiita-miint.ucsd.edu npm run gen:api   # -> src/lib/api/schema.d.ts
```

then re-implement `api.ts` on `openapi-fetch` + those types, so the UI can never
drift from the API.

## Deploy

Serve the app from the **same origin** as the control plane so the relative
`/api/v1/*` calls just work. Example host below: `qiita.knight-lab-dev.org`.

**1. Build.** Bake the CP origin in for the login redirect:

```bash
VITE_QIITA_BASE=https://qiita.knight-lab-dev.org npm run build
```

That emits a static bundle in `build/` — no Node runtime to run.

**2. Point DNS + get a cert.** `qiita.knight-lab-dev.org` → the node's IP, and a
TLS cert for it (login needs HTTPS).

**3. nginx.** `server_name qiita.knight-lab-dev.org;`, serve `build/` with SPA
fallback, and keep `location /api/v1/ → control_plane` (+ the Flight `location`
→ data_plane). Same block as `deploy/nginx/qiita.conf` with the hostname filled
in.

**4. Set the callback in `control-plane.env`, then restart the CP:**

```
QIITA_ENDPOINT_URL=https://qiita.knight-lab-dev.org
```

(These auth secrets must already be set: `AUTHROCKET_LOGINROCKET_URL`,
`AUTHROCKET_ISSUER`, `AUTHROCKET_JWKS_URL`, `AUTHROCKET_AUDIENCE`, and the
base64 login-cookie secret.)

**5. Whitelist the redirect in AuthRocket.** In the LoginRocket app config, add
this to the allowed redirect URIs — login fails until you do:

```
https://qiita.knight-lab-dev.org/api/v1/auth/handoff
```

Then log in through the browser flow and paste the PAT it hands you. (The
seamless loopback login only kicks in when you open the app on `localhost`.)
