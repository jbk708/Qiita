"""A profiler's genome name reduces to the `source_id` its reference loaded it under."""

import duckdb
import pytest

from qiita_common.taxonomic_profile_constants import genome_source_id_sql


@pytest.mark.parametrize(
    ("name", "source_id"),
    [
        ("/db/wol3/G000802255.fna.gz", "G000802255"),
        ("G000802255", "G000802255"),
        ("/gtdb/r232/GCA_000005845.2_genomic.fna.gz", "GCA_000005845.2"),
        ("GCF_000001405.40_genomic.fa", "GCF_000001405.40"),
        ("rel/path/G1.fasta", "G1"),
    ],
)
def test_a_genome_name_reduces_to_its_source_id(name, source_id):
    sql = genome_source_id_sql("name")
    assert duckdb.sql(f"SELECT {sql} FROM (SELECT ? AS name)", params=[name]).fetchall()[0][0] == (
        source_id
    )
