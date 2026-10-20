# Deploy checklist

Operator-facing deploy instructions — **not** a "what changed" log (that's the changelog, see [`CHANGELOG.md`](CHANGELOG.md); the git log is the authoritative record). `## Pending deploy` is the single consolidated checklist for the next deploy; past deploys are archived one file each under [`docs/deploy-archive/`](docs/deploy-archive/).

- **Deploying?** Follow [`docs/runbooks/redeploy.md`](docs/runbooks/redeploy.md) — it is the source of truth for the procedure (bucket order, `[admin]`/`[operator]` labels, the migration guard, archiving).
- **Adding to a PR?** Fold your operator steps into the `## Pending deploy` buckets with `/deploy-note`; don't add a standalone entry. The authoring rules are in CLAUDE.md ("Operator-facing changes").

Substitute your host's FQDN for the `qiita-miint.ucsd.edu` examples and `<scratch>` for the scratch root chosen at first deploy.

---

## Pending deploy

Everything merged but not yet deployed, folded in by each PR as it merges. Run buckets 1→6 in order; buckets 1–3 must precede the bucket-4 restart, and bucket 6 (irreversible cleanup — anything that burns the rollback path) must not run until bucket 5 is green. Each step carries its source `(#N)` tag.

### 1. Env vars — set BEFORE the deploy (most are `from_env()` fail-fast; a missing one keeps the unit down)

_None yet._

### 2. One-time host setup

_None yet._

### 3. Migrations

- `[operator]` (#feat/m1-26-partial-pool-landing) `make migrate` applies `20261021000000_sequenced_sample_download_failure.sql` (two nullable columns and a both-or-neither CHECK on `qiita.sequenced_sample`; no backfill).

### 4. Deploy

- `[admin]` (#feat/m1-26-partial-pool-landing) Before stopping the control plane, confirm no `download-ena-study` ticket is in flight; if the query lists any, wait for them to finish. `download-ena-study` 1.0.0 is edited in place (`qiita-admin actions sync` in `activate.sh` re-syncs it), and a ticket whose ingest step finished on the old code fails on the missing `run_outcomes` output, which a redrive cannot recover:
  `SELECT work_ticket_idx, state FROM qiita.work_ticket WHERE action_id = 'download-ena-study' AND state IN ('pending','queued','processing');`
  If one was missed and its `register-files` step has no COMPLETED row in `qiita.work_ticket_step`, re-import the study. If `register-files` completed, do not re-import or `--force` that pool (its reads would load twice); escalate.

### 5. Verify

- `[admin]` (#feat/m1-26-partial-pool-landing) `qiita.action` lists `download-ena-study` 1.0.0 with three steps, ending in `persist-ena-run-outcomes`, and `\d qiita.sequenced_sample` shows `download_failure_reason` and `download_failure_type`.

### 6. After the deploy verifies green

_None yet._

### Notes (no host action)

- (#feat/m1-26-partial-pool-landing) `GET /ena-import-batch/{idx}` items can read `completed_partial` (a pool landed with some runs failed permanently), and `ena_runs[].status` can be `download_failed`, with a new `failure_type`; clients matching on the closed set of states or statuses must accept them. `submit-ena-import --watch` exits 1 on a `completed_partial` item. A partially landed ticket is `completed` and sends no email.

## Deployed history

Past deploys live one file each in [`docs/deploy-archive/`](docs/deploy-archive/) — newest
first in its [index](docs/deploy-archive/README.md). `/deploy-archive` writes the next one
there when a deploy closes out.

(This heading has no content under it by design, and is not dead weight: it terminates the
`sed` range that prints `## Pending deploy` for the operator and for `/deploy-note`. See
`test_deployed_history_heading_pins_the_live_section_boundary`.)
