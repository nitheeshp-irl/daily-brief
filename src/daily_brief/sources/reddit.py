"""Reddit top-of-day via the public Atom feeds (the anonymous JSON API returns 403)."""

from __future__ import annotations

import html
import logging
import re
import time

from defusedxml import ElementTree as ET

from .. import http
from ..config import Config
from ..util import clip
from .base import SourceError
from .rss import _ATOM, _clean, _parse_date, _text

log = logging.getLogger(__name__)
_HREF = re.compile(r'href="([^"]+)"')
_NOISE = re.compile(r"submitted by\s+/u/\S+|\[link\]|\[comments\]", re.I)


def _is_reddit(url: str) -> bool:
    return bool(re.match(r"https?://([\w.-]+\.)?(reddit\.com|redd\.it|redditmedia\.com)/", url))


def parse_atom(xml_text: str, sub: str, limit: int) -> list[dict]:
    root = ET.fromstring(xml_text)
    posts: list[dict] = []
    for e in root.findall(f"{_ATOM}entry"):
        title = _clean(_text(e.find(f"{_ATOM}title")))
        link_el = e.find(f"{_ATOM}link")
        permalink = link_el.get("href", "") if link_el is not None else ""
        if not title or not permalink:
            continue
        content = html.unescape(_text(e.find(f"{_ATOM}content")))
        external = next((h for h in _HREF.findall(content) if not _is_reddit(h)), "")
        author = _text(e.find(f"{_ATOM}author/{_ATOM}name")).removeprefix("/u/")
        when = _parse_date(_text(e.find(f"{_ATOM}updated")))
        link = external or permalink
        posts.append({
            "origin": "reddit",
            "_source": f"reddit:r/{sub}",
            "title": title,
            "author": author,
            "body": clip(_NOISE.sub("", _clean(content)).strip(), 1500),
            "url": link,
            "links": [link] if link == permalink else [link, permalink],
            "published": when.isoformat() if when else "",
            "timestamp": when.date().isoformat() if when else "",
        })
        if len(posts) >= limit:
            break
    return posts


def collect(cfg: Config):
    c = cfg.get("sources.reddit")
    posts: list[dict] = []
    errors: list[str] = []
    for i, sub in enumerate(c["subreddits"]):
        if i:
            time.sleep(float(c.get("delay_seconds", 2)))  # Reddit throttles bursts
        try:
            xml_text = http.get(f"https://www.reddit.com/r/{sub}/top/.rss",
                                params={"t": "day", "limit": int(c["max_per_sub"])}).text
            posts += parse_atom(xml_text, sub, int(c["max_per_sub"]))
        except (http.HTTPError, ET.ParseError, ValueError) as e:
            errors.append(f"r/{sub}: {e}")
    if errors and not posts:
        raise SourceError("Reddit: " + "; ".join(errors)
                          + " (Reddit rate-limits anonymous clients; try again later)")
    for e in errors:
        log.warning("[reddit] %s", e)
    return posts
