"""GET /qc-check/{prep_sample_idx}: a prep_sample's QC check rows and verdict."""

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from qiita_common.api_paths import PATH_QC_CHECK_BY_PREP_SAMPLE, PATH_QC_CHECK_PREFIX
from qiita_common.auth_constants import Scope
from qiita_common.models import PrepSampleQcCheck, PrepSampleQcCheckResponse, QcVerdict

from ..auth.guards import COHORT_MIN_TIER, require_scope
from ..auth.principal import Principal
from ..deps import get_db_pool
from ..repositories.prep_sample import fetch_prep_sample_exists
from ..repositories.prep_sample_qc_check import (
    fetch_qc_checks_by_prep_sample_idx,
    fetch_qc_verdict_by_prep_sample_idx,
)
from ._helpers import authorize_prep_sample_cohort

router = APIRouter(prefix=PATH_QC_CHECK_PREFIX, tags=["qc-check"])


@router.get(PATH_QC_CHECK_BY_PREP_SAMPLE)
async def get_prep_sample_qc_check_route(
    prep_sample_idx: int,
    pool: asyncpg.Pool = Depends(get_db_pool),
    caller: Principal = Depends(require_scope(Scope.PREP_SAMPLE_READ)),
) -> PrepSampleQcCheckResponse:
    """Return the prep_sample's check rows and verdict; gated as `get_sequence_range_route`.

    The existence check follows the gate, so a caller without access gets 403 whether or
    not the prep_sample exists; admins skip the gate and get 404 for a missing one.
    """
    await authorize_prep_sample_cohort(
        pool, caller=caller, prep_sample_idx=[prep_sample_idx], min_tier=COHORT_MIN_TIER
    )
    if not await fetch_prep_sample_exists(pool, prep_sample_idx):
        raise HTTPException(status_code=404, detail=f"prep_sample_idx {prep_sample_idx} not found")
    rows = await fetch_qc_checks_by_prep_sample_idx(pool, prep_sample_idx)
    verdict = await fetch_qc_verdict_by_prep_sample_idx(pool, prep_sample_idx)
    return PrepSampleQcCheckResponse(
        prep_sample_idx=prep_sample_idx,
        verdict=None
        if verdict is None
        else QcVerdict(severity=verdict["severity"], check=verdict["check"]),
        checks=[
            PrepSampleQcCheck(
                check=r["check"],
                severity=r["severity"],
                reason=r["reason"],
                mask_idx=r["mask_idx"],
                alignment_idx=r["alignment_idx"],
                measured_value=r["measured_value"],
                threshold=r["threshold"],
                work_ticket_idx=r["work_ticket_idx"],
                created_at=r["created_at"],
                state=r["state"],
                invalidated_at=r["invalidated_at"],
                invalidation_reason=r["invalidation_reason"],
            )
            for r in rows
        ],
    )
