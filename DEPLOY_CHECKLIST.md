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

- `long-read-assembly` 1.0.1's in-place resource edit reached `qiita.action`. `PT2H30M` exists only in the new `bin_refine` baseline and `sync` replaces the whole `steps` array, so this one token proves the edit landed (#557):
  ```bash
  sudo -u qiita-api bash -c 'set -a; . /etc/qiita/control-plane.env; set +a
  psql "$DATABASE_URL" -Atc "SELECT steps::text LIKE '\''%PT2H30M%'\'' FROM qiita.action WHERE action_id = '\''long-read-assembly'\'' AND version = '\''1.0.1'\'';"'
  ```
  Expect `t`. `f` means the row still holds the pre-edit steps; **empty output** means no `long-read-assembly` 1.0.1 row synced at all.
- The rebuilt binning image derives metaWRAP's `-m` from the allocation. Anchored at start-of-line so a comment mentioning it cannot satisfy it (#557):
  ```bash
  derived=$(sudo grep '^PATH_DERIVED=' /etc/qiita/compute-orchestrator.env | tail -1 | cut -d= -f2-)
  cd /tmp && sudo -u qiita-orch apptainer exec --no-home \
    "$derived/images/long-read-assembly-binning-1.0.0.sif" \
    bash -c 'grep -q "^METAWRAP_MEM_GB=.*MEM_MB" /opt/qiita/binning.sh' \
    && echo BINNING_MEM_OK
  ```
  Expect `BINNING_MEM_OK`; without it, the image did not open or does not carry the derived `-m`.

### 6. After the deploy verifies green

_None yet._

### Notes (no host action)

- `long-read-assembly` 1.0.1 is **edited in place** — resource baselines for `bin_refine`, `binning`, `checkm` and the myloasm `assemble` profile — and re-synced into `qiita.action` by `qiita-admin actions sync` inside `activate.sh`. No new action, no migration (#557).
- The `long-read-assembly` binning SIF auto-rebuilds on deploy to pick up `binning.sh`'s allocation-derived metaWRAP `-m` (`binning.sh` is in its `HASH_INPUTS`). The image is shared with 1.0.0, whose 100 GB baseline still yields `-m 90` (#557).

## Deployed history

Past deploys live one file each in [`docs/deploy-archive/`](docs/deploy-archive/) — newest
first in its [index](docs/deploy-archive/README.md). `/deploy-archive` writes the next one
there when a deploy closes out.

(This heading has no content under it by design, and is not dead weight: it terminates the
`sed` range that prints `## Pending deploy` for the operator and for `/deploy-note`. See
`test_deployed_history_heading_pins_the_live_section_boundary`.)
