"""qc_reasons.yml is the vocabulary the QC writer validates against."""

import re

from qiita_common.models import QcCheck
from qiita_common.qc_reasons import load_qc_reasons

_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")


def test_every_key_is_a_qc_check_and_every_check_has_an_entry():
    reasons = load_qc_reasons()
    assert set(reasons) == set(QcCheck)


def test_codes_are_snake_case():
    bad = [
        code
        for codes in load_qc_reasons().values()
        for code in codes
        if not _SNAKE_CASE.match(code)
    ]
    assert not bad


def test_codes_are_unique_within_a_check():
    from importlib import resources

    import yaml

    raw = yaml.safe_load(
        resources.files("qiita_common.data").joinpath("qc_reasons.yml").read_text(encoding="utf-8")
    )
    for check, codes in raw.items():
        assert len(codes) == len(set(codes)), check


def test_loader_is_cached_and_frozen():
    reasons = load_qc_reasons()
    assert load_qc_reasons() is reasons
    assert all(isinstance(codes, frozenset) for codes in reasons.values())
