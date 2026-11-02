"""Small helpers: URLs, ids, token estimates."""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_URL_RE = re.compile(r"https?://[^\s)\]>\"']+|\b(?:lnkd\.in|github\.com)/[^\s)\]>\"']+")
_TRACKING = re.compile(r"^(utm_.*|fbclid|gclid|mc_.*|ref|ref_src|trk.*|trackingId|igshid)$", re.I)


def extract_links(text: str) -> list[str]:
    """All URLs in a text blob, trailing punctuation stripped, order preserved, deduped."""
    out: list[str] = []
    for url in _URL_RE.findall(text or ""):
        url = url.rstrip(".,;:!?)")
        if url not in out:
            out.append(url)
    return out


def normalize_url(url: str) -> str:
    """Canonical form for comparing URLs: no scheme/www/fragment/tracking params/trailing slash."""
    url = (url or "").strip().rstrip(".,;:!?)")
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not _TRACKING.match(k)])
    path = parts.path.rstrip("/")
    return urlunsplit(("", host, path, query, "")).lstrip("/")


def domain_of(url: str) -> str:
    n = normalize_url(url)
    return n.split("/", 1)[0] if n else ""


def primary_url(post: dict[str, Any]) -> str:
    if post.get("url"):
        return post["url"]
    links = post.get("links") or []
    return links[0] if links else ""


def post_id(post: dict[str, Any]) -> str:
    """Stable 10-hex id: from origin + url when there is one, else author + body prefix."""
    origin = post.get("origin") or post.get("_source", "")
    url = normalize_url(post.get("url", ""))
    if url:
        key = f"{origin.split(':')[0]}|{url}"
    else:
        key = f"{origin.split(':')[0]}|{post.get('author', '')}|{(post.get('body') or '')[:150]}"
    return hashlib.sha1(key.encode()).hexdigest()[:10]


def estimate_tokens(text: str) -> int:
    """Rough estimate (~4 chars/token). Good enough for budgets and pre-flight cost."""
    return max(1, len(text) // 4)


def clip(text: str, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"
