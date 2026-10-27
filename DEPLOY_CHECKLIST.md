# Deploy checklist

Operator-facing deploy instructions — **not** a "what changed" log (that's the changelog, see [`CHANGELOG.md`](CHANGELOG.md); the git log is the authoritative record). `## Pending deploy` is the single consolidated checklist for the next deploy; past deploys are archived one file each under [`docs/deploy-archive/`](docs/deploy-archive/).

- **Deploying?** Follow [`docs/runbooks/redeploy.md`](docs/runbooks/redeploy.md) — it is the source of truth for the procedure (bucket order, `[admin]`/`[operator]` labels, the migration guard, archiving).
- **Adding to a PR?** Fold your operator steps into the `## Pending deploy` buckets with `/deploy-note`; don't add a standalone entry. The authoring rules are in CLAUDE.md ("Operator-facing changes").

Substitute your host's FQDN for the `qiita-miint.ucsd.edu` examples and `<scratch>` for the scratch root chosen at first deploy.

---

## Pending deploy

Everything merged but not yet deployed, folded in by each PR as it merges. Run buckets 1→6 in order; buckets 1–3 must precede the bucket-4 restart, and bucket 6 (irreversible cleanup — anything that burns the rollback path) must not run until bucket 5 is green. Each step carries its source `(#N)` tag.

### 1. Env vars — set BEFORE the deploy (most are `from_env()` fail-fast; a missing one keeps the unit down)

- `[operator]` **`PATH_INGEST_ROOTS` must cover the sequencer run-folder root(s).** No new
  variable — but `submit-golay-demux --instrument-run-id <id>` resolves the run folder by
  scanning `PATH_INGEST_ROOTS` for a directory whose basename matches the run id, so a run
  living under a path the roots don't cover cannot be submitted. Ensure the existing value
  includes wherever instruments copy runs. (#244)
- `[admin]` **Optional, recommended: name the deployment.** `QIITA_DEPLOYMENT_NAME` (CP) is
  served at `GET /api/v1/deployment` and shown by `qiita whoami` and the web UI's badge, so
  users can tell prod from a dev stack. Unset reports `null`; a malformed value (not a
  lowercase `[a-z0-9-]` slug, ≤32 chars) **fails CP boot**. (#feat/web-ui)
  ```
  sudo bash -c 'grep -q "^QIITA_DEPLOYMENT_NAME=" /etc/qiita/control-plane.env || echo "QIITA_DEPLOYMENT_NAME=prod" >> /etc/qiita/control-plane.env'   # (#feat/web-ui)
  ```

### 2. One-time host setup

- `[admin]` **Optional: the Greengenes2 filter's file.** The feature table's `greengenes2`
  filter (a rapid-16S table cut to the ASVs Greengenes2 places) needs a Parquet built once
  from the release's `taxonomy.md5.tsv.gz` (~490 MB; ~23.1M ASVs), placed where `qiita-api`
  can read it, and named in `control-plane.env` as `GREENGENES2_ASV_TAXONOMY_PATH` (an
  unreadable path **fails CP boot**; unset, the filter answers 503). (#feat/web-ui)
  ```
  curl -O http://ftp.microbio.me/greengenes_release/2024.09/2024.09.taxonomy.md5.tsv.gz
  python scripts/greengenes2-asv-taxonomy.py --taxonomy 2024.09.taxonomy.md5.tsv.gz \
      --out <shared dir>/greengenes2-2024.09-asv.parquet
  sudo bash -c 'echo "GREENGENES2_ASV_TAXONOMY_PATH=<shared dir>/greengenes2-2024.09-asv.parquet" >> /etc/qiita/control-plane.env'
  ```

### 3. Migrations

- `[operator]` (#685) `make migrate` applies `20261009000000_work_ticket_follow_on.sql` (three nullable columns on `qiita.work_ticket`, two CHECKs, one partial index; no backfill).
- `[operator]` `make migrate` applies `exported_identifier_processing` (amplicon QM identifiers) and
  `amplicon_sample` (the per-sample amplicon gate the study lineage reads). All additive;
  no out-of-band step.
- `[operator]` `make migrate` applies `profiling_database_reference`,
  `taxonomic_profile_sample`, `profiling_taxonomy` and `sylph_on_sequence_reference`.
  Together they admit the `sylph` index type and add the per-sample taxonomic-profile gate;
  the standalone profiling-database kind and taxonomy table the earlier two introduce, the
  last removes. No out-of-band step.
- `[operator]` (#711) `make migrate` applies `20261014000000_legacy_band_column_comments.sql` (rewords the comments on `study.idx`, `prep_sample.idx` and `sequenced_pool.idx`; no row, sequence or constraint change).

### 4. Deploy

- `[operator]` The actions sync registers **`amplicon` 1.1.0** and auto-deprecates 1.0.0
  (see the API-contract note below). 1.1.0 adds the `mint-amplicon-identifiers` and
  `finalize-amplicon-sample` tail steps, so its samples get QM identifiers, a servable
  feature table, and their per-sample lineage state.
- `[operator]` The actions sync registers **`taxonomic-profile` 1.0.0** (sylph over a
  pool's metagenomic samples). The data plane creates the DuckLake `taxonomic_profile`
  table at boot. To profile, register each sylph database on the sequence reference whose
  genomes it was sketched from, where it sits, on a path the control plane and the compute
  nodes share and the service accounts can read:
  `qiita reference register-file --reference-idx <web-of-life 3-rc4> --label ani95 --path /…/wol3_rc4_ani95.syl2db`
  (system admin PAT), then submit with `{"sylph_index_idx": <the index row>}`.
- `[api]` `/amplicon/feature-table/{samples,counts,features,ticket}` is now
  `/feature-table/{samples,values,features}` (`counts` → `values`, a `value` DOUBLE column;
  the samples answer adds `method` and `value_kind`; `ticket` is removed), and
  `qiita feature-table amplicon` is now `qiita feature-table runs`, which reads the routes
  and takes no `--data-plane-url`.
- `[admin]` (#767) `activate.sh` now waits, before any restart, for the data plane to go idle (no DoGet stream, DoPut or registration in flight), up to 3900 s, printing `waiting for qiita-data-plane@50051 to go idle …` once a minute; it then restarts whether or not it went idle. `QIITA_DP_IDLE_WAIT_S=<s>` in the deploy's environment changes the bound (0 skips it). The deploy stops before any restart if that value is not whole seconds or the wait cannot query the catalog; `QIITA_DP_IDLE_WAIT_S=0` gets past the second. This deploy still stops the old data-plane binary at once on SIGTERM: the drain ships with the new binary, so from the next deploy the data plane's own restart can take up to the unit's `TimeoutStopSec=3900` too.

### 5. Verify

- **Deployment name served:** `curl -s https://qiita-miint.ucsd.edu/api/v1/deployment` →
  `{"name":"prod"}` (`{"name":null}` if bucket 1's optional var was skipped). (#feat/web-ui)
- `[admin]` (#767) `systemctl show -p TimeoutStopUSec qiita-data-plane@50051` prints `TimeoutStopUSec=1h 5min`.

### 6. After the deploy verifies green

_None yet._

### Notes (no host action)

- **`amplicon` 1.1.0 replaces 1.0.0.** The deploy's action sync loads it and
  auto-deprecates 1.0.0, so new amplicon submissions must name 1.1.0 and pass only
  `sortmerna_reference_idx` and `trim`; a context still carrying `primer` or
  `orient_primer` is refused. A disabled version cannot run, so any 1.0.0 amplicon
  ticket still pending, in flight, or failed and awaiting `/run` at deploy time
  becomes unrunnable (it fails, or its redrive is refused): resubmit it as 1.1.0.
- (#685) Work-ticket responses (`GET /work-ticket/{idx}` and the ticket lists) gain `on_success`, `follow_on_work_ticket_idx` and `follow_on_error` (all null for a ticket with no follow-on); clients validating that shape strictly must accept them.
- (#707) New optional `FOLLOW_ON_SWEEP_INTERVAL_SECONDS` (default 60) tunes how often the control plane retries a transiently-failed follow-on; no action required.
- (#711) `POST /study` now answers 422 to a body key it does not define, where it used to drop the key and return 201; a client sending extra keys must stop. It also takes an optional `study_idx` (system_admin only).
- (#767) A killed DoPut leaves `staging/uploads/<idx>/upload.parquet.partial`, which nothing reclaims, as nothing reclaims finished uploads. `qiita-admin masked-read-export` gains `--retry-seconds` (default 3900, the data-plane unit's stop timeout).

## Deployed history

Past deploys live one file each in [`docs/deploy-archive/`](docs/deploy-archive/) — newest
first in its [index](docs/deploy-archive/README.md). `/deploy-archive` writes the next one
there when a deploy closes out.

(This heading has no content under it by design, and is not dead weight: it terminates the
`sed` range that prints `## Pending deploy` for the operator and for `/deploy-note`. See
`test_deployed_history_heading_pins_the_live_section_boundary`.)
