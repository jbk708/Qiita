"""Unit tests for read_staging's per-slot DuckDB caps.

`per_slot_caps` splits a memory budget across `concurrency` in-flight
samples/runs. Under SLURM the budget is the cgroup; off SLURM the literal is a
ceiling bounded by detected host RAM, the same contract as
`resolve_duckdb_memory_gb` — these tests pin that bound (and its fail-soft).
"""

from __future__ import annotations

from qiita_compute_orchestrator import read_staging


def _caps(
    monkeypatch,
    ram: int | None,
    *,
    concurrency: int = 2,
    threads: int = 4,
    fallback_memory_gb: int = 7,
) -> tuple[int, int]:
    """per_slot_caps off SLURM with `ram` as the detected host RAM."""
    monkeypatch.delenv("SLURM_MEM_PER_NODE", raising=False)
    monkeypatch.setattr(read_staging, "detected_ram_gb", lambda: ram)
    return read_staging.per_slot_caps(
        concurrency, threads=threads, fallback_memory_gb=fallback_memory_gb
    )


def test_roomy_host_keeps_literal(monkeypatch):
    assert _caps(monkeypatch, 128) == (7, 4)


def test_small_host_gets_even_share_of_ram(monkeypatch):
    # 12 GB host, 2 slots x 4 threads: usable = 12 - headroom(8) = 6, so each
    # slot gets 6 // 2 = 3 instead of the 7 GB literal the host can't back.
    assert _caps(monkeypatch, 12) == (3, 4)


def test_tiny_host_floors_at_one(monkeypatch):
    # usable = 7 - headroom(8) = 1 → 1 // 2 = 0 → the same >= 1 floor resolve has.
    assert _caps(monkeypatch, 7) == (1, 4)


def test_detection_failure_keeps_literal(monkeypatch):
    assert _caps(monkeypatch, None) == (7, 4)


def test_under_slurm_detected_ram_is_irrelevant(monkeypatch):
    # The cgroup wins under SLURM: (48 - headroom(8)) // 2 = 21, and a 4 GB
    # detected-RAM reading must not shrink it.
    monkeypatch.setenv("SLURM_MEM_PER_NODE", str(48 * 1024))
    monkeypatch.setattr(read_staging, "detected_ram_gb", lambda: 4)
    assert read_staging.per_slot_caps(2, threads=4, fallback_memory_gb=7) == (21, 4)
