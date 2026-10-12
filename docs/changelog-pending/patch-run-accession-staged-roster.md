### Fixed

- **`PATCH /sequenced-sample/{idx}` refuses a changed `ena_run_accession` once the download could not pick it up (#626).**
  The change is refused with a 409 when the pool's latest `download-ena-study` ticket has read its run
  roster, when a failed ticket still has a live download job, or when the sample's reads are already
  stored (the download reuses them rather than fetching the new run). The 409 names the remedy: retire
  the sample and add the corrected run to a new `sequenced_pool`. A body that includes `ena_run_accession`
  now takes the sequencing_run lock and answers 503 with `Retry-After` if an import holds it.
