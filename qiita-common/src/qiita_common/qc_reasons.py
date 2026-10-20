"""QC reason codes per check, loaded from the vendored `data/qc_reasons.yml`."""

from functools import cache
from importlib import resources

import yaml

from qiita_common.models import QcCheck


@cache
def load_qc_reasons() -> dict[QcCheck, frozenset[str]]:
    """Return the allowed reason codes for each `QcCheck`."""
    raw = yaml.safe_load(
        resources.files("qiita_common.data").joinpath("qc_reasons.yml").read_text(encoding="utf-8")
    )
    return {QcCheck(check): frozenset(codes) for check, codes in raw.items()}
