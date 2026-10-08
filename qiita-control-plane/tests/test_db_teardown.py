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
    _ENTITY_TABLES,
    _GENOME_KEY_COLUMN,
    BIOSAMPLE,
    PREP_SAMPLE,
    STUDY,
    SWEEP_TIERS,
    UNSWEPT_ENTITY_TABLES,
    EntityGraphNotSweptError,
    _entity_keyed_candidates,
    _sweep_table,
    assert_entity_graph_swept,
    delete_principal,
    teardown_entity_graph,
)


def _sweep_drift_message(drift: dict[str, list[str]]) -> str:
    return (
        f"SWEEP_TIERS has drifted from the qiita schema: {drift}."
        " A table keyed on an entity or on a genome must be added to the tier"
        " that deletes it before its parents, or named in UNSWEPT_ENTITY_TABLES"
        " with the reason it is not swept; a swept table must name every such"
        " key it carries, since a row is matched on whichever one is in range;"
        " a table no longer in the schema must go."
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
    # Replaying SWEEP_TIERS here would miss whatever the function under test
    # misses; test_sweep_tiers_matches_the_live_schema is what closes that.
    # Each column is matched on its own entity, the way the function under test
    # does. Handing every column all three idxs would delete another test's rows
    # whenever the idxs collide, which they do: study.idx and prep_sample.idx
    # both restart at 25000, so the Nth of each carries the same number.
    seeded = {STUDY: study_idx, BIOSAMPLE: biosample_idx, PREP_SAMPLE: prep_sample_idx}
    for tier in SWEEP_TIERS:
        for table, keys in tier:
            for column, key in keys:
                if key not in seeded:
                    continue
                await postgres_pool.execute(
                    f"DELETE FROM qiita.{table} WHERE {column} = ANY($1::bigint[])",
                    [seeded[key]],
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


@pytest.mark.db
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


@pytest.mark.db
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


@pytest.mark.db
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


@pytest.mark.db
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


@pytest.mark.db
async def test_teardown_entity_graph_accepts_empty_lists(postgres_pool):
    """Tests the case where a caller created none of some entity kind."""
    await teardown_entity_graph(
        postgres_pool, study_idxs=[], biosample_idxs=[], prep_sample_idxs=[]
    )


@pytest.mark.db
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


@pytest.mark.db
async def test_sweep_tiers_matches_the_live_schema(postgres_pool):
    """Tests the case where the schema and the sweep list have moved apart.

    `assert_entity_graph_swept` only names a forgotten table once some test
    seeds a row into it. This fails on the schema alone, so a table added to a
    migration is caught whether or not anything exercises it yet. It walks the
    assertion's own candidate query, genome included, and checks both halves of
    an entry: that the table is swept, and that it names every key it carries.
    """
    entity_keyed = await _entity_keyed_candidates(postgres_pool)
    all_tables = await postgres_pool.fetch(
        "SELECT table_name FROM information_schema.tables"
        " WHERE table_schema = 'qiita' AND table_type = 'BASE TABLE'"
    )

    swept = {table for tier in SWEEP_TIERS for table, _keys in tier}
    accounted_for = swept | _ENTITY_TABLES | UNSWEPT_ENTITY_TABLES
    # A swept table must name every entity key it carries, not just one of them:
    # a row whose other key is out of range is matched on the key that is in it.
    declared_keys = {
        (table, column) for tier in SWEEP_TIERS for table, keys in tier for column, _key in keys
    }
    # qiita.genome carries genome_idx as its own primary key rather than as a
    # reference to a parent, so it is keyed on the prep_sample that made it.
    carried_keys = {
        (row["table_name"], row["column_name"])
        for row in entity_keyed
        if row["table_name"] in swept
        and (row["table_name"], row["column_name"]) != ("genome", _GENOME_KEY_COLUMN)
    }
    drift = {
        "missing_from_sweep": sorted({row["table_name"] for row in entity_keyed} - accounted_for),
        "absent_from_schema": sorted(swept - {row["table_name"] for row in all_tables}),
        "keys_not_declared": sorted(carried_keys - declared_keys),
    }

    expected = {"missing_from_sweep": [], "absent_from_schema": [], "keys_not_declared": []}
    assert drift == expected, _sweep_drift_message(drift)


@pytest.mark.db
async def test_assert_entity_graph_swept_names_a_missed_genome_table(postgres_pool, graph):
    """Tests the case where a table hanging off a genome survives the sweep.

    `exported_feature` clears its genome_idx on delete rather than refusing, so
    a row left behind here is never surfaced by a foreign-key violation later.
    The genomes have to be named explicitly, because a real teardown deletes
    qiita.genome before the check runs and they could not be found by walking
    back from the prep_sample. No sweep runs here, so the genome stands too, and
    the candidates are ordered by name, which is what settles that
    `feature_genome` is the survivor named first.
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


@pytest.mark.db
async def test_teardown_entity_graph_sweeps_a_link_row_by_either_side(postgres_pool, graph):
    """Tests the case where a link row names one entity in range and one outside it.

    biosample_to_study is keyed on both its sides, and a caller can hand over a
    biosample without the second study it is linked to. Matching any one key is
    what carries that row out; requiring every key would strand it, and the
    assertion would then name biosample_to_study rather than let the teardown
    reach its entity delete.
    """
    other_study_idx = await seed_study(
        postgres_pool, owner_idx=graph["principal_idx"], title="teardown probe unswept study"
    )
    try:
        await postgres_pool.execute(
            "INSERT INTO qiita.biosample_to_study (biosample_idx, study_idx, created_by_idx)"
            " VALUES ($1, $2, $3)",
            graph["biosample_idx"],
            other_study_idx,
            graph["principal_idx"],
        )

        await teardown_entity_graph(
            postgres_pool,
            study_idxs=[graph["study_idx"]],
            biosample_idxs=[graph["biosample_idx"]],
            prep_sample_idxs=[graph["prep_sample_idx"]],
        )

        survivors = {
            "biosample_to_study": await _count(
                postgres_pool, "biosample_to_study", "study_idx", [other_study_idx]
            ),
            "study": await _count(postgres_pool, "study", "idx", [other_study_idx]),
        }
        assert survivors == {"biosample_to_study": 0, "study": 1}
    finally:
        # The fixture's replay cannot reach this study, and its owner is the
        # principal that replay deletes under RESTRICT, so a failed assert here
        # would otherwise surface as a foreign-key error in teardown.
        await postgres_pool.execute(
            "DELETE FROM qiita.biosample_to_study WHERE study_idx = $1", other_study_idx
        )
        await delete_idxs(postgres_pool, "study", other_study_idx)


@pytest.mark.db
async def test_teardown_entity_graph_leaves_a_reference_exclusion(postgres_pool, graph):
    """Tests the case where an exclusion names a genome the teardown deletes.

    An exclusion carries its genome as a bare BIGINT and is meant to outlive it,
    so the assertion has to pass over the table rather than report the row as a
    survivor and refuse the teardown.
    """
    genome_idx, _source_id = await seed_genome(
        postgres_pool, source=GenomeSource.QIITA, prep_sample_idx=graph["prep_sample_idx"]
    )
    exclusion_idx = await postgres_pool.fetchval(
        "INSERT INTO qiita.reference_exclusion (genome_idx, reason, excluded_by_idx)"
        " VALUES ($1, $2, $3) RETURNING reference_exclusion_idx",
        genome_idx,
        "teardown probe",
        graph["principal_idx"],
    )

    try:
        await teardown_entity_graph(
            postgres_pool,
            study_idxs=[graph["study_idx"]],
            biosample_idxs=[graph["biosample_idx"]],
            prep_sample_idxs=[graph["prep_sample_idx"]],
        )

        survivors = {
            "reference_exclusion": await _count(
                postgres_pool, "reference_exclusion", "genome_idx", [genome_idx]
            ),
            "genome": await _count(postgres_pool, "genome", "genome_idx", [genome_idx]),
        }
        assert survivors == {"reference_exclusion": 1, "genome": 0}
    finally:
        # The exclusion is deliberately unswept and references the principal
        # under RESTRICT, so leaving it would fail the fixture's own teardown.
        await postgres_pool.execute(
            "DELETE FROM qiita.reference_exclusion WHERE reference_exclusion_idx = $1",
            exclusion_idx,
        )


async def test_delete_idxs_rejects_a_non_identifier_table():
    """Tests the case where the interpolated table name is not a bare identifier."""
    with pytest.raises(ValueError, match="non-identifier name"):
        await delete_idxs(None, "study; SELECT 1 --", [1])


async def test__sweep_table_rejects_a_non_identifier_table():
    """Tests the case where the interpolated table name is not a bare identifier."""
    with pytest.raises(ValueError, match="non-identifier name"):
        await _sweep_table(
            None,
            "study_access; SELECT 1 --",
            (("study_idx", STUDY),),
            {STUDY: [1]},
        )


async def test__sweep_table_rejects_a_non_identifier_column():
    """Tests the case where an interpolated column name is not a bare identifier.

    The keys are interpolated alongside the table, so a bad one has to be
    refused on the same terms rather than ride in behind a clean table name.
    """
    with pytest.raises(ValueError, match="non-identifier name"):
        await _sweep_table(
            None,
            "study_access",
            (("study_idx; SELECT 1 --", STUDY),),
            {STUDY: [1]},
        )
