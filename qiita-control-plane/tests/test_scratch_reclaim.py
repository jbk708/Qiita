import os

import pytest

from qiita_control_plane.routes.work_ticket import _RUN_APPLICABLE_STATES
from qiita_control_plane.scratch_reclaim import (
    RECLAIMABLE_STATES,
    measure_workspace,
    reclaim_workspace,
)


def test_no_reclaimable_state_can_be_run_again():
    assert not set(RECLAIMABLE_STATES) & set(_RUN_APPLICABLE_STATES)


def _make_ticket(root, idx, *, attempts=2):
    ticket = root / str(idx)
    for attempt in range(1, attempts + 1):
        attempt_dir = ticket / "align" / f"attempt-{attempt}"
        for sub, name, data in [
            ("input", "params.json", b"{}"),
            ("output", "data.bin", b"x" * 100),
            ("output", "manifest.json", b"{}"),
            ("tmp", "scratch", b"t"),
            ("logs", "stdout", f"out-{attempt}".encode()),
            ("logs", "stderr", b""),
        ]:
            (attempt_dir / sub).mkdir(parents=True, exist_ok=True)
            (attempt_dir / sub / name).write_bytes(data)
        (attempt_dir / "runner.json").write_text("{}")
    return ticket


def test_reclaim_keeps_every_attempts_logs_and_removes_the_rest(tmp_path):
    ticket = _make_ticket(tmp_path, 7)

    assert reclaim_workspace(tmp_path, 7) == []

    for attempt in (1, 2):
        attempt_dir = ticket / "align" / f"attempt-{attempt}"
        assert sorted(p.name for p in attempt_dir.iterdir()) == ["logs"]
        assert (attempt_dir / "logs" / "stdout").read_bytes() == f"out-{attempt}".encode()
        assert (attempt_dir / "logs" / "stderr").exists()


def test_a_file_that_cannot_be_unlinked_does_not_stop_the_other_attempts(tmp_path, monkeypatch):
    ticket = _make_ticket(tmp_path, 7)
    real_unlink = os.unlink

    def unlink(path, *args, **kwargs):
        if os.path.basename(path) == "runner.json":
            raise PermissionError(13, "denied", path)
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", unlink)

    errors = reclaim_workspace(tmp_path, 7)

    assert len(errors) == 2 and all("runner.json" in e for e in errors)
    for attempt in (1, 2):
        attempt_dir = ticket / "align" / f"attempt-{attempt}"
        assert sorted(p.name for p in attempt_dir.iterdir()) == ["logs", "runner.json"]


def test_reclaim_unlinks_symlinks_without_following_them(tmp_path):
    root = tmp_path / "ticket"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    ticket = _make_ticket(root, 7, attempts=1)
    output = ticket / "align" / "attempt-1" / "output"
    (output / "link-dir").symlink_to(outside, target_is_directory=True)
    (output / "link-file").symlink_to(outside / "keep.txt")
    (ticket / "align" / "attempt-1" / "top-link").symlink_to(outside, target_is_directory=True)

    assert reclaim_workspace(root, 7) == []

    assert (outside / "keep.txt").read_text() == "keep"
    assert not os.path.lexists(output / "link-dir")
    assert not os.path.lexists(ticket / "align" / "attempt-1" / "top-link")


def test_a_ticket_dir_that_is_a_symlink_is_refused_and_its_target_untouched(tmp_path):
    root = tmp_path / "ticket"
    root.mkdir()
    target = _make_ticket(tmp_path / "elsewhere", 7)
    (root / "7").symlink_to(target, target_is_directory=True)

    errors = reclaim_workspace(root, 7)

    assert len(errors) == 1 and "symlink" in errors[0]
    assert (target / "align" / "attempt-1" / "output" / "data.bin").exists()
    with pytest.raises(ValueError, match="symlink"):
        measure_workspace(root, 7)


def test_measure_counts_by_inode_and_only_frees_inodes_with_no_outside_link(tmp_path):
    root = tmp_path / "ticket"
    ticket = _make_ticket(root, 7, attempts=1)
    output = ticket / "align" / "attempt-1" / "output"
    (tmp_path / "staging").mkdir()
    os.link(output / "data.bin", tmp_path / "staging" / "data.bin")
    (output / "both.bin").write_bytes(b"y" * 50)
    os.link(output / "both.bin", output / "both-alias.bin")

    usage = measure_workspace(root, 7)

    # data.bin 100 is still linked outside; both.bin 50 counts once though linked twice
    assert usage.total_bytes == 162
    assert usage.reclaimable_bytes == 57
    assert usage.log_bytes == 5


def test_measure_reports_the_inodes_the_reclaim_leaves_behind(tmp_path):
    _make_ticket(tmp_path, 7, attempts=1)

    usage = measure_workspace(tmp_path, 7)

    # ticket/, align/, attempt-1/, logs/, stdout, stderr
    assert usage.kept_inodes == 6
    assert usage.inodes > usage.kept_inodes


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_a_subtree_that_cannot_be_deleted_is_reported_and_the_rest_still_goes(tmp_path):
    ticket = _make_ticket(tmp_path, 7, attempts=1)
    attempt_dir = ticket / "align" / "attempt-1"
    locked = attempt_dir / "output" / "locked"
    locked.mkdir()
    (locked / "f").write_text("x")
    locked.chmod(0o500)
    try:
        errors = reclaim_workspace(tmp_path, 7)
    finally:
        locked.chmod(0o700)

    assert errors and any("locked" in e for e in errors)
    assert not (attempt_dir / "tmp").exists()
    assert (attempt_dir / "logs" / "stdout").exists()
