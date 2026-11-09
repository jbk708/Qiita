"""Schema-level invariants for `qiita.exported_identifier`.

The route tests cover who may mint and what comes back. These cover the
guarantees that live in the database itself, because they are the ones a caller
cannot reach and a future migration could quietly drop:

* `export_id` is authored by Postgres and unwritable, so a public identifier
  cannot be forged, cannot drift from `idx`, and cannot be edited after
  publication.
* Exactly one processing identifier on a live row (`num_nonnulls`), which is the
  constraint a new processing type must extend.
* An identifier OUTLIVES the processing it names: deleting an alignment_definition
  row detaches and retires it instead of deleting it. The trigger that does this
  is what makes the FK's `ON DELETE SET NULL` satisfy the check at all, so without
  it that delete fails outright.
* Retirement columns cannot disagree with each other.
"""

import hashlib
import uuid

import asyncpg
import pytest

from qiita_control_plane.testing.db_seeds import (
    seed_biosample_with_sequenced_prep_sample,
    seed_user_principal,
)
from qiita_control_plane.testing.db_teardown import delete_principal, teardown_entity_graph

pytestmark = pytest.mark.db


async def _seed(postgres_pool, *, state="completed"):
    """An alignment, a prep_sample, and their gate row.

    Reuses the shared `seed_biosample_with_sequenced_prep_sample` rather than
    hand-writing the two INSERTs — those columns are not this test's business, and
    a local copy is how a schema test comes to disagree with the schema.
    """
    # A user-kind principal: qiita.biosample.owner_idx is guarded by a role-typed
    # FK trigger that rejects a service account.
    principal_idx = await seed_user_principal(
        postgres_pool, prefix="expid-schema", suffix=str(uuid.uuid4())[:8]
    )
    alignment_idx = await postgres_pool.fetchval(
        "SELECT (qiita.mint_alignment_definition($1, $2, $3)).alignment_idx",
        hashlib.sha256(f"exported-identifier-probe-{uuid.uuid4()}".encode()).digest(),
        '{"probe": "exported_identifier"}',
        principal_idx,
    )
    biosample_idx, prep_sample_idx = await seed_biosample_with_sequenced_prep_sample(
        postgres_pool, owner_idx=principal_idx
    )
    await postgres_pool.execute(
        "INSERT INTO qiita.alignment_sample (alignment_idx, prep_sample_idx, state)"
        " VALUES ($1, $2, $3)",
        alignment_idx,
        prep_sample_idx,
        state,
    )
    return alignment_idx, prep_sample_idx, biosample_idx, principal_idx


async def _cleanup(postgres_pool, alignment_idx, prep_sample_idx, biosample_idx, principal_idx):
    await teardown_entity_graph(
        postgres_pool,
        study_idxs=[],
        biosample_idxs=[biosample_idx],
        prep_sample_idxs=[prep_sample_idx],
    )
    await postgres_pool.execute(
        "DELETE FROM qiita.alignment_definition WHERE alignment_idx = $1", alignment_idx
    )
    await delete_principal(postgres_pool, [principal_idx])


@pytest.fixture
async def probe(postgres_pool):
    alignment_idx, prep_sample_idx, biosample_idx, principal_idx = await _seed(postgres_pool)
    yield {
        "alignment_idx": alignment_idx,
        "prep_sample_idx": prep_sample_idx,
        "principal_idx": principal_idx,
    }
    await _cleanup(postgres_pool, alignment_idx, prep_sample_idx, biosample_idx, principal_idx)


async def _mint(postgres_pool, probe) -> asyncpg.Record:
    return await postgres_pool.fetchrow(
        "INSERT INTO qiita.exported_identifier"
        "       (alignment_idx, prep_sample_idx, created_by_idx)"
        " VALUES ($1, $2, $3) RETURNING idx, export_id, retired",
        probe["alignment_idx"],
        probe["prep_sample_idx"],
        probe["principal_idx"],
    )


async def test_export_id_is_qm_prefixed_and_tracks_idx(postgres_pool, probe):
    row = await _mint(postgres_pool, probe)
    assert row["export_id"] == f"QM{row['idx']}"
    assert row["retired"] is False


async def test_export_id_cannot_be_supplied(postgres_pool, probe):
    """GENERATED ALWAYS, so the database refuses a caller-authored value outright —
    the guarantee that no code path anywhere can forge a public identifier."""
    with pytest.raises(asyncpg.PostgresError, match="generated column"):
        await postgres_pool.execute(
            "INSERT INTO qiita.exported_identifier"
            "       (export_id, alignment_idx, prep_sample_idx, created_by_idx)"
            " VALUES ('QMforged', $1, $2, $3)",
            probe["alignment_idx"],
            probe["prep_sample_idx"],
            probe["principal_idx"],
        )


async def test_export_id_cannot_be_edited_after_publication(postgres_pool, probe):
    await _mint(postgres_pool, probe)
    with pytest.raises(asyncpg.PostgresError, match="generated column"):
        await postgres_pool.execute(
            "UPDATE qiita.exported_identifier SET export_id = 'QMedited'"
            " WHERE prep_sample_idx = $1",
            probe["prep_sample_idx"],
        )


async def test_one_live_identifier_per_processed_sample(postgres_pool, probe):
    """The partial unique index is what makes the mint idempotent — a second live
    row for the same processed sample is what "stable identifier" forbids."""
    await _mint(postgres_pool, probe)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _mint(postgres_pool, probe)


async def test_a_live_row_needs_exactly_one_processing(postgres_pool, probe):
    """The constraint a new processing type must extend. Today that means
    alignment_idx cannot be NULL on a live row."""
    with pytest.raises(asyncpg.CheckViolationError, match="one_processing"):
        await postgres_pool.execute(
            "INSERT INTO qiita.exported_identifier (prep_sample_idx, created_by_idx)"
            " VALUES ($1, $2)",
            probe["prep_sample_idx"],
            probe["principal_idx"],
        )


async def test_deleting_the_alignment_retires_the_identifier_rather_than_deleting_it(
    postgres_pool, probe
):
    """The whole reason retirement exists. A published citation must keep resolving
    after the alignment behind it is deleted, and must say what happened.

    This also proves the delete SUCCEEDS: without the retire-on-detach trigger the
    FK's SET NULL would leave a live row with no processing, the one_processing
    check would reject it, and DELETEing the alignment would fail.
    """
    minted = await _mint(postgres_pool, probe)

    await postgres_pool.execute(
        "DELETE FROM qiita.alignment_definition WHERE alignment_idx = $1",
        probe["alignment_idx"],
    )

    row = await postgres_pool.fetchrow(
        "SELECT export_id, alignment_idx, retired, retired_at, retire_reason"
        "  FROM qiita.exported_identifier WHERE idx = $1",
        minted["idx"],
    )
    assert row is not None, "a published identifier was deleted with its alignment"
    assert row["export_id"] == minted["export_id"]
    assert row["alignment_idx"] is None
    assert row["retired"] is True
    assert row["retired_at"] is not None
    # The reason keeps the alignment_idx the column no longer can.
    assert row["retire_reason"] == (
        f"alignment_definition {probe['alignment_idx']} was deleted; the processing"
        " this identifier named no longer exists"
    )


async def test_a_retired_tuple_can_be_reminted(postgres_pool, probe):
    """The unique index is partial on `NOT retired` precisely so a retired-then-
    re-exported sample gets a FRESH identifier instead of colliding with its own
    history. Re-pointing the old one would silently change what a citation means."""
    first = await _mint(postgres_pool, probe)
    await postgres_pool.execute(
        "UPDATE qiita.exported_identifier"
        "   SET retired = true, retired_at = now(), retire_reason = 'test'"
        " WHERE idx = $1",
        first["idx"],
    )

    second = await _mint(postgres_pool, probe)
    assert second["export_id"] != first["export_id"]


async def test_retirement_columns_cannot_disagree(postgres_pool, probe):
    """`retired` without a timestamp and a reason is a row nobody can interpret."""
    minted = await _mint(postgres_pool, probe)
    with pytest.raises(asyncpg.CheckViolationError, match="retirement_consistent"):
        await postgres_pool.execute(
            "UPDATE qiita.exported_identifier SET retired = true WHERE idx = $1",
            minted["idx"],
        )


async def test_a_sample_with_a_published_identifier_cannot_be_hard_deleted(postgres_pool, probe):
    """RESTRICT, not CASCADE: a published handle must not disappear because someone
    removed the sample row underneath it."""
    await _mint(postgres_pool, probe)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await postgres_pool.execute(
            "DELETE FROM qiita.prep_sample WHERE idx = $1", probe["prep_sample_idx"]
        )


async def test_deleting_the_alignment_does_not_give_the_alignment_idx_back(postgres_pool, probe):
    """Pins WHY a deleted alignment_definition is gone for good as far as an
    identifier is concerned, since the reasoning is easy to get backwards:
    `mint_alignment_definition` dedups on params_hash, so one might expect re-minting
    the same config to recover the same alignment_idx. It does not — alignment_idx
    is GENERATED ALWAYS AS IDENTITY and the row is gone, so Postgres issues a fresh
    value and the re-aligned data is a genuinely new processed sample.
    """
    params_hash = await postgres_pool.fetchval(
        "SELECT params_hash FROM qiita.alignment_definition WHERE alignment_idx = $1",
        probe["alignment_idx"],
    )
    await postgres_pool.execute(
        "DELETE FROM qiita.alignment_definition WHERE alignment_idx = $1",
        probe["alignment_idx"],
    )
    reminted = await postgres_pool.fetchval(
        "SELECT (qiita.mint_alignment_definition($1, $2, $3)).alignment_idx",
        params_hash,
        '{"probe": "exported_identifier"}',
        probe["principal_idx"],
    )
    assert reminted != probe["alignment_idx"]
    await postgres_pool.execute(
        "DELETE FROM qiita.alignment_definition WHERE alignment_idx = $1", reminted
    )


def test_missing_from_reports_the_gap():
    """The mint's all-or-nothing guard. Exercised directly because the only thing
    that makes it fire — an alignment_definition row deleted between the INSERT and the
    SELECT of one READ COMMITTED transaction — cannot be staged deterministically
    without racing two connections, and the promise it protects (every requested sample
    is present or the request fails) is the response's headline claim."""
    from qiita_control_plane.repositories.exported_identifier import _missing_from

    rows = [{"prep_sample_idx": 4}, {"prep_sample_idx": 9}]
    assert _missing_from(rows, [4, 9]) == []
    assert _missing_from(rows, [4, 7, 9, 2]) == [2, 7]
    assert _missing_from([], [5]) == [5]
    # Deduped input must not report a phantom gap.
    assert _missing_from(rows, [4, 4, 9]) == []


# ---------------------------------------------------------------------------
# The processing kind (amplicon): (processing_idx, prep_sample_idx)
# ---------------------------------------------------------------------------


@pytest.fixture
async def processing_probe(postgres_pool, probe):
    """A qiita.processing run beside `probe`'s alignment, on the same sample."""
    from qiita_control_plane.repositories.processing import mint_processing

    async with postgres_pool.acquire() as conn:
        row = await mint_processing(
            conn,
            workflow="amplicon",
            version="1.1.0",
            params={"workflow": "amplicon", "version": "1.1.0", "probe": str(uuid.uuid4())},
        )
    processing_idx = row["processing_idx"]
    yield {**probe, "processing_idx": processing_idx}
    await postgres_pool.execute(
        "DELETE FROM qiita.exported_identifier WHERE prep_sample_idx = $1",
        probe["prep_sample_idx"],
    )
    await postgres_pool.execute(
        "DELETE FROM qiita.processing WHERE processing_idx = $1", processing_idx
    )


async def _mint_processing_kind(postgres_pool, p) -> asyncpg.Record:
    return await postgres_pool.fetchrow(
        "INSERT INTO qiita.exported_identifier"
        "       (processing_idx, prep_sample_idx, created_by_idx)"
        " VALUES ($1, $2, $3) RETURNING idx, export_id, retired",
        p["processing_idx"],
        p["prep_sample_idx"],
        p["principal_idx"],
    )


async def test_a_processing_run_names_a_processed_sample(postgres_pool, processing_probe):
    row = await _mint_processing_kind(postgres_pool, processing_probe)
    assert row["export_id"] == f"QM{row['idx']}"


async def test_alignment_and_processing_on_one_row_is_refused(postgres_pool, processing_probe):
    with pytest.raises(asyncpg.CheckViolationError, match="one_processing"):
        await postgres_pool.execute(
            "INSERT INTO qiita.exported_identifier"
            "       (alignment_idx, processing_idx, prep_sample_idx, created_by_idx)"
            " VALUES ($1, $2, $3, $4)",
            processing_probe["alignment_idx"],
            processing_probe["processing_idx"],
            processing_probe["prep_sample_idx"],
            processing_probe["principal_idx"],
        )


async def test_one_live_identifier_per_processing_sample(postgres_pool, processing_probe):
    """The processing kind's own partial index: the alignment one cannot cover a
    row whose alignment_idx is NULL."""
    await _mint_processing_kind(postgres_pool, processing_probe)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _mint_processing_kind(postgres_pool, processing_probe)


async def test_both_kinds_coexist_for_one_sample(postgres_pool, processing_probe):
    """One sample aligned and denoised is two processed samples, two identifiers."""
    a = await _mint(postgres_pool, processing_probe)
    b = await _mint_processing_kind(postgres_pool, processing_probe)
    assert a["export_id"] != b["export_id"]


async def test_deleting_the_processing_retires_the_identifier(postgres_pool, processing_probe):
    row = await _mint_processing_kind(postgres_pool, processing_probe)
    await postgres_pool.execute(
        "DELETE FROM qiita.processing WHERE processing_idx = $1",
        processing_probe["processing_idx"],
    )
    after = await postgres_pool.fetchrow(
        "SELECT export_id, processing_idx, retired, retire_reason"
        " FROM qiita.exported_identifier WHERE idx = $1",
        row["idx"],
    )
    assert after["export_id"] == row["export_id"]
    assert after["processing_idx"] is None
    assert after["retired"] is True
    assert f"processing {processing_probe['processing_idx']} was deleted" in after["retire_reason"]
