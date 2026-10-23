### Changed

- **ENA `neg_cont_type` and `pos_cont_type` attributes never mark a biosample as a control (#694).** They stay study-local text on every sample; set `qiita sample type` to a `control_*` term to mark a real control. Documented in the ENA import runbook and pinned by tests.
