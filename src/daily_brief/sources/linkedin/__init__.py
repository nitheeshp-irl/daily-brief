"""LinkedIn source (opt-in). Read docs/linkedin.md before enabling.

Reads YOUR OWN logged-in browser session: home feed + topic searches through a third-party MCP
server, and your saved posts through a headless browser. Personal use only.
"""

from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path

from ...config import Config
from ..base import SourceError, SourceResult
from .errors import MCPError, ParserDrift, SessionExpired
from .parser import parse_posts

log = logging.getLogger(__name__)

__all__ = ["collect", "MCPError", "ParserDrift", "SessionExpired"]

_LOGIN_MARKERS = ("sign in", "log in", "join now", "authwall", "not logged in", "session expired")


def looks_like_login_wall(raw) -> bool:
    text = raw if isinstance(raw, str) else str(raw)
    low = text.lower()
    return len(text) < 3000 and any(m in low for m in _LOGIN_MARKERS)


def collect(cfg: Config) -> SourceResult:
    c = cfg.get("sources.linkedin")
    if not c.get("accept_tos"):
        raise SourceError(
            "LinkedIn is enabled but sources.linkedin.accept_tos is not true. LinkedIn's terms "
            "restrict automated access; read docs/linkedin.md and opt in explicitly.")
    return asyncio.run(_collect(cfg, c))


async def _pause(c: dict) -> None:
    lo, hi = c.get("delay_seconds", [2.0, 5.0])
    await asyncio.sleep(random.uniform(lo, hi))


async def _collect(cfg: Config, c: dict) -> SourceResult:
    from .mcp_client import MCPClient

    posts: list[dict] = []
    extra: dict = {}
    session_dir = Path(c["session_dir"]).expanduser()
    if not session_dir.exists():
        raise SessionExpired(f"No LinkedIn session at {session_dir}. "
                             "Run: daily-brief linkedin login")

    if c["use_feed"] or c["use_search"]:
        async with MCPClient(c["mcp_package"], c["mcp_version"], c["retries"]) as mcp:
            prof = await mcp.call("get_my_profile", {}, timeout=60)
            if looks_like_login_wall(prof):
                raise SessionExpired("LinkedIn session expired. Run: daily-brief linkedin login")
            if isinstance(prof, dict):
                extra["profile"] = {
                    "main": prof.get("sections", {}).get("main_profile", "")[:1000]}

            if c["use_feed"]:
                raw = await mcp.call("get_feed", {"num_posts": c["max_feed_posts"]}, timeout=120)
                feed = parse_posts(raw, "feed")
                if not feed and looks_like_login_wall(raw):
                    raise SessionExpired("LinkedIn session expired. "
                                         "Run: daily-brief linkedin login")
                nrefs = sum(len(v) for v in (raw.get("references") or {}).values()) \
                    if isinstance(raw, dict) else 0
                log.info("[linkedin] feed: %d posts, %d permalink refs, %d linked", len(feed),
                         nrefs, sum(1 for x in feed if x.get("url")))
                posts += feed

            if c["use_search"]:
                for query in c["search_queries"]:
                    await _pause(c)
                    raw = await mcp.call("search_posts", {
                        "keywords": query, "date_posted": "past_24_hours", "max_pages": 1},
                        timeout=60)
                    found = parse_posts(raw, f"search:{query[:50]}")
                    posts += found[: c["max_search_posts_per_query"]]
                    log.info("[linkedin] search %r: %d posts", query[:40], len(found))

    if c["use_saved"]:
        from .saved import fetch_saved_posts

        await _pause(c)
        posts += await fetch_saved_posts(session_dir, c["saved_posts_max_scrolls"])

    return SourceResult(name="linkedin", posts=posts, extra=extra)
