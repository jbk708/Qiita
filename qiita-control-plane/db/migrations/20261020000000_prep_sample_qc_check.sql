-- Per-prep_sample QC check results and the verdict view over them.

-- migrate:up

-- FKs cascade: these rows are derived receipts and must not block a pool
-- force-delete or a mask/alignment delete.
CREATE TABLE qiita.prep_sample_qc_check (
    idx                 BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    prep_sample_idx     BIGINT NOT NULL REFERENCES qiita.prep_sample(idx) ON DELETE CASCADE,
    mask_idx            BIGINT REFERENCES qiita.mask_definition(mask_idx) ON DELETE CASCADE,
    alignment_idx       BIGINT REFERENCES qiita.alignment_definition(alignment_idx)
                            ON DELETE CASCADE,
    "check"             TEXT NOT NULL,
    severity            TEXT NOT NULL,
    reason              TEXT,
    measured_value      DOUBLE PRECISION,
    threshold           DOUBLE PRECISION,
    work_ticket_idx     BIGINT NOT NULL
                            REFERENCES qiita.work_ticket(work_ticket_idx) ON DELETE CASCADE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    state               TEXT NOT NULL DEFAULT 'current',
    invalidated_at      TIMESTAMPTZ,
    invalidated_by_idx  BIGINT REFERENCES qiita.principal(idx) ON DELETE RESTRICT,
    invalidation_reason TEXT,

    CONSTRAINT prep_sample_qc_check_check_values CHECK ("check" IN (
        'data_type', 'composition', 'host_fraction', 'read_retention',
        'amplicon_region', 'orientation', 'source_tracker', 'classifier'
    )),
    CONSTRAINT prep_sample_qc_check_severity_values CHECK (
        severity IN ('info', 'warning', 'fail')
    ),
    CONSTRAINT prep_sample_qc_check_state_values CHECK (state IN ('current', 'invalidated')),
    CONSTRAINT prep_sample_qc_check_reason_format CHECK (reason ~ '^[a-z][a-z0-9_]*$'),
    CONSTRAINT prep_sample_qc_check_one_producer CHECK (num_nonnulls(mask_idx, alignment_idx) <= 1),
    CONSTRAINT prep_sample_qc_check_invalidation_fields CHECK (
        (state = 'invalidated')
            = (invalidated_at IS NOT NULL
               AND invalidated_by_idx IS NOT NULL
               AND invalidation_reason IS NOT NULL)
    ),
    CONSTRAINT prep_sample_qc_check_invalidation_reason_nonblank CHECK (
        invalidation_reason IS NULL OR btrim(invalidation_reason) <> ''
    )
);

-- NULLS NOT DISTINCT is safe here, unlike on exported_identifier, because "check"
-- is part of the key.
CREATE UNIQUE INDEX prep_sample_qc_check_key
    ON qiita.prep_sample_qc_check (prep_sample_idx, "check", mask_idx, alignment_idx)
    NULLS NOT DISTINCT;

COMMENT ON TABLE qiita.prep_sample_qc_check IS
    'One QC check result per (prep_sample, check, producer); the producer is a '
    'mask, an alignment or neither. check, severity and state are TEXT + CHECK, '
    'not Postgres ENUMs; Python twins qiita_common.models.QcCheck, QcSeverity and '
    'QcCheckState. reason codes live in qiita_common/data/qc_reasons.yml, not in a '
    'CHECK. A re-run is an upsert and must reset state and the three invalidated_* '
    'fields, which clears an earlier invalidation in place.';

CREATE VIEW qiita.qc_verdict AS
SELECT DISTINCT ON (c.prep_sample_idx)
       c.prep_sample_idx, c.severity, c."check"
  FROM qiita.prep_sample_qc_check c
  LEFT JOIN qiita.mask_definition md ON md.mask_idx = c.mask_idx
  LEFT JOIN qiita.mask_sample ms
    ON ms.mask_idx = c.mask_idx AND ms.prep_sample_idx = c.prep_sample_idx
 WHERE c.state = 'current'
   AND (c.mask_idx IS NULL OR (md.status = 'active' AND ms.state = 'completed'))
 ORDER BY c.prep_sample_idx,
          CASE c.severity WHEN 'fail' THEN 3 WHEN 'warning' THEN 2 ELSE 1 END DESC,
          c."check", c.idx;

COMMENT ON VIEW qiita.qc_verdict IS
    'Worst severity per prep_sample among the check rows that count, with the '
    'check that set it (ties go to the first "check", then idx). A row counts when '
    'state is current and, for a mask row, the mask is active and its mask_sample '
    'is completed; a mask row with no mask_sample row does not count. Python twin: '
    'qiita_common.models.QcVerdict.';

-- migrate:down

DROP VIEW qiita.qc_verdict;
DROP TABLE qiita.prep_sample_qc_check;
