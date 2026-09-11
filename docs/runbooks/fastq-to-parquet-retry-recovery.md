# Reads-ingest retry recovery (fastq_to_parquet / bam_to_parquet / ingest_reads)

A reads job mints a `sequence_range` and **then** does its heavy durable write. The
window between the two is exactly where an OOM or walltime kill lands, which leaves an
orphaned range: the reads never reached the lake, but the sample's one-shot mint is
spent.

**This recovers itself now — there is almost nothing for an operator to do.**

Audience: operators and `system_admin`s.

## The common case: nothing to do

Every reads job pairs the mint with a read-back (`mint_or_reuse_sequence_range`). On a
409 it reads the existing range back and, **if its own ticket minted it**, reuses it and
carries on. So:

- The runner's in-place retry (including the OOM memory-escalation) recovers by itself.
- A ticket that ended `FAILED` is re-driven with `qiita ticket run <idx>` — same ticket,
  so the range is still its own, so the reuse applies.

No DB reads, no hand-built resubmission payload. (The old `pre_minted_range` input that
this page used to describe has been removed: it bypassed the ownership check below, and
nothing could set it — the step binder drops unknown `action_context` keys.)

## Re-ingesting reads that are already loaded

A prep_sample's reads are loaded once. Three checks refuse a second load, and each refusal
points here.

**At submission.** A `submit-bcl-convert` over a pool that already has a completed
bcl-convert ticket is refused with a 409:

> `a COMPLETED ticket already exists for this (sequenced_pool, action); its reads are
> already loaded, and a re-run's registration of them is refused.`

`--force` (wet_lab_admin or system_admin) lets that submission through, to be refused by
one of the checks below. A completed ticket does not block a prep_sample-scoped
submission, so PacBio ingest is not refused here.

**At the mint.** A reads job whose prep_sample already has a `sequence_range` minted by a
different ticket fails before it writes anything:

> `prep_sample N already has a sequence_range minted by work_ticket M, not by this one
> (work_ticket K) — its reads are already loaded`

The same refusal fires when the minter is unknown (`minted_by_work_ticket_idx IS NULL` —
a row the migration's backfill could not attribute unambiguously); read it the same way.
A job of the minting ticket itself is refused once that ticket is no longer in flight
(`… which is no longer in flight (state=…) — this attempt is stale`); a `failed` ticket
is re-driven with `qiita ticket run <idx>`, as above.

**At registration.** The data plane's `register_files` refuses `read` files for a
prep_sample whose reads another ticket registered, and moves none of the call's files:

> `work_ticket K: refusing to register reads another ticket already registered, for 1
> prep_sample(s); see docs/runbooks/fastq-to-parquet-retry-recovery.md — prep_sample N
> (registered by work_ticket M)`

A long list ends `and N more`.

Registering again under the same ticket — a redrive that re-runs the step that produced
the reads — replaces that ticket's rows instead of adding a second copy.

**`submit-bcl-convert --force` and `submit-pacbio-ingest` do not re-ingest.**

- `qiita submit-bcl-convert --force` over a completed pool re-uses each prep_sample's
  stored read copy without minting, and is refused at registration (at the mint instead,
  if a stored copy is gone).
- `qiita submit-pacbio-ingest`, with or without `--force`, submits a new bam-to-parquet
  ticket for each prep_sample in the pre-flight file that has no ticket in flight. For a
  prep_sample whose reads are loaded, that ticket is refused at the mint.

**To re-ingest anyway:**

- One prep_sample: there is no supported way. No route or CLI deletes a single
  prep_sample or its reads.
- A whole pool: `qiita delete-sequenced-pool --force` hard-deletes the pool and everything
  under it, including its reads in the lake and their stored copies. Without `--force`,
  the pool's completed tickets block the delete. Its `--help` lists what it removes and
  who can run it. Then resubmit.

To see which ticket minted a prep_sample's range:

```sql
SELECT sr.prep_sample_idx,
       sr.sequence_idx_start,
       sr.sequence_idx_stop,
       sr.minted_by_work_ticket_idx
  FROM qiita.sequence_range sr
 WHERE sr.prep_sample_idx = $PREP_SAMPLE_IDX;
```

## The other manual case: a width mismatch

> `… but its input now has N reads — the range must match the prior mint count exactly`

The range's width no longer matches the input's read count, which means the **input file
changed between attempts**. That is a data-integrity problem, not a retry problem:
inputs are required to be immutable between work_ticket submission and step execution.
Establish which file is correct before doing anything else. If the new file is the
intended one, the same options apply as in
[Re-ingesting reads that are already loaded](#re-ingesting-reads-that-are-already-loaded).

## Force-failing a stuck ticket

If a `pending`, `queued`, or `processing` ticket needs to be terminally failed (operator
triage, blocked-by-unrelated-bug, etc.), use `qiita-admin ticket force-fail` rather than
writing the UPDATE by hand. It mirrors the `work_ticket_failure_step_name_consistent`
CHECK constraint client-side and refuses to overwrite an already-terminal ticket:

```bash
# [admin] — DATABASE_URL sourced from /etc/qiita/control-plane.env
qiita-admin ticket force-fail \
    --idx 42 \
    --stage step_run \
    --step-name fastq \
    --reason "manual triage: stuck mid-step"
```

`--step-name` is required when `--stage=step_run` and rejected when `--stage` is
`submission` or `finalize`.

## Invariants preserved

- All identifiers are still minted exclusively by the control plane; a retry reuses the
  range its own attempt minted rather than allocating a new one.
- `qiita.sequence_range.UNIQUE(prep_sample_idx)` is never violated — the reuse path does
  not re-mint.
- The compute service account stays scope-minimal: the read-back
  (`GET /sequence-range/{idx}`) is deliberately gated on the same `sequence_range:mint`
  scope the mint uses, so no `prep_sample:read` grant is needed.
- The mint endpoint's contract is unchanged (still 409 on duplicate); what changed is
  that the *caller* now recovers from that 409 when — and only when — the range is its
  own.
