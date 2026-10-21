"""HTTP status classification shared across components."""


def is_transient_http_status(status: int) -> bool:
    """True for a status a retry can self-heal: any 5xx, 408 or 429. Other 4xx
    (including 401/403) are permanent."""
    return status >= 500 or status in (408, 429)
