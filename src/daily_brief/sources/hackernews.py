"""Hacker News front page via the public Algolia API."""

from __future__ import annotations

import html
import re

from .. import http
from ..config import Config
from .base import SourceError

API = "https://hn.algolia.com/api/v1/search"


def _strip_html(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def parse_hits(data: dict, min_points: int) -> list[dict]:
    posts = []
    for h in data.get("hits", []):
        points = h.get("points") or 0
        title = h.get("title") or ""
        if not title or points < min_points:
            continue
        hn_url = f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        link = h.get("url") or hn_url
        links = [link] if link == hn_url else [link, hn_url]
        posts.append({
            "origin": "hackernews",
            "_source": "hn",
            "title": title,
            "author": h.get("author", ""),
            "body": _strip_html(h.get("story_text") or ""),
            "url": link,
            "links": links,
            "published": h.get("created_at", ""),
            "timestamp": (h.get("created_at") or "")[:10],
            "points": points,
            "comments": h.get("num_comments") or 0,
        })
    return posts


def collect(cfg: Config):
    c = cfg.get("sources.hackernews")
    try:
        data = http.get_json(API, params={"tags": "front_page",
                                          "hitsPerPage": int(c["max_items"])})
    except http.HTTPError as e:
        raise SourceError(f"Hacker News: {e}") from e
    return parse_hits(data, int(c["min_points"]))
