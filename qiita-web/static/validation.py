"""
METAMEQ validation runner for Pyodide environment.
Direct API calls — skips file I/O round-trip used by write_validator_metadata.

metameq validation is host-type- and sample-type-driven: every row needs a
`hosttype_shorthand` and a `sampletype_shorthand`, which metameq infers from a
`host_common_name` and a `sample_type` column (the config's
`hosttype_column_options` / `sampletype_column_options`). Arbitrary metadata often
has neither, so before validating we RESOLVE each axis:

  1. an explicit `resolution` from the caller (the type-resolution modal): a named
     column to read, or one value to apply to every row;
  2. failing that, inference — a known/synonym column name, else a column whose
     values are mostly valid host/sample types (value sniff);
  3. failing that, we return `needsTypeResolution` with the data's columns and the
     config's type taxonomy, so the caller can prompt, rather than erroring out.

Whatever resolves each axis is written into the config's standard column
(`host_common_name` / `sample_type`) and persisted to `data_path`, so the review
pass (metadata_review.py), which re-reads the file, sees the same thing.
"""

import io
import sys
import traceback

import pandas as pd
from metameq import (
    HOSTTYPE_SHORTHAND_KEY,
    QC_NOTE_KEY,
    SAMPLE_NAME_KEY,
    SAMPLETYPE_SHORTHAND_KEY,
    extract_config_dict,
    get_qc_failures,
)
from metameq.src.metadata_extender import _extend_metadata_from_full_flat_config

# Monkey-patch: metameq 2026.2.6 format_validation_msgs_as_df crashes on
# cerberus anyof errors because they produce nested dict error messages that
# pandas can't sort. Patch converts all error messages to strings first.
# Must patch in both modules since metadata_extender imports it directly.
import metameq.src.metadata_extender as _me  # noqa: E402
import metameq.src.metadata_validator as _mv  # noqa: E402

if not hasattr(_mv, "_orig_format_validation_msgs_as_df"):
    _mv._orig_format_validation_msgs_as_df = _mv.format_validation_msgs_as_df

    def _safe_format_validation_msgs_as_df(validation_msgs):
        for msg in validation_msgs:
            msg["error_message"] = [
                str(e) if not isinstance(e, str) else e for e in msg["error_message"]
            ]
        return _mv._orig_format_validation_msgs_as_df(validation_msgs)

    _mv.format_validation_msgs_as_df = _safe_format_validation_msgs_as_df
    _me.format_validation_msgs_as_df = _safe_format_validation_msgs_as_df

# Monkey-patch: metameq casts every field's whole column to its declared type
# before cerberus validates (metadata_validator.validate_metadata_df), and
# cast_field_to_type RAISES on the first uncastable cell — so one bad value (e.g.
# "metagenome" in the required-integer taxon_id field) aborts the entire validate
# AND the extension it runs inside, surfacing as a stack trace instead of a finding.
# The cast is validation in disguise: cerberus runs next and reports the same value
# as a normal type error ("must be of integer type"). So degrade the cast to return
# the raw value on failure, letting cerberus do the reporting. One bad cell becomes
# one finding, never a crash; castable cells are unaffected.
import metameq.src.util as _util  # noqa: E402

if not hasattr(_mv, "_orig_cast_field_to_type"):
    _mv._orig_cast_field_to_type = _mv.cast_field_to_type

    def _safe_cast_field_to_type(raw_field_val, allowed_pandas_types):
        try:
            return _mv._orig_cast_field_to_type(raw_field_val, allowed_pandas_types)
        except ValueError:
            return raw_field_val

    # validate_metadata_df resolves the name in its own module namespace; patch there
    # (and at the source) so every call site degrades.
    _mv.cast_field_to_type = _safe_cast_field_to_type
    _util.cast_field_to_type = _safe_cast_field_to_type


# Map QC note strings to the internal column key they relate to
_QC_NOTE_TO_INTERNAL_KEY = {
    "invalid host_type": HOSTTYPE_SHORTHAND_KEY,
    "invalid sample_type": SAMPLETYPE_SHORTHAND_KEY,
}

# Column names (besides the config's own options) that commonly hold the host or
# sample type, matched case-insensitively before falling back to a value sniff.
# Includes the taxon/organism columns MIxS-style studies carry instead of a host
# common name (the value is then a scientific name, which the review step maps to
# metameq's common-name host type, e.g. "Mus musculus" -> "mouse").
_HOST_COL_SYNONYMS = (
    "host_common_name",
    "host_type",
    "hosttype",
    "common_name",
    "host_taxon_id",
    "host_scientific_name",
    "host_organism",
    "host_species",
)
_SAMPLE_COL_SYNONYMS = ("sample_type", "sampletype", "sample_substance")

# Values that mean "blank/unknown", excluded from the value sniff's denominator.
_MISSINGISH = {
    "",
    "na",
    "n/a",
    "nan",
    "none",
    "null",
    "unknown",
    "not provided",
    "not applicable",
    "tbd",
    "?",
    "-",
    "--",
}

# A column is taken as holding a type if at least this fraction of its non-blank
# values are valid host/sample types. High enough that an incidental match (a free
# text column with one "human" in it) does not hijack the axis.
_SNIFF_THRESHOLD = 0.6


def _type_taxonomy(config: dict) -> dict:
    """The config's host/sample type vocabulary, for the resolution modal: the
    valid host types, the valid sample types under each, the reverse map (which
    hosts define a sample type), and the columns metameq reads each from."""
    hosts = config.get("host_type_specific_metadata") or {}
    sample_types_by_host = {
        host: sorted((cfg.get("sample_type_specific_metadata") or {}).keys())
        for host, cfg in hosts.items()
    }
    host_of_sample_type: dict[str, list[str]] = {}
    for host, sample_types in sample_types_by_host.items():
        for st in sample_types:
            host_of_sample_type.setdefault(st, []).append(host)
    return {
        "hostTypes": sorted(hosts.keys()),
        "sampleTypesByHost": sample_types_by_host,
        "hostOfSampleType": host_of_sample_type,
        "hostColumn": (
            config.get("hosttype_column_options") or [HOSTTYPE_SHORTHAND_KEY]
        )[0],
        "sampleColumn": (
            config.get("sampletype_column_options") or [SAMPLETYPE_SHORTHAND_KEY]
        )[0],
    }


def _infer_type_column(
    raw_df: pd.DataFrame, config: dict, axis: str, taxonomy: dict
) -> str | None:
    """The column in `raw_df` that holds the host (axis="host") or sample
    (axis="sample") type, or None if none can be identified.

    Checks, in order: the config's column options, a short synonym list (both by
    name, case-insensitively), then a value sniff — the column whose non-blank
    values are most-and-mostly valid types for this axis."""
    if axis == "host":
        options = config.get("hosttype_column_options") or []
        synonyms = _HOST_COL_SYNONYMS
        valid = {h.lower() for h in taxonomy["hostTypes"]}
    else:
        options = config.get("sampletype_column_options") or []
        synonyms = _SAMPLE_COL_SYNONYMS
        valid = {s.lower() for s in taxonomy["hostOfSampleType"]}

    by_lower = {str(c).strip().lower(): c for c in raw_df.columns}
    for candidate in list(options) + list(synonyms):
        hit = by_lower.get(str(candidate).strip().lower())
        if hit is not None:
            return hit

    best_col, best_frac = None, 0.0
    for col in raw_df.columns:
        values = [
            v
            for v in (str(x).strip().lower() for x in raw_df[col].tolist())
            if v not in _MISSINGISH
        ]
        if not values:
            continue
        frac = sum(1 for v in values if v in valid) / len(values)
        if frac >= _SNIFF_THRESHOLD and frac > best_frac:
            best_col, best_frac = col, frac
    return best_col


def _resolved_series(
    raw_df: pd.DataFrame,
    axis: str,
    axis_resolution: dict | None,
    taxonomy: dict,
    config: dict,
) -> tuple[pd.Series | None, dict | None]:
    """The host/sample-type values for every row and how they were resolved, or
    (None, None) when the axis cannot be resolved without prompting.

    `axis_resolution` (from the modal) wins: `{"value": v}` applies one type to all
    rows, `{"column": c}` reads an existing column. Otherwise inference runs."""
    axis_resolution = axis_resolution or {}
    value = axis_resolution.get("value")
    if value:
        return pd.Series([value] * len(raw_df), index=raw_df.index), {
            "mode": "value",
            "value": value,
        }
    column = axis_resolution.get("column")
    if column:
        if column not in raw_df.columns:
            raise ValueError(
                f"resolution named {axis} column {column!r}, not in the metadata"
            )
        return raw_df[column], {"mode": "column", "column": column}

    internal_key = (
        HOSTTYPE_SHORTHAND_KEY if axis == "host" else SAMPLETYPE_SHORTHAND_KEY
    )
    if internal_key in raw_df.columns:
        return raw_df[internal_key], {"mode": "present", "column": internal_key}

    inferred = _infer_type_column(raw_df, config, axis, taxonomy)
    if inferred is not None:
        return raw_df[inferred], {"mode": "inferred", "column": inferred}
    return None, None


def run_metameq_validation(
    data_path: str, config_path: str, file_ext: str, resolution: dict | None = None
):
    """
    Run METAMEQ validation on the provided data file.

    `resolution` (optional) is the type-resolution modal's answer, shaped
    ``{"host": {"column"|"value": ...}, "sample": {"column"|"value": ...}}``. When an
    axis cannot be resolved from it or by inference, the result carries
    ``needsTypeResolution`` with the data's columns and the config taxonomy instead
    of a hard "missing required columns" error.
    """
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()

    result = {
        "success": False,
        "processed": False,
        "errors": [],
        "extendedMetadata": None,
        "validationErrors": None,
        "originalColumns": None,
        "needsTypeResolution": False,
        "unresolved": [],
        "taxonomy": None,
        "resolvedContext": None,
        "stdout": "",
        "stderr": "",
        "summary": {"totalRows": 0, "validRows": 0, "errorCount": 0},
    }

    try:
        # 1. Load metadata — read headers from raw file to preserve blanks
        #    (pandas renames empty headers to "Unnamed: X")
        if file_ext in (".xlsx",):
            raw_df = pd.read_excel(data_path, dtype=str)
        else:
            sep = "," if file_ext == ".csv" else "\t"
            raw_df = pd.read_csv(data_path, sep=sep, dtype=str, header=0)
            with open(data_path, "r") as f:
                real_headers = f.readline().rstrip("\r\n").split(sep)
            if len(real_headers) == len(raw_df.columns):
                raw_df.columns = real_headers

        # 2. Capture original columns before resolution / metameq extension
        original_columns = list(raw_df.columns)
        result["originalColumns"] = original_columns

        # 3. Load config and its type taxonomy (also what the modal needs)
        config = extract_config_dict(config_path)
        taxonomy = _type_taxonomy(config)
        result["taxonomy"] = taxonomy

        # 4. Resolve the host and sample type for every row — from `resolution`,
        #    else by inference. An axis that resolves to neither needs the modal.
        host_series, host_ctx = _resolved_series(
            raw_df, "host", (resolution or {}).get("host"), taxonomy, config
        )
        sample_series, sample_ctx = _resolved_series(
            raw_df, "sample", (resolution or {}).get("sample"), taxonomy, config
        )
        result["resolvedContext"] = {"host": host_ctx, "sample": sample_ctx}

        unresolved = [
            axis
            for axis, series in (("host", host_series), ("sample", sample_series))
            if series is None
        ]
        if unresolved:
            result["needsTypeResolution"] = True
            result["unresolved"] = unresolved
            return result

        # 5. Write the resolved types into the config's standard columns and
        #    persist, so the review pass (which re-reads data_path) agrees.
        raw_df[taxonomy["hostColumn"]] = list(host_series)
        raw_df[taxonomy["sampleColumn"]] = list(sample_series)
        if file_ext in (".xlsx",):
            raw_df.to_excel(data_path, index=False)
        else:
            raw_df.to_csv(
                data_path, sep=("," if file_ext == ".csv" else "\t"), index=False
            )

        # 6. Extend metadata (metameq infers the shorthands from the standard columns)
        metadata_df, validation_msgs_df, col_name_mapping = (
            _extend_metadata_from_full_flat_config(raw_df, config, None, None, None)
        )

        result["processed"] = True

        # 7. Convert QC failures to validation message records
        qc_failures_df = get_qc_failures(metadata_df)
        qc_records = []
        for _, row in qc_failures_df.iterrows():
            qc_note = row[QC_NOTE_KEY]
            internal_key = _QC_NOTE_TO_INTERNAL_KEY.get(qc_note)
            if internal_key is not None:
                field_name = col_name_mapping.get(internal_key, internal_key)
                field_value = row[internal_key]
            else:
                field_name = qc_note
                field_value = None
            qc_records.append(
                {
                    SAMPLE_NAME_KEY: row[SAMPLE_NAME_KEY],
                    "field_name": field_name,
                    "field_value": field_value,
                    "error_message": qc_note,
                }
            )

        qc_msgs_df = pd.DataFrame(
            qc_records,
            columns=[SAMPLE_NAME_KEY, "field_name", "field_value", "error_message"],
        )

        # 8. Combine cerberus errors + QC messages, sort
        combined_df = pd.concat([validation_msgs_df, qc_msgs_df], ignore_index=True)
        combined_df.sort_values(
            by=[SAMPLE_NAME_KEY, "field_name", "error_message"], inplace=True
        )
        combined_df.reset_index(drop=True, inplace=True)

        # 9. Drop internal columns from metadata
        internal_cols = [HOSTTYPE_SHORTHAND_KEY, SAMPLETYPE_SHORTHAND_KEY, QC_NOTE_KEY]
        metadata_df = metadata_df.drop(columns=internal_cols)

        # Convert to result dicts
        metadata_df = metadata_df.where(metadata_df.notna(), None)
        result["extendedMetadata"] = metadata_df.astype(object).to_dict(
            orient="records"
        )

        if len(combined_df) > 0:
            combined_df = combined_df.where(combined_df.notna(), None)
            result["validationErrors"] = combined_df.astype(object).to_dict(
                orient="records"
            )

        # Derive summary — count unique failing samples, not individual errors
        total = len(result["extendedMetadata"])
        error_count = (
            len(result["validationErrors"]) if result["validationErrors"] else 0
        )

        if result["validationErrors"]:
            failing_samples = set(
                r[SAMPLE_NAME_KEY]
                for r in result["validationErrors"]
                if r.get(SAMPLE_NAME_KEY) is not None
            )
            valid_rows = total - len(failing_samples)
        else:
            valid_rows = total

        result["summary"]["totalRows"] = total
        result["summary"]["validRows"] = valid_rows
        result["summary"]["errorCount"] = error_count
        result["success"] = error_count == 0

    except Exception as e:
        tb = traceback.format_exc()
        result["errors"].append(
            {"message": f"{str(e)}\n\nTraceback:\n{tb}", "severity": "error"}
        )

    finally:
        result["stdout"] = sys.stdout.getvalue()
        result["stderr"] = sys.stderr.getvalue()
        sys.stdout, sys.stderr = old_stdout, old_stderr

    return result
