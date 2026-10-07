"""Ordered teardown of the study / biosample / prep_sample graph.

Deletes by parent FK rather than by row idx, so a caller needs no per-row
bookkeeping and a row nothing recorded — one a trigger or a cascade produced —
goes with the rest. The caller supplies three entity idx lists and nothing
else; these cannot be derived, because these entities may legitimately carry no
study link.
"""

import asyncpg

from .db_seeds import delete_idxs

STUDY = "study"
BIOSAMPLE = "biosample"
PREP_SAMPLE = "prep_sample"

# A column keyed this way names a genome rather than an entity, and is matched
# through the genomes that belong to the caller's prep_samples. Only
# qiita-derived genomes carry a prep_sample_idx (genome_qiita_origin_check), so
# an external genbank/refseq genome is never in range.
GENOME_OF_PREP_SAMPLE = "genome_of_prep_sample"

# The sweep, in delete order. Each entry is a table and the columns it is keyed
# on; a table with several keys is matched on any of them. Tier order is
# load-bearing — the tier-1 tables are referenced by tier-0 ones — while order
# within a tier is free.
#
# Hard-coded rather than derived from the catalog so a reader can see exactly
# what a teardown touches. A parity test compares this list against the live
# schema, so a table added to a migration and forgotten here fails on the schema
# alone rather than waiting for something to seed a row into it.
SWEEP_TIERS = (
    (
        ("alignment_sample", (("prep_sample_idx", PREP_SAMPLE),)),
        ("assembly_membership", (("prep_sample_idx", PREP_SAMPLE),)),
        ("assembly_sample", (("prep_sample_idx", PREP_SAMPLE),)),
        ("biosample_field_exception", (("biosample_idx", BIOSAMPLE),)),
        ("biosample_metadata", (("biosample_idx", BIOSAMPLE),)),
        ("biosample_to_study", (("biosample_idx", BIOSAMPLE), ("study_idx", STUDY))),
        ("block_member", (("prep_sample_idx", PREP_SAMPLE),)),
        ("ena_import_batch_item", (("study_idx", STUDY),)),
        ("exported_feature", (("genome_idx", GENOME_OF_PREP_SAMPLE),)),
        ("exported_identifier", (("prep_sample_idx", PREP_SAMPLE),)),
        ("feature_genome", (("genome_idx", GENOME_OF_PREP_SAMPLE),)),
        ("mask_sample", (("prep_sample_idx", PREP_SAMPLE),)),
        ("prep_sample_field_exception", (("prep_sample_idx", PREP_SAMPLE),)),
        ("prep_sample_metadata", (("prep_sample_idx", PREP_SAMPLE),)),
        ("prep_sample_to_study", (("prep_sample_idx", PREP_SAMPLE), ("study_idx", STUDY))),
        ("sequence_range", (("prep_sample_idx", PREP_SAMPLE),)),
        ("sequenced_sample", (("prep_sample_idx", PREP_SAMPLE),)),
        ("study_access", (("study_idx", STUDY),)),
        ("study_tag_to_study", (("study_idx", STUDY),)),
        ("syndna_read_count", (("prep_sample_idx", PREP_SAMPLE),)),
    ),
    (
        ("biosample_study_field", (("study_idx", STUDY),)),
        ("genome", (("prep_sample_idx", PREP_SAMPLE),)),
        ("prep_sample_study_field", (("study_idx", STUDY),)),
    ),
)

# prep_sample references biosample, and the link rows referencing study are gone
# by the time this runs, so the entities go in this order.
ENTITY_DELETE_ORDER = (PREP_SAMPLE, BIOSAMPLE, STUDY)

# Carries an entity idx column but is deliberately not swept: a work ticket is
# the caller's own, and teardown_entity_graph's docstring states when it has to
# be gone.
UNSWEPT_ENTITY_TABLES = frozenset({"work_ticket"})

_ENTITY_KEY_COLUMNS = {
    STUDY: "study_idx",
    BIOSAMPLE: "biosample_idx",
    PREP_SAMPLE: "prep_sample_idx",
}
_GENOME_KEY_COLUMN = "genome_idx"
_GENOME_TABLE = "genome"

_KEY_BY_COLUMN = {column: key for key, column in _ENTITY_KEY_COLUMNS.items()}
_KEY_BY_COLUMN[_GENOME_KEY_COLUMN] = GENOME_OF_PREP_SAMPLE
_ENTITY_TABLES = frozenset(_ENTITY_KEY_COLUMNS)


class EntityGraphNotSweptError(AssertionError):
    """Rows survived a teardown for the entities it was given.

    Carries the table and the surviving count, so the failure names what was
    missed rather than surfacing later as a foreign-key violation.
    """

    def __init__(self, table: str, column: str, surviving: int) -> None:
        self.table = table
        self.column = column
        self.surviving = surviving
        super().__init__(
            f"{surviving} row(s) survive in qiita.{table} for the swept entities"
            f" (matched on {column}); the sweep list is missing this table"
        )


async def _fetch_genome_idxs(pool: asyncpg.Pool, prep_sample_idxs: list[int]) -> list[int]:
    """Return the idxs of the genomes these prep_samples produced.

    Resolved before the sweep runs, because the sweep deletes qiita.genome and
    the genome-keyed tables can no longer be matched once it has.
    """
    if not prep_sample_idxs:
        return []
    rows = await pool.fetch(
        "SELECT genome_idx FROM qiita.genome WHERE prep_sample_idx = ANY($1::bigint[])",
        prep_sample_idxs,
    )
    genome_idxs = [row["genome_idx"] for row in rows]
    return genome_idxs


async def _sweep_table(pool: asyncpg.Pool, table: str, keys, idxs) -> None:
    """Delete one table's rows for the named entities, matching any of its keys."""
    clauses: list[str] = []
    args: list[list[int]] = []
    for column, key in keys:
        named = idxs[key]
        if not named:
            continue
        args.append(named)
        clauses.append(f"{column} = ANY(${len(args)}::bigint[])")
    if not clauses:
        return
    await pool.execute(f"DELETE FROM qiita.{table} WHERE " + " OR ".join(clauses), *args)


async def assert_entity_graph_swept(
    pool: asyncpg.Pool,
    *,
    study_idxs: list[int],
    biosample_idxs: list[int],
    prep_sample_idxs: list[int],
    genome_idxs: list[int] | None = None,
) -> None:
    """Raise if any table carrying an entity idx still holds rows for these entities.

    Discovers the tables from the catalog rather than the sweep list, so a table
    added to the schema and forgotten here fails the first time a test touches
    it. The entity tables themselves are skipped, since they are deleted after
    this runs.

    `genome_idxs` names the genomes whose derived rows are checked too. They
    cannot be looked up here: qiita.genome is already gone by the time this
    runs, so the caller resolves them first.
    """
    idxs = {
        STUDY: study_idxs,
        BIOSAMPLE: biosample_idxs,
        PREP_SAMPLE: prep_sample_idxs,
        GENOME_OF_PREP_SAMPLE: list(genome_idxs or ()),
    }
    candidates = await pool.fetch(
        "SELECT table_name, column_name FROM information_schema.columns"
        " WHERE table_schema = 'qiita' AND column_name = ANY($1::text[])"
        " ORDER BY table_name, column_name",
        [*_ENTITY_KEY_COLUMNS.values(), _GENOME_KEY_COLUMN],
    )
    for row in candidates:
        table, column = row["table_name"], row["column_name"]
        # The entities are deleted after this runs, and prep_sample carries a
        # biosample_idx of its own, so both would read as survivors here. The
        # genome rows go with the sweep, so the genome table reads the same way.
        if table in _ENTITY_TABLES or table in UNSWEPT_ENTITY_TABLES or table == _GENOME_TABLE:
            continue
        named = idxs[_KEY_BY_COLUMN[column]]
        if not named:
            continue
        surviving = await pool.fetchval(
            f"SELECT count(*) FROM qiita.{table} WHERE {column} = ANY($1::bigint[])",
            named,
        )
        if surviving:
            raise EntityGraphNotSweptError(table, column, surviving)


async def teardown_entity_graph(
    pool: asyncpg.Pool,
    *,
    study_idxs: list[int],
    biosample_idxs: list[int],
    prep_sample_idxs: list[int],
) -> None:
    """Delete these entities and everything hanging off them.

    Sweeps each tier in order, verifies nothing survived, then deletes the
    entities themselves. The caller keeps its own parents — pools, runs, masks,
    references — and deletes them after this returns.

    Work tickets go the other way: a ticket references its study and its
    prep_sample under RESTRICT, so the caller must clear its own tickets
    before calling this, or the entity delete at the end raises.
    """
    idxs = {
        STUDY: list(study_idxs),
        BIOSAMPLE: list(biosample_idxs),
        PREP_SAMPLE: list(prep_sample_idxs),
    }
    # Resolved up front: the tier that deletes qiita.genome runs below, and the
    # genome-keyed tables cannot be matched once it has.
    idxs[GENOME_OF_PREP_SAMPLE] = await _fetch_genome_idxs(pool, idxs[PREP_SAMPLE])
    for tier in SWEEP_TIERS:
        for table, keys in tier:
            await _sweep_table(pool, table, keys, idxs)
    await assert_entity_graph_swept(
        pool,
        study_idxs=idxs[STUDY],
        biosample_idxs=idxs[BIOSAMPLE],
        prep_sample_idxs=idxs[PREP_SAMPLE],
        genome_idxs=idxs[GENOME_OF_PREP_SAMPLE],
    )
    for table in ENTITY_DELETE_ORDER:
        await delete_idxs(pool, table, idxs[table])


async def delete_principal(pool: asyncpg.Pool, principal_idxs) -> None:
    """Delete these principals and their user rows.

    Runs last in a teardown: every table the caller created referencing a
    principal must already be gone, since those references are RESTRICT.
    """
    named = [principal_idxs] if isinstance(principal_idxs, int) else list(principal_idxs)
    if not named:
        return
    await pool.execute("DELETE FROM qiita.user WHERE principal_idx = ANY($1::bigint[])", named)
    await delete_idxs(pool, "principal", named)
