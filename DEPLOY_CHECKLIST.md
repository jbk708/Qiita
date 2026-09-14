# Deploy checklist

Operator-facing deploy instructions — **not** a "what changed" log (that's [`CHANGELOG.md`](CHANGELOG.md); the git log is the authoritative record). `## Pending deploy` is the single consolidated checklist for the next deploy; past deploys are archived one file each under [`docs/deploy-archive/`](docs/deploy-archive/).

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

_None yet._

### 4. Deploy

_None yet._

### 5. Verify

- `align` 1.0.0's in-place walltime-ceiling edit reached `qiita.action` (#563):
  ```bash
  sudo -u qiita-api bash -c 'set -a; . /etc/qiita/control-plane.env; set +a
  psql "$DATABASE_URL" -Atc "SELECT walltime_ceiling FROM qiita.action WHERE action_id = '\''align'\'' AND version = '\''1.0.0'\'';"'
  ```
  Expect `16:00:00`. `08:00:00` means the row still holds the pre-edit ceiling; **empty output** means no `align` 1.0.0 row synced at all.
- Redrive the close-reference blocks that failed at the old PT8H ceiling, each only if `qiita ticket status <idx>` still reads `failed` with "walltime escalation exhausted" (#563):
  ```bash
  for t in 9387 9411 9475 9476; do qiita ticket run "$t"; done
  ```
  Each first runs once more at its persisted PT8H floor, then escalates to PT16H on that timeout.
- `estimate-feature-table` is edited IN PLACE at 1.0.0, so the generic `qiita.action`
  list cannot tell a fresh sync from a stale row — the version string is unchanged.
  Assert the two new `context_schema` keys instead: (#564)

  ```bash
  psql "$DATABASE_URL" -tAc "SELECT context_schema->'properties' ? 'min_completeness' AND context_schema->'properties' ? 'max_contamination' FROM qiita.action WHERE action_id='estimate-feature-table' AND version='1.0.0'"
  ```

  Expect `t`. An `f` means `qiita-admin actions sync` did not pick the edited YAML up,
  and combined tables will still be built ungated.

### 6. After the deploy verifies green

_None yet._

### Notes (no host action)

- **Edited in place** and re-synced into `qiita.action` by `qiita-admin actions sync` inside `activate.sh` — no new action, no migration: `align` 1.0.0's `action_ceiling.walltime`, PT8H → PT16H, with `align_sharded`'s PT4H baseline unchanged (#563).
- **Combined feature tables change by default.** `estimate-feature-table` now gates the
  de novo arm's assembled genomes on CheckM completeness >= 50 / contamination <= 10, via
  two new optional `action_context` keys (`min_completeness`, `max_contamination`). A
  ticket carrying `denovo_alignment_idx` and naming neither key gets the gate, so a table
  rebuilt after this deploy holds fewer qiita genomes than the same request did before
  it: on the one arm an `assembly`-subject alignment exists for (`alignment_idx` 4), the
  defaults keep 27,962 of 59,427 MAG/LCG genomes (47.1%) — 48.9% of MAG, 40.7% of LCG.
  Every LCG under 300 kb fails, on completeness (0 of 7,181), against 5,479 of 6,287 at or
  above it; that small band is largely, not wholly, the `root (UID1)` elements CheckM
  places in no lineage (6,588 of 7,181). Reads on an excluded genome keep their reference
  placement rather than
  dropping out; reference genomes and reference-only tickets are unaffected. No env var,
  host dir, scope or migration; the edited YAML re-syncs at 1.0.0 inside `activate.sh`
  (verify in bucket 5). (#564)
- **Assembly runs that predate circular-genome scoring can no longer be a de novo arm.**
  A combined-table submit naming one is refused with the prep_samples and counts, because
  its LCG subjects have no `bin_quality` row and the gate cannot judge them. Unlike
  `genome_idx`, a CheckM score cannot be backfilled — the run must be assembled again
  (a re-submit runs the only enabled `long-read-assembly` version, which scores circular
  genomes). No such submit exists today: the two affected runs are `long-read-assembly`
  1.0.0 (`processing_idx` 1 and 2, every LCG subject unscored — 1,627 and 2,806), and
  neither has an `assembly`-subject alignment, which is what a de novo arm is named by.
  Both are deprecated, run 1 superseded by run 2 and run 2 by `processing_idx` 3 — so the
  column reaches a scored run from run 1 only in two hops, via the other affected run.
  Nothing to do at deploy time; if it ever fires it surfaces as a failed ticket with an
  explanatory message. (#564)
- The client-side `qiita feature-table build --denovo-alignment-idx` is NOT gated, and
  is unaffected by any of the above: `bin_quality` is un-mintable over HTTP, so a PAT
  cannot reach the scores. A client-built combined table therefore still includes every
  MAG/LCG genome the map admits, and is not refused for an unscored run. (#564)

## Deployed history

Past deploys live one file each in [`docs/deploy-archive/`](docs/deploy-archive/) — newest
first in its [index](docs/deploy-archive/README.md). `/deploy-archive` writes the next one
there when a deploy closes out.

(This heading has no content under it by design, and is not dead weight: it terminates the
`sed` range that prints `## Pending deploy` for the operator and for `/deploy-note`. See
`test_deployed_history_heading_pins_the_live_section_boundary`.)
