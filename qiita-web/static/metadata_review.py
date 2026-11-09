"""Metadata review for the upload loop: metameq's findings as one issue per cell,
in plain language, with the fixes worth offering, and a preview of what metameq's
extension would add.

`run_metameq_validation` (validation.py) does the validating; this turns its
output into what a person can act on:

  * one issue per (sample, field) — metameq reports an `anyof` failure twice, and
    its messages are Cerberus internals;
  * a kind: `error` (the value breaks the field's rule), `type` (the row's host or
    sample type is unknown, so metameq skipped the row: fixing it can reveal more),
    or `curation` (the standard pins one value and the sheet says something else,
    often more specific — a question for curators, not necessarily a mistake);
  * suggestions, each a value with the rule that produced it;
  * the extension: columns metameq would add and blanks it would fill, with a flag
    where an added column looks like a legacy-named column it did not recognise.

Nothing here edits the sheet; the page applies what the person accepts.
"""

import difflib
import re
from collections import Counter, defaultdict

import pandas as pd
from metameq import (
    HOSTTYPE_SHORTHAND_KEY,
    SAMPLE_NAME_KEY,
    SAMPLETYPE_SHORTHAND_KEY,
    extract_config_dict,
)

# Values people write for "I don't know" that are not one of the standard's terms.
MISSINGISH = {
    "",
    "tbd",
    "na",
    "n/a",
    "nan",
    "none",
    "null",
    "unknown",
    "?",
    "-",
    "--",
    "missing",
}
# Legacy placeholders for "does not apply to this row" (a control's collection date
# reading "control sample"): the standard's term for that is "not applicable".
NOT_APPLICABLE_PLACEHOLDERS = {"control sample", "control blank", "control", "blank"}
# metameq's host types are common names ("mouse", "human"); sheets often carry the
# scientific name instead, which metameq doesn't recognise. Map each to the host type
# it means, so an unknown-host row offers the common name as a confident fix.
HOST_SYNONYMS = {
    "mus musculus": "mouse",
    "homo sapiens": "human",
    "rattus norvegicus": "rat",
    "canis lupus familiaris": "dog",
    "canis familiaris": "dog",
}
_US_DATE = re.compile(
    r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?\s*$"
)
_NORM = re.compile(r"[\s_\-]+")
# Suggestion rules safe to apply without a judgement call: a reformatted date, a
# spelling of the allowed value, a host type that is the only one with the row's
# sample type, a host type's common name for a scientific name. Everything else is
# offered, not counted as fixable.
AUTO_FIX_RULES = ("iso-date", "spelling", "host-from-sample-type", "host-synonym")


def _norm(v: str) -> str:
    return _NORM.sub(" ", str(v).strip().lower())


def _iso(value: str) -> str | None:
    """A US month/day/year (with optional time) as ISO 8601, or None."""
    m = _US_DATE.match(str(value))
    if not m:
        return None
    mo, dd, yy, hh, mi, ss = m.groups()
    if not (1 <= int(mo) <= 12 and 1 <= int(dd) <= 31):
        return None
    out = f"{yy}-{int(mo):02d}-{int(dd):02d}"
    if hh is not None:
        out += f" {int(hh):02d}:{mi}" + (f":{ss}" if ss else "")
    return out


def _rule_parts(rule: dict) -> tuple[list[str], list[str], bool, str]:
    """(allowed values, missing-value terms, is a date, description) of a field rule.

    An `anyof` rule is a value rule OR a list of missing-value terms; the value rule
    may be a date (a `date_not_in_future` check / date regex) or an allowed list."""
    allowed: list[str] = list(rule.get("allowed") or [])
    terms: list[str] = []
    is_date = False
    for alt in rule.get("anyof") or []:
        if alt.get("check_with") == "date_not_in_future" or "regex" in alt:
            is_date = True
        alt_allowed = alt.get("allowed") or []
        if alt_allowed and all(
            isinstance(a, str) and a.startswith(("not ", "restricted"))
            for a in alt_allowed
        ):
            terms = list(alt_allowed)
        elif alt_allowed:
            allowed += list(alt_allowed)
    return allowed, terms, is_date, str(rule.get("field_desc") or "")


class Config:
    """The flattened metameq config, indexed for lookups."""

    def __init__(self, path: str):
        self.raw = extract_config_dict(path)
        self.hosts = self.raw["host_type_specific_metadata"]
        self.host_column = (
            self.raw.get("hosttype_column_options") or [HOSTTYPE_SHORTHAND_KEY]
        )[0]
        self.sample_column = (
            self.raw.get("sampletype_column_options") or [SAMPLETYPE_SHORTHAND_KEY]
        )[0]
        # sample type -> the host types that define it, for inferring a host type.
        self.hosts_of = defaultdict(list)
        for host, cfg in self.hosts.items():
            for st in cfg.get("sample_type_specific_metadata") or {}:
                self.hosts_of[st].append(host)

    def rule(self, host: str, sample_type: str, field: str) -> dict:
        h = self.hosts.get(host) or {}
        st = (h.get("sample_type_specific_metadata") or {}).get(sample_type) or {}
        return (
            (st.get("metadata_fields") or {}).get(field)
            or (h.get("metadata_fields") or {}).get(field)
            or {}
        )


def _message(
    field: str, value, raw_msgs: list[str], rule: dict, kind: str, cfg: Config, row
) -> str:
    allowed, terms, is_date, desc = _rule_parts(rule)
    v = "" if value is None else str(value)
    if kind == "type":
        which = "host type" if "host_type" in " ".join(raw_msgs) else "sample type"
        known = sorted(cfg.hosts) if which == "host type" else sorted(cfg.hosts_of)
        return (
            f"Unknown {which} '{v}': metameq has no rules for it, so this row was not checked."
            f" Known {which}s include {', '.join(known[:6])}…"
        )
    if is_date:
        tail = f" Or one of: {', '.join(terms)}." if terms else ""
        return f"'{v}' is not a date. Use YYYY-MM-DD, optionally with HH:MM.{tail}"
    if any("number" in m for m in raw_msgs):
        tail = f" Or one of: {', '.join(terms)}." if terms else ""
        return f"'{v}' is not a number.{tail}"
    if any(m.startswith("unallowed value") for m in raw_msgs):
        shown = ", ".join(allowed[:5]) + ("…" if len(allowed) > 5 else "")
        if kind == "curation":
            return f"The standard expects '{allowed[0]}' here; the sheet says '{v}'."
        return f"'{v}' is not an allowed value. Allowed: {shown}."
    if any("required" in m or "empty" in m for m in raw_msgs):
        return f"Required, but empty.{' ' + desc if desc else ''}"
    return (
        "; ".join(dict.fromkeys(m for m in raw_msgs if m != "no definitions validate"))
        or "Invalid value."
    )


def _suggestions(
    field: str, value, rule: dict, kind: str, cfg: Config, row
) -> list[dict]:
    allowed, terms, is_date, _ = _rule_parts(rule)
    v = "" if value is None else str(value)
    out: list[dict] = []

    def add(val, label, rule_name):
        if val is not None and val != v and all(s["value"] != val for s in out):
            out.append({"value": val, "label": label, "rule": rule_name})

    if kind == "type":
        # A scientific name metameq doesn't know, but we can map to its common name:
        # offer that first, so it's the counted (fixable) suggestion.
        alias = HOST_SYNONYMS.get(_norm(v))
        if alias and alias in cfg.hosts:
            add(alias, f"{alias} (common name for '{v}')", "host-synonym")
        st = row.get(cfg.sample_column)
        for host in cfg.hosts_of.get(st, []):
            unique = len(cfg.hosts_of[st]) == 1
            add(
                host,
                f"{host}{' (only host type with sample type ' + repr(st) + ')' if unique else ''}",
                "host-from-sample-type",
            )
        return out
    if is_date:
        add(_iso(v), f"{_iso(v)} (ISO date)", "iso-date")
    if _norm(v) in NOT_APPLICABLE_PLACEHOLDERS and "not applicable" in terms:
        add(
            "not applicable",
            "not applicable (placeholder for a control)",
            "missing-term",
        )
    if _norm(v) in MISSINGISH and terms:
        add("not provided", "not provided", "missing-term")
    if allowed:
        exact = [a for a in allowed if _norm(a) == _norm(v)]
        for a in exact:
            add(a, f"{a} (spelling)", "spelling")
        if not exact:
            for a in difflib.get_close_matches(v, allowed, n=2, cutoff=0.8):
                add(a, f"{a} (closest allowed)", "closest")
    if kind == "curation":
        add(allowed[0], f"{allowed[0]} (the standard's value)", "standard-value")
    for t in terms:
        add(t, t, "missing-term")
    return out


def _extension(raw: pd.DataFrame, ext: pd.DataFrame) -> dict:
    """What metameq's extension would change: columns added (with their values) and
    blanks filled (by value), and added columns that look like a legacy-named column
    the sheet already has (`sex` beside a populated `host_sex`)."""
    raw_i = raw.set_index(SAMPLE_NAME_KEY)
    ext_i = ext.set_index(SAMPLE_NAME_KEY).reindex(raw_i.index)
    added, filled, flags = [], [], []
    for col in ext_i.columns:
        values = ext_i[col].fillna("").astype(str)
        if col not in raw_i.columns:
            added.append(
                {"column": col, "values": dict(Counter(values).most_common(5))}
            )
            for other in raw_i.columns:
                if other != col and other.endswith("_" + col):
                    data = raw_i[other].fillna("").astype(str)
                    real = data[
                        ~data.map(_norm).isin(
                            MISSINGISH
                            | {"not provided", "not applicable", "not collected"}
                        )
                    ]
                    if len(real) and set(values.map(_norm)) <= {
                        "not provided",
                        "not applicable",
                        "",
                    }:
                        flags.append(
                            {
                                "column": col,
                                "source": other,
                                "message": (
                                    f"metameq adds '{col}' as not provided, but '{other}' has values"
                                    f" ({', '.join(f'{k} {n}' for k, n in Counter(real).most_common(3))})."
                                    f" Likely the same field under a legacy name."
                                ),
                            }
                        )
            continue
        before = raw_i[col].fillna("").astype(str)
        changed = before.map(_norm).isin({""}) & (values != "")
        if changed.any():
            filled.append(
                {
                    "column": col,
                    "count": int(changed.sum()),
                    "values": dict(Counter(values[changed]).most_common(3)),
                }
            )
    return {
        "added": added,
        "filled": sorted(filled, key=lambda x: -x["count"]),
        "flags": flags,
    }


def review(
    validation_result: dict, data_path: str, config_path: str, file_ext: str
) -> dict:
    """Turn `run_metameq_validation`'s result into the upload loop's review."""
    r = validation_result
    out = {
        "processed": r.get("processed", False),
        "errors": r.get("errors", []),
        "issues": [],
        "extension": None,
        "summary": {
            "cells": 0,
            "samples": 0,
            "rows": r["summary"]["totalRows"],
            "fixable": 0,
        },
    }
    if not r.get("processed"):
        return out
    cfg = Config(config_path)
    sep = "," if file_ext == ".csv" else "\t"
    raw = pd.read_csv(data_path, sep=sep, dtype=str, keep_default_na=False)
    rows = raw.set_index(SAMPLE_NAME_KEY, drop=False)

    by_cell: dict[tuple[str, str], list[str]] = defaultdict(list)
    for m in r.get("validationErrors") or []:
        by_cell[(m["sample_name"], m["field_name"])].append(str(m["error_message"]))

    for (sample, field), msgs in by_cell.items():
        row = rows.loc[sample] if sample in rows.index else {}
        value = row.get(field) if hasattr(row, "get") else None
        host, st = row.get(cfg.host_column), row.get(cfg.sample_column)
        rule = cfg.rule(host, st, field)
        allowed, _, _, _ = _rule_parts(rule)
        if any(x.startswith("invalid ") for x in msgs):
            kind = "type"
        elif (
            any(x.startswith("unallowed value") for x in msgs)
            and len(allowed) == 1
            and _norm(value) not in MISSINGISH
        ):
            kind = "curation"
        else:
            kind = "error"
        suggestions = _suggestions(field, value, rule, kind, cfg, row)
        # The one allowed value spelled differently is a slip, not a curation question
        # (a merely similar value, e.g. a more specific term, stays a question).
        if kind == "curation" and any(s["rule"] == "spelling" for s in suggestions):
            kind = "error"
            suggestions = [s for s in suggestions if s["rule"] != "standard-value"]
        out["issues"].append(
            {
                "sample": sample,
                "field": field,
                "value": value,
                "kind": kind,
                "message": _message(field, value, msgs, rule, kind, cfg, row),
                "suggestions": suggestions,
            }
        )
    out["issues"].sort(key=lambda i: (i["field"], str(i["value"]), i["sample"]))
    out["summary"].update(
        cells=len(out["issues"]),
        samples=len({i["sample"] for i in out["issues"]}),
        fixable=sum(
            1
            for i in out["issues"]
            if i["suggestions"] and i["suggestions"][0]["rule"] in AUTO_FIX_RULES
        ),
    )
    out["extension"] = _extension(raw, pd.DataFrame(r["extendedMetadata"]))
    out["hostColumn"], out["sampleTypeColumn"] = cfg.host_column, cfg.sample_column
    return out
