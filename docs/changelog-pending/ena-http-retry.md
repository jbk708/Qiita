### Fixed

- **An ENA read download survives dropped connections (#815).** The compute orchestrator's
  miint connections now retry a failed HTTP request up to 8 times over about a minute (the
  first at once, then waiting 500 ms and doubling), instead of DuckDB's 3 within half a
  second, so a burst of connections dropped by ENA's file server no longer fails the run.
