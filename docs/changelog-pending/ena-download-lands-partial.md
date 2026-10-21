### Changed

- **A pool with some permanently failed runs now lands the rest (#731).** When a `download-ena-study`
  ticket's only remaining failures are permanent (a format error, a run with no sequence data),
  `ingest_ena_reads` stores every other run, and the new `persist-ena-run-outcomes` step records
  each failed run's reason and failure type on its `sequenced_sample`. The batch item reads
  `completed_partial`, and each failed run shows in `ena_runs` as `download_failed` with its
  `failure_reason` and `failure_type`. `qiita submit-ena-import --watch` exits 1 on a
  `completed_partial` item. The ticket is COMPLETED, so the submitter gets the normal completion email. A pool with a
  transient failure still retries first, and one where no run was stored still fails, as does
  any error not recognised as a bad run (such as out of memory).
