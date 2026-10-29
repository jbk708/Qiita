"""`open_miint_conn` raises DuckDB's HTTP retry settings so a dropped connection to
ENA's file server is retried rather than failing the run."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler

import duckdb
import pytest
from qiita_common.api_paths import LOOPBACK_HOST
from qiita_common.duckdb_miint import MIINT_HTTP_RETRY_CONFIG
from qiita_common.testing.loopback import serve_on_loopback

from qiita_compute_orchestrator.miint import open_miint_conn

_RECORDS = 40
_FASTQ = "".join(f"@r{i}\nACGTACGTAC\n+\nIIIIIIIIII\n" for i in range(_RECORDS)).encode()
_DROPPED_CONNECTIONS = 5


@pytest.fixture
def miint_conn():
    conn = open_miint_conn()
    yield conn
    conn.close()


def _http_settings(conn) -> dict[str, tuple[float, str]]:
    """DuckDB reports the backoff as `2.0`, so values are compared as numbers."""
    rows = conn.execute(
        "SELECT name, value, scope FROM duckdb_settings() WHERE name LIKE 'http_retr%'"
    ).fetchall()
    return {name: (float(value), scope) for name, value, scope in rows}


def test_retry_settings_are_global_and_reach_cursors(miint_conn):
    """A session-scoped SET would leave a cursor, and miint's ENA client, on 3."""
    expected = {name: (float(value), "GLOBAL") for name, value in MIINT_HTTP_RETRY_CONFIG.items()}
    assert _http_settings(miint_conn) == expected
    assert _http_settings(miint_conn.cursor()) == expected


class _FlakyFastqHandler(BaseHTTPRequestHandler):
    """Drops the first connections on HEAD, then partway through a 206 body, then
    serves `_FASTQ` with Range support."""

    lock = threading.Lock()
    head_drops = 0
    body_drops = 0

    def _drop(self) -> None:
        self.close_connection = True
        self.connection.close()

    def do_HEAD(self) -> None:
        with type(self).lock:
            if type(self).head_drops < _DROPPED_CONNECTIONS:
                type(self).head_drops += 1
                self._drop()
                return
        self.send_response(200)
        self.send_header("Content-Length", str(len(_FASTQ)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self) -> None:
        start, end = 0, len(_FASTQ) - 1
        range_header = self.headers.get("Range")
        if range_header:
            lo, _, hi = range_header.removeprefix("bytes=").partition("-")
            start = int(lo)
            end = min(int(hi), end) if hi else end
        body = _FASTQ[start : end + 1]
        with type(self).lock:
            cut = type(self).body_drops < _DROPPED_CONNECTIONS
            if cut:
                type(self).body_drops += 1
        self.send_response(206 if range_header else 200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        if range_header:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(_FASTQ)}")
        self.end_headers()
        if cut:
            self.wfile.write(body[: len(body) // 2])
            self.wfile.flush()
            self._drop()
            return
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def flaky_fastq_url():
    _FlakyFastqHandler.head_drops = 0
    _FlakyFastqHandler.body_drops = 0
    with serve_on_loopback(_FlakyFastqHandler) as port:
        yield f"http://{LOOPBACK_HOST}:{port}/reads.fastq"


def _read_ids(conn, url: str) -> list[str]:
    return [r[0] for r in conn.execute("SELECT read_id FROM read_fastx(?)", [url]).fetchall()]


def test_read_survives_more_dropped_connections_than_duckdb_default(miint_conn, flaky_fastq_url):
    """Five dropped connections on HEAD and five cut bodies exceed DuckDB's default of
    3 retries; the raised setting recovers and every record comes back."""
    miint_conn.execute("SET threads=1")
    miint_conn.execute("SET GLOBAL http_retry_wait_ms=10")
    assert len(_read_ids(miint_conn, flaky_fastq_url)) == _RECORDS
    assert _FlakyFastqHandler.head_drops == _DROPPED_CONNECTIONS
    assert _FlakyFastqHandler.body_drops == _DROPPED_CONNECTIONS


def test_read_fails_when_retries_are_disabled(miint_conn, flaky_fastq_url):
    """Guards the test above: with no retries the same server is a hard failure."""
    miint_conn.execute("SET threads=1")
    miint_conn.execute("SET GLOBAL http_retry_wait_ms=10")
    miint_conn.execute("SET GLOBAL http_retries=0")
    with pytest.raises(duckdb.IOException, match="Server returned nothing"):
        _read_ids(miint_conn, flaky_fastq_url)
