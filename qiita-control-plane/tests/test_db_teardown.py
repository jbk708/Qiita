"""The ordered sweep that tears down a study / biosample / prep_sample graph.

These pin what the sweep promises: everything hanging off the entities goes,
including rows the caller never recorded; what belongs to nobody else is left
alone; and a table missing from the sweep list is named rather than surfacing
later as a foreign-key violation.
"""

import pytest
from qiita_common.models import GenomeSource

from qiita_control_plane.testing.db_seeds import (
    delete_idxs,
    seed_bare_feature,
    seed_biosample_with_sequenced_prep_sample,
    seed_feature_genome,
    seed_genome,
    seed_sequenced_sample_subtype,
    seed_study,
    seed_user_principal,
)
from qiita_control_plane.testing.db_teardown import (
    SWEEP_TIERS,
    UNSWEPT_ENTITY_TABLES,
    EntityGraphNotSweptError,
    assert_entity_graph_swept,
    delete_principal,
    teardown_entity_graph,
)

pytestmark = pytest.mark.db

# The entity tables are deleted after the sweep rather than by it, so they are
# expected to carry their own idx column without appearing in SWEEP_TIERS.
_ENTITY_TABLE_NAMES = frozenset({"study", "biosample", "prep_sample"})

_ENTITY_KEY_COLUMN_NAMES = ("study_idx", "biosample_idx", "prep_sample_idx")


def _sweep_drift_message(drift: dict[str, list[str]]) -> str:
    return (
        f"SWEEP_TIERS has drifted from the qiita schema: {drift}."
        " A table carrying an entity idx must be added to the tier that deletes"
        " it before its parents, or named in UNSWEPT_ENTITY_TABLES with the"
        " reason the caller owns it; a table no longer in the schema must go."
    )


async def _count(pool, table, column, idxs) -> int:
    sql = f"SELECT count(*) FROM qiita.{table} WHERE {column} = ANY($1::bigint[])"
    return await pool.fetchval(sql, idxs)


@pytest.fixture
async def graph(postgres_pool):
    """A principal owning a study, a biosample, and a sequenced prep_sample.

    The teardown here is best-effort and deliberately tolerant: it runs whether
    or not the function under test did its job, so a failing run leaves nothing
    behind for the rest of the session.
    """
    principal_idx = await seed_user_principal(postgres_pool, prefix="teardown", suffix="probe")
    study_idx = await seed_study(postgres_pool, owner_idx=principal_idx, title="teardown probe")
    biosample_idx, prep_sample_idx = await seed_biosample_with_sequenced_prep_sample(
        postgres_pool, owner_idx=principal_idx
    )
    # The subtype chain gives the prep_sample a sequenced_sample the caller never
    # names, and a run and pool that belong to the caller rather than the sweep.
    run_idx, pool_idx, _ss_idx = await seed_sequenced_sample_subtype(
        postgres_pool,
        prep_sample_idx=prep_sample_idx,
        owner_idx=principal_idx,
        sequenced_pool_item_id="A1",
    )
    yield {
        "principal_idx": principal_idx,
        "study_idx": study_idx,
        "biosample_idx": biosample_idx,
        "prep_sample_idx": prep_sample_idx,
    }

    # The genome-derived rows go first: the tier replay below deletes
    # qiita.genome, which they reference, and this runs after a failed test as
    # readily as a passing one.
    for table in ("feature_genome", "exported_feature"):
        await postgres_pool.execute(
            f"DELETE FROM qiita.{table} WHERE genome_idx IN"
            " (SELECT genome_idx FROM qiita.genome WHERE prep_sample_idx = $1)",
            prep_sample_idx,
        )
    for tier in SWEEP_TIERS:
        for table, keys in tier:
            for column, _key in keys:
                if column == "genome_idx":
                    continue
                await postgres_pool.execute(
                    f"DELETE FROM qiita.{table} WHERE {column} = ANY($1::bigint[])",
                    [study_idx, biosample_idx, prep_sample_idx],
                )
    await postgres_pool.execute(
        "DELETE FROM qiita.genome WHERE prep_sample_idx = $1", prep_sample_idx
    )
    await delete_idxs(postgres_pool, "prep_sample", prep_sample_idx)
    await delete_idxs(postgres_pool, "biosample", biosample_idx)
    await delete_idxs(postgres_pool, "study", study_idx)
    await delete_idxs(postgres_pool, "sequenced_pool", pool_idx)
    await delete_idxs(postgres_pool, "sequencing_run", run_idx)
    await postgres_pool.execute("DELETE FROM qiita.user WHERE principal_idx = $1", principal_idx)
    await delete_idxs(postgres_pool, "principal", principal_idx)


async def test_teardown_entity_graph_sweeps_the_whole_graph(postgres_pool, graph):
    """Tests the case where entities carrying children are torn down.

    `seed_biosample_with_sequenced_prep_sample` writes a sequenced_sample the
    caller never names, which is the kind of row the sweep exists to catch.
    """
    await teardown_entity_graph(
        postgres_pool,
        study_idxs=[graph["study_idx"]],
        biosample_idxs=[graph["biosample_idx"]],
        prep_sample_idxs=[graph["prep_sample_idx"]],
    )

    survivors = {
        "sequenced_sample": await _count(
            postgres_pool, "sequenced_sample", "prep_sample_idx", [graph["prep_sample_idx"]]
        ),
        "prep_sample": await _count(
            postgres_pool, "prep_sample", "idx", [graph["prep_sample_idx"]]
        ),
        "biosample": await _count(postgres_pool, "biosample", "idx", [graph["biosample_idx"]]),
        "study": await _count(postgres_pool, "study", "idx", [graph["study_idx"]]),
    }
    assert survivors == {"sequenced_sample": 0, "prep_sample": 0, "biosample": 0, "study": 0}


async def test_teardown_entity_graph_sweeps_an_assembly_derived_genome(postgres_pool, graph):
    """Tests the case where a prep_sample has produced a genome.

    The genome references the prep_sample under RESTRICT, so leaving it would
    block the prep_sample delete; its feature_genome rows have to go first.
    """
    genome_idx, _source_id = await seed_genome(
        postgres_pool, source=GenomeSource.QIITA, prep_sample_idx=graph["prep_sample_idx"]
    )
    feature_idx = await seed_bare_feature(postgres_pool)
    await seed_feature_genome(postgres_pool, feature_idx=feature_idx, genome_idx=genome_idx)

    await teardown_entity_graph(
        postgres_pool,
        study_idxs=[graph["study_idx"]],
        biosample_idxs=[graph["biosample_idx"]],
        prep_sample_idxs=[graph["prep_sample_idx"]],
    )

    survivors = {
        "feature_genome": await _count(postgres_pool, "feature_genome", "genome_idx", [genome_idx]),
        "genome": await _count(postgres_pool, "genome", "genome_idx", [genome_idx]),
    }
    assert survivors == {"feature_genome": 0, "genome": 0}
    await postgres_pool.execute("DELETE FROM qiita.feature WHERE feature_idx = $1", feature_idx)


async def test_teardown_entity_graph_leaves_an_external_genome(postgres_pool, graph):
    """Tests the case where an unrelated reference genome shares the database.

    An external genome carries no prep_sample_idx, so nothing about it is in
    range and the sweep must not widen to it.
    """
    external_idx, _source_id = await seed_genome(postgres_pool, source=GenomeSource.REFSEQ)

    await teardown_entity_graph(
        postgres_pool,
        study_idxs=[graph["study_idx"]],
        biosample_idxs=[graph["biosample_idx"]],
        prep_sample_idxs=[graph["prep_sample_idx"]],
    )

    assert await _count(postgres_pool, "genome", "genome_idx", [external_idx]) == 1
    await postgres_pool.execute("DELETE FROM qiita.genome WHERE genome_idx = $1", external_idx)


async def test_assert_entity_graph_swept_names_the_table_the_sweep_missed(postgres_pool, graph):
    """Tests the case where a table carrying an entity idx is not in the sweep list.

    This is what a schema gaining a table nobody added here looks like, and it
    has to fail naming that table rather than as an FK violation three
    statements later.
    """
    with pytest.raises(EntityGraphNotSweptError, match="sequenced_sample"):
        await assert_entity_graph_swept(
            postgres_pool,
            study_idxs=[],
            biosample_idxs=[],
            prep_sample_idxs=[graph["prep_sample_idx"]],
        )


async def test_teardown_entity_graph_accepts_empty_lists(postgres_pool):
    """Tests the case where a caller created none of some entity kind."""
    await teardown_entity_graph(
        postgres_pool, study_idxs=[], biosample_idxs=[], prep_sample_idxs=[]
    )


async def test_delete_principal_removes_the_user_row_first(postgres_pool):
    """Tests the case where a principal with a user row is deleted.

    qiita.user references qiita.principal under RESTRICT, so the order is the
    whole content of the function.
    """
    principal_idx = await seed_user_principal(
        postgres_pool, prefix="teardown-principal", suffix="probe"
    )

    await delete_principal(postgres_pool, principal_idx)

    survivors = {
        "user": await postgres_pool.fetchval(
            "SELECT count(*) FROM qiita.user WHERE principal_idx = $1", principal_idx
        ),
        "principal": await _count(postgres_pool, "principal", "idx", [principal_idx]),
    }
    assert survivors == {"user": 0, "principal": 0}


async def test_sweep_tiers_matches_the_live_schema(postgres_pool):
    """Tests the case where the schema and the sweep list have moved apart.

    `assert_entity_graph_swept` only names a forgotten table once some test
    seeds a row into it. This fails on the schema alone, so a table added to a
    migration is caught whether or not anything exercises it yet.
    """
    entity_keyed = await postgres_pool.fetch(
        "SELECT DISTINCT c.table_name FROM information_schema.columns c"
        "  JOIN information_schema.tables t"
        "    ON t.table_schema = c.table_schema AND t.table_name = c.table_name"
        " WHERE c.table_schema = 'qiita' AND t.table_type = 'BASE TABLE'"
        "   AND c.column_name = ANY($1::text[])",
        list(_ENTITY_KEY_COLUMN_NAMES),
    )
    all_tables = await postgres_pool.fetch(
        "SELECT table_name FROM information_schema.tables"
        " WHERE table_schema = 'qiita' AND table_type = 'BASE TABLE'"
    )

    swept = {table for tier in SWEEP_TIERS for table, _keys in tier}
    accounted_for = swept | _ENTITY_TABLE_NAMES | UNSWEPT_ENTITY_TABLES
    drift = {
        "missing_from_sweep": sorted({row["table_name"] for row in entity_keyed} - accounted_for),
        "absent_from_schema": sorted(swept - {row["table_name"] for row in all_tables}),
    }

    expected = {"missing_from_sweep": [], "absent_from_schema": []}
    assert drift == expected, _sweep_drift_message(drift)


async def test_assert_entity_graph_swept_names_a_missed_genome_table(postgres_pool, graph):
    """Tests the case where a table hanging off a genome survives the sweep.

    `exported_feature` clears its genome_idx on delete rather than refusing, so
    a row left behind here is never surfaced by a foreign-key violation later.
    The genomes have to be named explicitly: the sweep deletes qiita.genome
    before this runs, so they cannot be found by walking back from the
    prep_sample.
    """
    genome_idx, _source_id = await seed_genome(
        postgres_pool, source=GenomeSource.QIITA, prep_sample_idx=graph["prep_sample_idx"]
    )
    feature_idx = await seed_bare_feature(postgres_pool)
    await seed_feature_genome(postgres_pool, feature_idx=feature_idx, genome_idx=genome_idx)

    with pytest.raises(EntityGraphNotSweptError, match="feature_genome"):
        await assert_entity_graph_swept(
            postgres_pool,
            study_idxs=[],
            biosample_idxs=[],
            prep_sample_idxs=[],
            genome_idxs=[genome_idx],
        )

    await postgres_pool.execute(
        "DELETE FROM qiita.feature_genome WHERE genome_idx = $1", genome_idx
    )
    await postgres_pool.execute("DELETE FROM qiita.genome WHERE genome_idx = $1", genome_idx)
    await postgres_pool.execute("DELETE FROM qiita.feature WHERE feature_idx = $1", feature_idx)
