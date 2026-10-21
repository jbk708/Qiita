### Added

- **A step's QC check results can be persisted.** The `persist-qc-checks` action primitive
  reads a step's `qc_checks.json` and upserts its `qiita.prep_sample_qc_check` rows under
  the running ticket's producer. A re-run under the same producer replaces its row and
  returns an invalidated one to `current`. No workflow wires it yet.
