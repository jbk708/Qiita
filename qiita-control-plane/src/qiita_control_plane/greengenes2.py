"""The feature table's `greengenes2` filter: an amplicon table cut to the ASVs
Greengenes2 places, each ranked by its Greengenes2 lineage.

Greengenes2 (2024.09) places V4 ASVs of 90, 100 and 150 nt, and names each by the
md5 of its sequence — the same name a rapid-16S deblur table gives an ASV, read on
the same strand. So the filter is an exact join on that md5: an ASV of another
length or region matches nothing, and the table comes back empty rather than wrong.

The file the server joins against is `GREENGENES2_ASV_TAXONOMY_PATH`: a Parquet of
`(md5, lineage)`, one row per placed ASV, sorted by md5 so a lookup reads only the
row groups it needs. `scripts/greengenes2-asv-taxonomy.py` builds it from the
release's `taxonomy.md5.tsv.gz`. Until Greengenes2 is loaded as a reference, this
file is how the server knows it.
"""

from qiita_common.taxonomy import QUOTED_RANK_COLUMNS, RANK_PREFIXES

# The relation `stage` creates: each kept feature (an md5) and its eight ranks.
GREENGENES2_RELATION = "greengenes2_kept"


def _rank(prefix: str) -> str:
    # The lineage's term for this rank, when it names one: `p__Bacteroidota`, never
    # a bare `p__`, which reads as "reported, and empty".
    return f"NULLIF(list_filter(terms, lambda t: starts_with(t, '{prefix}'))[1], '{prefix}')"


def stage(con, *, path: str, features: str) -> None:
    """Create `GREENGENES2_RELATION` (feature, <eight ranks>) on `con`: the rows of
    `features` (any relation with a `feature` md5 column) that Greengenes2 places,
    with their ranks. `path` is the server's Parquet; it is read, never written."""
    ranks = ", ".join(
        f"{_rank(prefix)} AS {column}"
        for column, prefix in zip(QUOTED_RANK_COLUMNS, RANK_PREFIXES, strict=True)
    )
    con.execute(
        f"CREATE TEMP TABLE {GREENGENES2_RELATION} AS"
        f" SELECT feature, {ranks}"
        " FROM (SELECT g.md5 AS feature,"
        "              list_transform(string_split(g.lineage, ';'), lambda t: trim(t)) AS terms"
        "         FROM read_parquet(?) g"
        f"        WHERE g.md5 IN (SELECT feature FROM {features}))",
        [path],
    )
