"""One HTTP helper with a polite User-Agent and retry/backoff for transient errors."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from . import __version__

log = logging.getLogger(__name__)

USER_AGENT = f"daily-brief/{__version__} (personal briefing agent; +https://github.com/)"
_RETRY_STATUS = {429, 500, 502, 503, 504}


class HTTPError(RuntimeError):
    pass


def get(url: str, *, params: dict | None = None, headers: dict | None = None,
        timeout: float = 20, retries: int = 2) -> requests.Response:
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=hdrs, timeout=timeout)
            if r.status_code in _RETRY_STATUS and attempt < retries:
                wait = float(r.headers.get("Retry-After", 0) or 0) or 2 ** attempt
                time.sleep(min(wait, 10))
                continue
            if r.status_code >= 400:
                raise HTTPError(f"{url} -> HTTP {r.status_code}")
            return r
        except requests.RequestException as e:
            last = e
            if attempt < retries:
                time.sleep(2 ** attempt)
    raise HTTPError(f"{url}: {last}")


def get_json(url: str, **kw: Any) -> Any:
    try:
        return get(url, **kw).json()
    except ValueError as e:
        raise HTTPError(f"{url}: invalid JSON ({e})") from e
