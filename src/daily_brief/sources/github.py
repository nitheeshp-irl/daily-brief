"""Fast-growing new repositories, via the official GitHub search API.

"Trending" has no official API, so this lists repos created in the last N days sorted by
stars, which is a good proxy. Set GITHUB_TOKEN to raise the rate limit.
"""

from __future__ import annotations

import datetime as dt
import logging
import os

from .. import http
from ..config import Config
from .base import SourceError

log = logging.getLogger(__name__)
API = "https://api.github.com/search/repositories"


def parse_repos(data: dict) -> list[dict]:
    posts = []
    for r in data.get("items", []):
        desc = r.get("description") or ""
        topics = r.get("topics") or []
        body = desc + (f"\nTopics: {', '.join(topics)}" if topics else "")
        posts.append({
            "origin": "github",
            "_source": "github_trending",
            "title": r.get("full_name", ""),
            "author": (r.get("owner") or {}).get("login", ""),
            "headline": r.get("language") or "",
            "body": body.strip(),
            "url": r.get("html_url", ""),
            "links": [r.get("html_url", "")],
            "published": r.get("created_at", ""),
            "timestamp": (r.get("created_at") or "")[:10],
            "points": r.get("stargazers_count") or 0,
        })
    return posts


def collect(cfg: Config):
    c = cfg.get("sources.github_trending")
    since = (dt.date.today() - dt.timedelta(days=int(c["since_days"]))).isoformat()
    base_q = f"created:>{since} stars:>={int(c['min_stars'])}"
    queries = [f"{base_q} topic:{t}" for t in c["topics"]] or [base_q]
    if c["languages"]:
        queries = [f"{q} language:{lang}" for q in queries for lang in c["languages"]]
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"

    posts: list[dict] = []
    seen: set[str] = set()
    errors: list[str] = []
    per_query = max(5, int(c["max_items"]))
    for q in queries[:8]:
        try:
            data = http.get_json(API, headers=headers, params={
                "q": q, "sort": "stars", "order": "desc", "per_page": per_query})
        except http.HTTPError as e:
            errors.append(str(e))
            continue
        for p in parse_repos(data):
            if p["url"] not in seen:
                seen.add(p["url"])
                posts.append(p)
    if errors and not posts:
        raise SourceError("GitHub: " + "; ".join(errors)
                          + " (set GITHUB_TOKEN if rate-limited)")
    posts.sort(key=lambda p: p.get("points", 0), reverse=True)
    return posts[: int(c["max_items"])]
