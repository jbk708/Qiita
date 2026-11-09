"""A run feature table: a query over a cohort, answered as sparse COO.

A feature table is not a run's artifact. It is a selection — one or more
processing runs, over a study's samples or a named list — of the values the lake
already holds as COO rows, one per processed sample and feature. Two methods
produce such runs, each a `Source`:

  * amplicon: `amplicon_membership`, ASV counts. ASVs are content-deduped into one
    feature space, so the same sequence found by two runs is one feature, named by
    the md5 of its sequence, which the table carries.
  * taxonomic-profile: `taxonomic_profile`, a profiler's relative abundance per
    genome of the sequence reference its database was sketched from. A genome is
    named as an alignment-based table names one — its `exported_feature` handle,
    the accession wherever one is free — and carries the reference's taxonomy, by
    the same reduction and sidecar SQL (`qiita_common.analytic`). No sequence.

Merging runs is that selection, nothing more, provided they are runs of one method
whose features compare (`Source.merge_keys`). Three steps, each testable alone:
  * `resolve_cohort` picks the processed samples (one run's view of one
    prep_sample) the request names and gives each a public column name;
  * `cohort_tickets` signs the reads of their values (and an amplicon table's ASV
    bytes), which `fetch_runs` performs;
  * `stage_values` turns those into the features `(feature, sequence, length)` and
    the values `(sample, feature, value)`, public handles only.

Nothing is made dense: a rapid-16S pool is ~8% dense, and density falls as runs
and samples are added. Internal idxs are join keys and never reach the output.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass

import asyncpg
import duckdb
import pyarrow as pa
import pyarrow.flight as flight
from qiita_common import analytic as ft
from qiita_common.actions import AMPLICON_ACTION_ID, TAXONOMIC_PROFILE_ACTION_ID
from qiita_common.amplicon_constants import (
    AMPLICON_MEMBERSHIP_TABLE,
    AMPLICON_SEQUENCE_CHUNKS_TABLE,
)
from qiita_common.models.run_feature_table import (
    FeatureTableRequest,
    FeatureTableSample,
    FeatureTableValueKind,
)
from qiita_common.taxonomic_profile_constants import TAXONOMIC_PROFILE_TABLE
from qiita_common.taxonomy import (
    RANK_COLUMNS,
    TAXONOMY_SOURCE_TABLE,
    quote_rank,
    rank_columns_sql,
)

from . import greengenes2
from .auth.tickets import sign_ticket
from .repositories.exported_feature import mint_exported_features
from .repositories.reference_membership import GENOME_MAP_PAIRS_SQL
from .sample_names import BIOSAMPLE_NAME_JOIN, BIOSAMPLE_NAME_SQL

ARROW_STREAM_MEDIA_TYPE = "application/vnd.apache.arrow.stream"


@dataclass(frozen=True)
class Source:
    """Where one method's runs keep their values, and what makes two comparable."""

    method: str
    value_kind: FeatureTableValueKind
    values_table: str
    # The values table's column holding a cell's value.
    value_column: str
    # What a value row's feature is: a content-deduped `feature_idx` (an ASV), or a
    # `genome_idx` of the reference the run read (`reference_key`).
    feature_key: str
    # The ASV-bytes table of a method whose features are sequences; None otherwise.
    sequences_table: str | None
    # The run params whose difference makes two runs' features incomparable.
    merge_keys: tuple[str, ...]
    # What a run counts, for refusals ("these runs' ASVs ...").
    noun: str
    # The run param naming the reference a genome-keyed run's genomes belong to,
    # whose taxonomy labels them; None for a method without one.
    reference_key: str | None = None


SOURCES: dict[str, Source] = {
    # The trim length is the ASV's length, and orientation decides which strand it
    # is read on: ASVs from runs differing in these are different features, so
    # merging them would yield disjoint blocks under one table's name. Other params
    # (the SortMeRNA reference, the workflow version) change which reads survive,
    # not what an ASV is.
    AMPLICON_ACTION_ID: Source(
        method=AMPLICON_ACTION_ID,
        value_kind="count",
        values_table=AMPLICON_MEMBERSHIP_TABLE,
        value_column="count",
        feature_key="feature_idx",
        sequences_table=AMPLICON_SEQUENCE_CHUNKS_TABLE,
        merge_keys=("trim", "orient_primer"),
        noun="ASVs",
    ),
    # A database file decides which of its reference's genomes a profile can
    # report, and relative abundance is relative to those, so only profiles against
    # one file compare; the threshold changes which genomes a sample reports, so two
    # thresholds' abundances are not one table's either.
    TAXONOMIC_PROFILE_ACTION_ID: Source(
        method=TAXONOMIC_PROFILE_ACTION_ID,
        value_kind="relative_abundance",
        values_table=TAXONOMIC_PROFILE_TABLE,
        value_column="taxonomic_abundance",
        feature_key="genome_idx",
        sequences_table=None,
        merge_keys=("sylph_reference_idx", "sylph_database_sha256", "min_ani"),
        noun="profiles",
        reference_key="sylph_reference_idx",
    ),
}

# Dictionary-encoded names: each sample and feature id is sent once, and each row
# carries two small integer codes and its value. Plain strings would repeat a
# 32-character md5 per observation, making the stream larger than a dense TSV.
_NAME = pa.dictionary(pa.int32(), pa.string())
VALUES_SCHEMA = pa.schema([("sample", _NAME), ("feature", _NAME), ("value", pa.float64())])
# `sequence` and `length` are null for a method whose features are not sequences;
# the eight ranks are a genome's, from its reference's taxonomy with the source
# prefixes restored (the alignment taxonomy sidecar's columns), or a Greengenes2-
# filtered ASV's from its lineage; null for an unfiltered ASV and an unclassified
# genome.
FEATURES_SCHEMA = pa.schema(
    [
        ("feature", pa.string()),
        ("sequence", pa.string()),
        ("length", pa.int32()),
        *((rank, pa.string()) for rank in RANK_COLUMNS),
    ]
)
_NULL_RANKS = ", ".join(f"NULL::VARCHAR AS {quote_rank(r)}" for r in RANK_COLUMNS)


class CohortError(Exception):
    """A request the cohort cannot answer; `status` is the HTTP status it maps to."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class ProcessedSample:
    processing_idx: int
    prep_sample_idx: int
    column: FeatureTableSample


@dataclass(frozen=True)
class Cohort:
    source: Source
    runs: list[int]
    samples: list[ProcessedSample]
    # The reference whose genomes a genome-keyed table's features are (one, by the
    # merge keys); None for amplicon runs.
    reference_idx: int | None = None
    # The read-time filter the request names (`greengenes2`), if any.
    feature_filter: str | None = None

    @property
    def prep_sample_idxs(self) -> list[int]:
        return sorted({s.prep_sample_idx for s in self.samples})


def _params(row: asyncpg.Record) -> dict:
    params = row["params"]
    return json.loads(params) if isinstance(params, str) else (params or {})


def _source_of(runs: list[int], found: dict[int, asyncpg.Record]) -> tuple[Source, dict]:
    """The one method the runs share, or a refusal naming what is wrong."""
    unknown = [p for p in runs if p not in found or found[p]["workflow"] not in SOURCES]
    if unknown:
        raise CohortError(
            404,
            f"not feature-table runs ({', '.join(sorted(SOURCES))}): processing_idx {unknown}",
        )
    methods = {p: found[p]["workflow"] for p in runs}
    if len(set(methods.values())) > 1:
        detail = "; ".join(f"{p}: {m}" for p, m in methods.items())
        raise CohortError(409, f"these runs are of different methods: {detail}")
    source = SOURCES[next(iter(methods.values()))]
    settings = {p: tuple(_params(found[p]).get(k) for k in source.merge_keys) for p in runs}
    if len(set(settings.values())) > 1:
        detail = "; ".join(
            f"{p}: " + ", ".join(f"{k}={v}" for k, v in zip(source.merge_keys, s, strict=True))
            for p, s in settings.items()
        )
        raise CohortError(
            409,
            f"these runs' {source.noun} are not comparable"
            f" ({', '.join(source.merge_keys)} differ): {detail}",
        )
    return source, _params(found[runs[0]])


async def resolve_cohort(pool: asyncpg.Pool, request: FeatureTableRequest) -> Cohort:
    """The processed samples `request` names, each with its column name.

    The runs must be runs of one method and comparable (`_source_of`). A processed
    sample is a live QM (`exported_identifier`) of one of the runs — each workflow
    mints one per sample it reported on — whose prep_sample is in the named study
    (live link) or the named list, and not ENA-flagged. A named list that includes
    a prep_sample none of the runs reported on is refused rather than trimmed.
    """
    runs = sorted(set(request.processing_idx))
    rows = await pool.fetch(
        "SELECT processing_idx, workflow, params FROM qiita.processing"
        " WHERE processing_idx = ANY($1::bigint[])",
        runs,
    )
    source, params = _source_of(runs, {r["processing_idx"]: r for r in rows})
    if request.feature_filter is not None and source.feature_key != "feature_idx":
        raise CohortError(
            409,
            f"a {source.method} table's features are genomes;"
            f" the {request.feature_filter} filter applies to an amplicon table's ASVs",
        )

    processed = await pool.fetch(
        "SELECT ei.processing_idx, ei.prep_sample_idx, ei.export_id,"
        "       COALESCE(b.biosample_accession, b.ena_sample_accession) AS accession,"
        f"      {BIOSAMPLE_NAME_SQL} AS biosample"
        "  FROM qiita.exported_identifier ei"
        "  JOIN qiita.prep_sample ps ON ps.idx = ei.prep_sample_idx"
        "  JOIN qiita.biosample b ON b.idx = ps.biosample_idx"
        f" {BIOSAMPLE_NAME_JOIN}"
        "  LEFT JOIN qiita.sequenced_sample ss ON ss.prep_sample_idx = ps.idx"
        " WHERE ei.processing_idx = ANY($1::bigint[]) AND NOT ei.retired"
        "   AND ss.ena_status IS NULL"
        "   AND ($2::bigint IS NULL OR EXISTS ("
        "     SELECT 1 FROM qiita.prep_sample_to_study pst"
        "      WHERE pst.prep_sample_idx = ps.idx AND pst.study_idx = $2 AND NOT pst.retired))"
        "   AND ($3::bigint[] IS NULL OR ps.idx = ANY($3::bigint[]))"
        " ORDER BY ei.prep_sample_idx, ei.processing_idx",
        runs,
        request.study_idx,
        request.prep_sample_idx,
    )
    if request.prep_sample_idx is not None:
        uncounted = sorted(set(request.prep_sample_idx) - {r["prep_sample_idx"] for r in processed})
        if uncounted:
            raise CohortError(
                404,
                f"{len(uncounted)} named prep_sample(s) were not processed by these runs"
                f" (prep_sample_idx {uncounted[:5]})",
            )
    if not processed:
        raise CohortError(404, "these runs processed none of the named samples")
    if any(r["biosample"] is None for r in processed):
        raise RuntimeError("a processed sample's biosample has no accession and no QB")

    # A deposited accession names one column only when no other column of this
    # table shares it (one biosample, one processed sample); otherwise each of
    # those columns falls back to its own QM, so the two stay distinguishable.
    per_accession: dict[str, int] = {}
    for r in processed:
        if r["accession"]:
            per_accession[r["accession"]] = per_accession.get(r["accession"], 0) + 1
    samples = [
        ProcessedSample(
            processing_idx=r["processing_idx"],
            prep_sample_idx=r["prep_sample_idx"],
            column=FeatureTableSample(
                sample=(
                    r["accession"]
                    if r["accession"] and per_accession[r["accession"]] == 1
                    else r["export_id"]
                ),
                export_id=r["export_id"],
                biosample=r["biosample"],
                processing_idx=r["processing_idx"],
            ),
        )
        for r in processed
    ]
    return Cohort(
        source=source,
        runs=runs,
        samples=samples,
        reference_idx=params.get(source.reference_key) if source.reference_key else None,
        feature_filter=request.feature_filter,
    )


def cohort_scope(cohort: Cohort) -> dict[str, list[int]]:
    """The one ticket filter every read of `cohort` signs: its runs over its
    prep_samples (the data plane's `build_amplicon_run_query` shape)."""
    return {"processing_idx": cohort.runs, "prep_sample_idx": cohort.prep_sample_idxs}


def cohort_tickets(cohort: Cohort, signing_key: bytes) -> tuple[bytes, bytes | None]:
    """Signed DoGet tickets for the cohort's values and, for an amplicon table, its
    ASV chunks."""
    scope = cohort_scope(cohort)
    source = cohort.source
    values = sign_ticket(table=source.values_table, filter=scope, secret=signing_key)
    sequences = (
        sign_ticket(table=source.sequences_table, filter=scope, secret=signing_key)
        if source.sequences_table
        else None
    )
    return values, sequences


def fetch_runs(
    *, data_plane_url: str, signing_key: bytes, cohort: Cohort, values: bool = True
) -> tuple[pa.Table | None, pa.Table | None]:
    """Read the cohort's values (unless `values` is False) and, for an amplicon
    table, the ASV chunks they name. Blocking (Flight is synchronous); callers run
    it off the event loop."""
    values_ticket, sequences_ticket = cohort_tickets(cohort, signing_key)
    with flight.FlightClient(data_plane_url) as client:
        observed = client.do_get(flight.Ticket(values_ticket)).read_all() if values else None
        chunks = (
            client.do_get(flight.Ticket(sequences_ticket)).read_all()
            if sequences_ticket is not None
            else None
        )
    return observed, chunks


# The relations `stage_values` creates on the caller's connection.
FEATURE_RELATION = "run_feature"
VALUE_RELATION = "run_value"


def stage_features(con, *, chunks: str) -> None:
    """Create `FEATURE_RELATION` (feature_idx, feature, sequence, length) from the
    ASV chunk relation `chunks`: each ASV's bytes reassembled, and its feature id,
    the md5 of its sequence (the QIIME 2 / Greengenes2 convention)."""
    con.execute(
        f"CREATE TEMP TABLE {FEATURE_RELATION} AS"
        " SELECT feature_idx, md5(seq) AS feature, seq AS sequence,"
        "        length(seq)::INTEGER AS length"
        " FROM (SELECT feature_idx, string_agg(chunk_data, '' ORDER BY chunk_index) AS seq"
        f"       FROM {chunks} GROUP BY feature_idx)"
    )


def _refuse_unnamed(con, *, names: str, values: str) -> None:
    unnamed = con.execute(
        f"SELECT count(*) FROM {values} ANTI JOIN {names} USING (processing_idx, prep_sample_idx)"
    ).fetchone()[0]
    if unnamed:
        raise RuntimeError(f"{unnamed} value(s) of a processed sample with no QM")


def stage_values(
    con,
    *,
    source: Source,
    names: str,
    values: str,
    chunks: str | None = None,
    genome_labels: str | None = None,
    greengenes2_path: str | None = None,
) -> None:
    """Create `FEATURE_RELATION` (feature, sequence, length) and `VALUE_RELATION`
    (sample, feature, value): the values in `values` relabelled to public names,
    one row per nonzero cell. `names` maps (processing_idx, prep_sample_idx) to a
    column name; `chunks` is the ASV chunk relation of an amplicon table, and
    `genome_labels` the `exported_feature` handles `(genome_idx, export_feature_id)`
    of a genome-keyed one. `greengenes2_path` filters an amplicon table to the ASVs
    Greengenes2 places (`greengenes2.py`): the others leave both relations. Refuses
    rather than guesses: a value of a processed
    sample `names` lacks, or of a feature with no name, would need an internal idx
    as its name."""
    _refuse_unnamed(con, names=names, values=values)
    if source.feature_key == "genome_idx":
        if genome_labels is None:
            raise RuntimeError(f"a {source.method} table needs its genomes' labels")
        con.execute(ft.genome_label_table_sql(genome_labels))
        unlabelled = con.execute(
            f"SELECT count(*) FROM {values} v"
            f" ANTI JOIN {ft.GENOME_LABEL_TABLE} g USING (genome_idx)"
        ).fetchone()[0]
        if unlabelled:
            raise RuntimeError(f"{unlabelled} value(s) of a genome with no label")
        con.execute(
            f"CREATE TEMP TABLE {FEATURE_RELATION} AS"
            " SELECT DISTINCT g.feature_id AS feature, NULL::VARCHAR AS sequence,"
            "        NULL::INTEGER AS length"
            f" FROM {values} v JOIN {ft.GENOME_LABEL_TABLE} g USING (genome_idx)"
        )
        con.execute(
            f"CREATE TEMP TABLE {VALUE_RELATION} AS"
            f" SELECT n.sample, g.feature_id AS feature,"
            f"        sum(v.{source.value_column})::DOUBLE AS value"
            f" FROM {values} v JOIN {names} n USING (processing_idx, prep_sample_idx)"
            f" JOIN {ft.GENOME_LABEL_TABLE} g USING (genome_idx)"
            " GROUP BY n.sample, g.feature_id"
        )
        return
    if chunks is None:
        raise RuntimeError(f"a {source.method} table needs its ASV chunks")
    stage_features(con, chunks=chunks)
    missing = con.execute(
        f"SELECT count(DISTINCT feature_idx) FROM {values}"
        f" ANTI JOIN {FEATURE_RELATION} USING (feature_idx)"
    ).fetchone()[0]
    if missing:
        raise RuntimeError(f"{missing} observed ASV(s) have no sequence in the lake")
    if greengenes2_path is not None:
        _keep_greengenes2(con, greengenes2_path)
    con.execute(
        f"CREATE TEMP TABLE {VALUE_RELATION} AS"
        f" SELECT n.sample, f.feature, sum(m.{source.value_column})::DOUBLE AS value"
        f" FROM {values} m"
        f" JOIN {names} n USING (processing_idx, prep_sample_idx)"
        f" JOIN {FEATURE_RELATION} f USING (feature_idx)"
        " GROUP BY n.sample, f.feature"
    )


def _keep_greengenes2(con, path: str) -> None:
    greengenes2.stage(con, path=path, features=FEATURE_RELATION)
    con.execute(
        f"DELETE FROM {FEATURE_RELATION}"
        f" WHERE feature NOT IN (SELECT feature FROM {greengenes2.GREENGENES2_RELATION})"
    )


def names_table(cohort: Cohort) -> pa.Table:
    """(processing_idx, prep_sample_idx, sample): each processed sample's column name."""
    return pa.table(
        {
            "processing_idx": pa.array([s.processing_idx for s in cohort.samples], pa.int64()),
            "prep_sample_idx": pa.array([s.prep_sample_idx for s in cohort.samples], pa.int64()),
            "sample": pa.array([s.column.sample for s in cohort.samples], pa.string()),
        }
    )


@dataclass(frozen=True)
class Genomes:
    """What a genome-keyed table names its features with: each observed genome's
    `exported_feature` handle, and for its taxonomy, the reference's member features
    of those genomes `(feature_idx, genome_idx)` and their taxonomy rows (None when
    only the values are wanted)."""

    labels: pa.Table
    members: pa.Table | None = None
    taxonomy: pa.Table | None = None


def _staged(
    cohort: Cohort,
    values: pa.Table,
    chunks: pa.Table | None,
    genomes: Genomes | None,
    query: str,
    greengenes2_path: str | None = None,
) -> pa.Table:
    with duckdb.connect() as duck:
        duck.register("run_values", values)
        duck.register("names", names_table(cohort))
        if chunks is not None:
            duck.register("chunks", chunks)
        if genomes is not None:
            duck.register("genome_labels", genomes.labels)
        stage_values(
            duck,
            source=cohort.source,
            names="names",
            values="run_values",
            chunks="chunks" if chunks is not None else None,
            genome_labels="genome_labels" if genomes is not None else None,
            greengenes2_path=greengenes2_path,
        )
        if genomes is not None and genomes.members is not None:
            duck.register("genome_members", genomes.members)
            duck.register("genome_taxonomy", genomes.taxonomy)
            duck.execute(ft.map_table_sql("genome_members"))
            duck.execute(ft.taxonomy_table_sql("genome_taxonomy"))
            duck.execute(ft.taxonomy_sidecar_sql())
        return duck.sql(query).to_arrow_table()


def build_values(
    cohort: Cohort,
    values: pa.Table,
    chunks: pa.Table | None,
    genomes: Genomes | None = None,
    greengenes2_path: str | None = None,
) -> pa.Table:
    """The values `(sample, feature, value)` as Arrow, for the REST answer."""
    table = _staged(
        cohort,
        values,
        chunks,
        genomes,
        f"SELECT sample, feature, value FROM {VALUE_RELATION} ORDER BY sample, feature",
        greengenes2_path,
    )
    return table.cast(VALUES_SCHEMA)


def observed_genomes(values: pa.Table) -> list[int]:
    """The distinct genomes a genome-keyed table's values name. A NULL is a row
    written before profiles resolved their genomes, whose run no table can name."""
    genomes = set(values.column("genome_idx").to_pylist())
    if None in genomes:
        raise RuntimeError("a value names no genome: its run predates genome resolution")
    return sorted(genomes)


async def genome_labels(pool: asyncpg.Pool, genome_idx: list[int], created_by_idx: int) -> pa.Table:
    """Each genome's `exported_feature` handle, minted where it has none yet: the
    name an alignment-based table gives the same genome."""
    rows = await mint_exported_features(
        pool,
        genome_idx=genome_idx,
        reference_idx=None,
        feature_idx=[],
        created_by_idx=created_by_idx,
    )
    rows = [r for r in rows if r["genome_idx"] is not None]
    return pa.table(
        {
            "genome_idx": pa.array([r["genome_idx"] for r in rows], pa.int64()),
            "export_feature_id": pa.array([r["export_feature_id"] for r in rows], pa.string()),
        }
    )


async def genome_members(pool: asyncpg.Pool, reference_idx: int, genome_idx: list[int]) -> pa.Table:
    """The reference's member features of `genome_idx`, `(feature_idx, genome_idx)`:
    what a genome's taxonomy is reduced from."""
    rows = await pool.fetch(
        "SELECT rm.feature_idx, fg.genome_idx"
        + GENOME_MAP_PAIRS_SQL
        + " AND fg.genome_idx = ANY($2::bigint[])",
        reference_idx,
        genome_idx,
    )
    return pa.table(
        {
            "feature_idx": pa.array([r["feature_idx"] for r in rows], pa.int64()),
            "genome_idx": pa.array([r["genome_idx"] for r in rows], pa.int64()),
        }
    )


def fetch_taxonomy(
    *, data_plane_url: str, signing_key: bytes, reference_idx: int, feature_idx: list[int]
) -> pa.Table:
    """The reference's taxonomy rows of `feature_idx`, from the exclusion-aware
    view. Blocking (Flight is synchronous); callers run it off the event loop."""
    ticket = sign_ticket(
        table=TAXONOMY_SOURCE_TABLE,
        filter={"reference_idx": [reference_idx], "feature_idx": feature_idx},
        secret=signing_key,
    )
    with flight.FlightClient(data_plane_url) as client:
        return client.do_get(flight.Ticket(ticket)).read_all()


def build_features(
    cohort: Cohort,
    values: pa.Table | None,
    chunks: pa.Table | None,
    genomes: Genomes | None = None,
    greengenes2_path: str | None = None,
) -> pa.Table:
    """The features `(feature, sequence, length, <ranks>)` as Arrow, for the REST
    answer. An amplicon table's needs only the chunks (the ticket already scopes
    them to the ASVs the cohort observed), and a Greengenes2-filtered one's are the
    ASVs it places, ranked by it; a genome-keyed one's are read from its values,
    named by `genomes.labels` and ranked from `genomes.taxonomy`."""
    if cohort.source.sequences_table is not None:
        if chunks is None:
            raise RuntimeError(f"a {cohort.source.method} table needs its ASV chunks")
        with duckdb.connect() as duck:
            duck.register("chunks", chunks)
            stage_features(duck, chunks="chunks")
            ranks, join = _NULL_RANKS, ""
            if greengenes2_path is not None:
                _keep_greengenes2(duck, greengenes2_path)
                ranks = rank_columns_sql(alias="t")
                join = f" JOIN {greengenes2.GREENGENES2_RELATION} t USING (feature)"
            table = duck.sql(
                f"SELECT f.feature, f.sequence, f.length, {ranks}"
                f" FROM {FEATURE_RELATION} f{join} ORDER BY f.feature"
            ).to_arrow_table()
        return table.cast(FEATURES_SCHEMA)
    if values is None or genomes is None:
        raise RuntimeError(f"a {cohort.source.method} table's features come from its values")
    ranks = (
        ", ".join(f"t.{quote_rank(r)}" for r in RANK_COLUMNS)
        if genomes.members is not None
        else _NULL_RANKS
    )
    taxonomy_join = (
        f" LEFT JOIN {ft.TAXONOMY_SIDECAR_RELATION} t ON t.feature_id = f.feature"
        if genomes.members is not None
        else ""
    )
    table = _staged(
        cohort,
        values,
        None,
        genomes,
        f"SELECT f.feature, f.sequence, f.length, {ranks}"
        f" FROM {FEATURE_RELATION} f{taxonomy_join} ORDER BY f.feature",
    )
    return table.cast(FEATURES_SCHEMA)


def arrow_stream_bytes(table: pa.Table) -> bytes:
    """`table` as an Arrow IPC stream: what the values and features routes send."""
    sink = io.BytesIO()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue()
