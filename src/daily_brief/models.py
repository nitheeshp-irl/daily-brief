"""The post dict that flows through the pipeline (documented as a TypedDict)."""

from __future__ import annotations

from typing import Any, TypedDict

from .util import post_id, primary_url


class Post(TypedDict, total=False):
    id: str  # stable hash (see util.post_id)
    origin: str  # linkedin | hackernews | reddit | rss | github
    _source: str  # finer-grained: feed | saved | saved_article | search:<q> | hn | reddit:<sub> ...
    _priority: str  # "high" for saved items
    title: str
    author: str
    headline: str
    timestamp: str  # human string ("3h") or ISO date
    published: str  # ISO 8601 when known
    body: str
    url: str
    links: list[str]
    relevance_signal: str  # linkedin network signal text
    points: int  # upstream popularity (HN points, reddit score, GitHub stars)
    comments: int
    # filled by the pipeline
    quality_flags: list[str]
    matched_topics: list[str]
    matched_keywords: list[str]
    signals: list[dict]
    score: float
    _features: list[str]


_LINKEDIN_SOURCES = ("feed", "saved", "saved_article")


def origin_of(post: dict[str, Any]) -> str:
    if post.get("origin"):
        return post["origin"]
    s = post.get("_source", "")
    if s in _LINKEDIN_SOURCES or s.startswith("search:"):
        return "linkedin"
    return s.split(":")[0] or "unknown"


def normalize_post(post: dict[str, Any]) -> dict[str, Any]:
    """Fill defaults so downstream code can rely on a consistent shape."""
    post.setdefault("title", post.get("article_title", ""))
    post.setdefault("author", "")
    post.setdefault("body", "")
    post.setdefault("links", [])
    post["origin"] = origin_of(post)
    post.setdefault("url", primary_url(post))
    post.setdefault("_priority", "high" if post.get("_source") in ("saved", "saved_article")
                    else "normal")
    post["id"] = post.get("id") or post_id(post)
    return post
