"""Tests for `ena_import.harmonization.build_biosample_metadata`.

`harmonization.py` writes exactly one `biosample_global_field.display_name`
literal directly (`host taxon id`'s unconditional `missing_value_reason`
entry); the lookup-table names in `attribute_mapping.py` are covered there.
"""

from pathlib import Path

from qiita_control_plane import ena_import
from qiita_control_plane.ena_import.harmonization import (
    HOST_TAXON_ID_UNKNOWN,
    build_biosample_metadata,
)


def test_build_biosample_metadata_empty_input_marks_host_taxon_id_unknown():
    global_metadata, local_metadata, result = build_biosample_metadata({})

    assert global_metadata == {"host taxon id": HOST_TAXON_ID_UNKNOWN}
    assert local_metadata == {}
    assert result.mapped_count == 0


def test_harmonization_source_has_no_bare_host_taxon_id_literal():
    """`harmonization.py` has no lookup table, so `"host taxon id"` is
    unambiguous there -- unlike `attribute_mapping.py`, where the same string
    is a normalized-tag lookup key that must stay literal."""
    source_path = Path(ena_import.__file__).parent / "harmonization.py"
    assert '"host taxon id"' not in source_path.read_text()
