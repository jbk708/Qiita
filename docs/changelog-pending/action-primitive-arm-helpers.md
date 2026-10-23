### Changed
- **The runner's action-primitive arms share one scope guard and one capped-list helper (#749).** The scope check and the "and N more" truncation in `persist_qc_checks` and the barcode-roster check were each written inline per arm; they now go through `_require_scope` and `capped`. No behavior change: every message is unchanged.
