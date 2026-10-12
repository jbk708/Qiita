### Added

- **`negative control type` and `positive control type` biosample fields (#342).**
  `negative control type` takes the MIxS negative-control values plus `extraction blank`
  and `no-template control`; `positive control type` is free text.

### Changed

- **A control is now identified from sample metadata, ahead of `host taxon id` (#342).**
  A real value on either new field, or a control `qiita sample type` term
  (`control_blank`, `control_sterile_water`, `control_sea_water`, `control_spike_in`),
  resolves CONTROL whatever the sample's host. A missing-value reason does not count.
  `is_control_sample` (expected-empty wells) now also counts `negative control type`
  and the three negative sample-type terms; positive controls resolve CONTROL for host
  filtering but still read as failures on zero reads. `qiita-admin backfill host-taxon-id`
  writes `missing: control sample` for negative controls found in metadata too; #344
  sweeps those markers.
