# Test layout and tiers

The three test tiers, what infrastructure each needs, and where the shared
fixtures live. Referenced from [`CLAUDE.md`](../CLAUDE.md).

The test suite is split into three tiers by the infrastructure each one needs:

- **Pure-unit** (no infrastructure): `make test`. Pure Python + Rust unit tests across all components. Excludes tests carrying the `db` marker.
- **Control-plane with DB**: `make test-control-plane-with-db`. Brings up Postgres on :5433 (or uses host Postgres via `QIITA_USE_HOST_POSTGRES=1`), applies dbmate migrations, and runs every control-plane test including the `db`-marked ones. Tests opt in either at module scope (`pytestmark = pytest.mark.db` — pulls every test in the file into the DB tier) or per-test (`@pytest.mark.db` decorator on the function — for mixed modules where only some tests need a DB).
- **Cross-component integration**: `make test-integration`. Same Postgres, plus builds the data-plane debug binary; runs the Python integration suite, then resets the `qiita_ducklake` catalog and runs the Rust DuckLake tests. System tests (`@pytest.mark.system`) are excluded — run those with `make test-system`.

**Shared fixtures across tiers**: the DB / session / OIDC-JWKS fixtures live in `qiita-control-plane/src/qiita_control_plane/testing/` and are imported by both the control-plane and integration conftests so they cannot drift.

**Teardown is by parent FK**: a test hands `teardown_entity_graph` (in `testing/db_teardown.py`) the idxs of its study, biosample and prep_sample, and the sweep deletes everything keyed on them, including rows a trigger or a cascade produced. The parents above that graph — pools, runs, principals — are the caller's own: it either deletes them with `delete_idxs` once the sweep returns, or calls one of the composed teardowns in the same module, which take a whole shape at once (`delete_principal` for a principal and its user row, `teardown_ena_study_graph` for an ENA import's studies together with the runs and pools they registered). A migration that adds a table carrying `study_idx`, `biosample_idx`, `prep_sample_idx` or `genome_idx` must add that table to `SWEEP_TIERS` in the same PR, or name it in `UNSWEPT_ENTITY_TABLES` with the reason; `test_sweep_tiers_matches_the_live_schema` fails on the schema alone under `make test-control-plane-with-db`, whether or not any test seeds a row into the new table.

**Postgres harness**: `docker-compose.yml` + `initdb/` live under `qiita-control-plane/tests/_postgres/` and are reused by both DB-bound tiers. Port `5433` (not `5432`) avoids collision with a host Postgres.

**DuckLake catalog reset between phases**: `make test-integration` runs the Python suite, drops and recreates the `qiita_ducklake` Postgres database, then runs the Rust suite. DuckLake pins `DATA_PATH` into the catalog at creation time and the two suites use different `DATA_PATH` values; reusing the catalog produces confusing "path mismatch" failures. The Python conftest has the same drop/recreate logic so a single phase is self-contained too — keep the two mechanisms in sync.
