"""DB-tier parity check: every `BIOSAMPLE_DISPLAY_*` name in
`qiita_common.models.biosample` must resolve to a real
`qiita.biosample_global_field` row.

Subset only -- the seeded registry legitimately holds display names with no
Python constant (`taxon id`, the environmental-context triad), so this does
not assert equality. Reads the live schema after `dbmate up`, so it covers
every seed migration that inserts a `biosample_global_field` row, present or
future, without needing an edit when a new one lands.
"""

from __future__ import annotations

import pytest
from qiita_common.models import BIOSAMPLE_DISPLAY_NAMES

pytestmark = pytest.mark.db


async def test_biosample_display_names_exist_as_global_fields(postgres_pool):
    rows = await postgres_pool.fetch(
        "SELECT display_name FROM qiita.biosample_global_field"
        " WHERE display_name = ANY($1::text[])",
        list(BIOSAMPLE_DISPLAY_NAMES),
    )
    found = {r["display_name"] for r in rows}
    missing = BIOSAMPLE_DISPLAY_NAMES - found
    assert not missing, (
        f"BIOSAMPLE_DISPLAY_NAMES names {sorted(missing)}, but no "
        f"qiita.biosample_global_field row has that display_name. A display "
        f"name only changes via a new migration -- check for a rename that "
        f"didn't update the constant."
    )
