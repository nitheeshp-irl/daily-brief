"""Convert the MCP server's scraped text into post dicts.

The server returns text like {"sections": {"feed": "Feed post\\nAuthor\\nRole\\nbody..."}};
we split on "Feed post" markers and pull out author / headline / timestamp / body.
"""

from __future__ import annotations

import re
from typing import Any

from ...util import extract_links

_UI_CHROME = {
    "Follow", "Connect", "Message", "Like", "Comment", "Share",
    "Repost", "Send", "Unfollow", "Report", "Save", "More",
    "Reactions", "1st", "2nd", "3rd", "• 1st", "• 2nd", "• 3rd",
}
_TIMESTAMP_RE = re.compile(r"^\d+\s*[hdwm]")
_SIGNAL_WORDS = ("likes this", "commented", "reposted", "shared",
                 "Followed by", "follows", "celebrated", "joined")
_ROLE_HINTS = (" at ", " | ", "followers", "connections", "Engineer", "Manager", "Director",
               "CEO", "Founder", "Developer", "Architect", "Lead", "Principal", "Head of",
               "VP ", "CTO", "Staff ", "Senior ", "Junior ")
# LinkedIn page furniture that sometimes lands in the feed text
_CHROME_NOISE = re.compile(
    r"(^Profile viewers|^Post impressions|^Start a post|Select feed view|"
    r"Reach career heights|^See who.s hiring|Reactivate Premium|Write article)",
    re.I | re.M,
)


def _norm(t: str) -> str:
    """ASCII letters/digits only, lowercase: stable across the Python and browser sides."""
    return "".join(c for c in (t or "").lower() if c.isascii() and c.isalnum())


def item_key(body: str, n: int = 40) -> str:
    return _norm(body)[:n]


def text_overlaps(a: str, b: str, win: int = 28) -> bool:
    """True if any `win`-char window of a's first 200 chars occurs in b (both normalised)."""
    a, b = _norm(a)[:200], _norm(b)
    return len(a) >= win and any(a[i:i + win] in b for i in range(0, len(a) - win + 1, 7))


def absolute(url: str) -> str:
    return url if url.startswith("http") else "https://www.linkedin.com" + url


_SLUG_END = re.compile(r"-(share|ugcPost|activity)-\d+.*$")


def slug_words(url: str) -> str:
    """'/posts/jane-doe_first-words-of-the-post-share-123-AbCd' -> 'firstwordsofthepost'."""
    if not url.startswith("/posts/"):
        return ""
    core = _SLUG_END.sub("", url[len("/posts/"):])
    if "_" in core:  # handle_words
        core = core.split("_", 1)[1]
    return _norm(core)


def attach_permalinks(posts: list[dict], refs: list[dict]) -> int:
    """Give each post its LinkedIn permalink. Match by text excerpt first, then by order when
    the counts agree. Returns how many posts got one."""
    refs = [r for r in refs if isinstance(r, dict) and r.get("url")]
    if not refs:
        return 0
    used: set[int] = set()
    n = 0
    for post in posts:
        body = post.get("body", "")
        for i, r in enumerate(refs):
            if i not in used and r.get("text") and (
                    text_overlaps(post.get("body", ""), r["text"])
                    or text_overlaps(r["text"], body)):
                post["url"] = absolute(r["url"])
                used.add(i)
                n += 1
                break
    # Fallback: the /posts/<slug> URL starts with the post's own first words. Only an unambiguous
    # match (one post, one ref) is used, so a post never gets someone else's link.
    for i, r in enumerate(refs):
        words = slug_words(r["url"])
        if i in used or len(words) < 14:
            continue
        hits = [q for q in posts if not q.get("url") and words[:36] in _norm(q.get("body", ""))[:600]]
        if len(hits) == 1:
            hits[0]["url"] = absolute(r["url"])
            used.add(i)
            n += 1
    return n


def parse_posts(raw: Any, source: str) -> list[dict]:
    """`source` is one of: "feed", "saved", "search:<query>"."""
    if isinstance(raw, list):
        for p in raw:
            p.setdefault("_source", source)
            p.setdefault("origin", "linkedin")
        return raw

    text = ""
    if isinstance(raw, dict):
        sections = raw.get("sections", {})
        text = (sections.get("feed") or sections.get("search_results")
                or sections.get("saved_posts") or "")
    elif isinstance(raw, str):
        text = raw
    posts = _parse_text(text, source) if text else []
    if isinstance(raw, dict) and posts:
        refs = [r for v in (raw.get("references") or {}).values() if isinstance(v, list)
                for r in v if isinstance(r, dict) and r.get("kind", "feed_post") == "feed_post"]
        attach_permalinks(posts, refs)
    return posts


def _parse_text(text: str, source: str) -> list[dict]:
    posts = []
    for chunk in (c.strip() for c in text.split("Feed post") if c.strip()):
        post = _parse_chunk(chunk, source)
        if post:
            posts.append(post)
    return posts


def _parse_chunk(chunk: str, source: str) -> dict | None:
    if _CHROME_NOISE.search(chunk):
        return None
    lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
    if len(lines) < 3:
        return None

    relevance_signal = "direct"
    author_start = 0
    if any(sw in lines[0] for sw in _SIGNAL_WORDS):
        relevance_signal = lines[0]
        author_start = 1

    author = ""
    for i in range(author_start, min(author_start + 3, len(lines))):
        if lines[i] not in _UI_CHROME and not _TIMESTAMP_RE.match(lines[i]):
            author = lines[i]
            author_start = i + 1
            break
    if not author:
        return None

    headline = ""
    ts_start = author_start
    for i, line in enumerate(lines[author_start: author_start + 5], start=author_start):
        if _TIMESTAMP_RE.match(line):
            ts_start = i
            break
        if line in _UI_CHROME:
            continue
        if any(kw in line for kw in _ROLE_HINTS):
            headline = line
            ts_start = i + 1

    timestamp = ""
    body_start = ts_start
    for i, line in enumerate(lines[ts_start: ts_start + 4], start=ts_start):
        if _TIMESTAMP_RE.match(line):
            timestamp = line.split("•")[0].strip()
            body_start = i + 1
            break

    body_lines = []
    for line in lines[body_start:]:
        if line in _UI_CHROME or line.startswith("… more"):
            break
        if re.match(r"^\d+$", line):
            continue
        body_lines.append(line)
    body = "\n".join(body_lines[:50]).strip()
    if len(body) < 40:
        return None

    return {
        "origin": "linkedin",
        "author": author,
        "headline": headline,
        "timestamp": timestamp,
        "relevance_signal": relevance_signal,
        "body": body,
        "links": extract_links(body),
        "_source": source,
        "_priority": "high" if source == "saved" else "normal",
    }
