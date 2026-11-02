"""RSS 2.0 / Atom feeds. Parsed with defusedxml (safe against XML bombs / XXE)."""

from __future__ import annotations

import datetime as dt
import html
import logging
import re
from email.utils import parsedate_to_datetime

from defusedxml import ElementTree as ET

from .. import http
from ..config import Config
from ..util import clip
from .base import SourceError

log = logging.getLogger(__name__)

_ATOM = "{http://www.w3.org/2005/Atom}"
_CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"


def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def _clean(s: str) -> str:
    s = re.sub(r"<(script|style).*?</\1>", " ", s or "", flags=re.S | re.I)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"\s+", " ", s).strip()


def _parse_date(s: str) -> dt.datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def parse_feed(xml_text: str, name: str, max_items: int, max_age_hours: float,
               now: dt.datetime | None = None) -> list[dict]:
    now = now or dt.datetime.now(dt.timezone.utc)
    root = ET.fromstring(xml_text)
    entries = root.findall(f".//{_ATOM}entry") or root.findall(".//item")
    posts: list[dict] = []
    for e in entries:
        title = _clean(_text(e.find(f"{_ATOM}title")) or _text(e.find("title")))
        link = ""
        for l_el in e.findall(f"{_ATOM}link"):
            if l_el.get("rel", "alternate") == "alternate":
                link = l_el.get("href", "")
                break
        link = link or _text(e.find("link"))
        summary = (_text(e.find(_CONTENT)) or _text(e.find("description"))
                   or _text(e.find(f"{_ATOM}content")) or _text(e.find(f"{_ATOM}summary")))
        when = _parse_date(_text(e.find("pubDate")) or _text(e.find(f"{_ATOM}published"))
                           or _text(e.find(f"{_ATOM}updated")) or _text(e.find("date")))
        if when and max_age_hours and (now - when).total_seconds() > max_age_hours * 3600:
            continue
        if not title or not link:
            continue
        author = (_text(e.find("author")) or _text(e.find(f"{_ATOM}author/{_ATOM}name"))
                  or name)
        posts.append({
            "origin": "rss",
            "_source": f"rss:{name}",
            "title": title,
            "author": author,
            "headline": name,
            "body": clip(_clean(summary), 1500),
            "url": link,
            "links": [link],
            "published": when.isoformat() if when else "",
            "timestamp": when.date().isoformat() if when else "",
        })
        if len(posts) >= max_items:
            break
    return posts


def collect(cfg: Config):
    c = cfg.get("sources.rss")
    posts: list[dict] = []
    errors: list[str] = []
    for feed in c["feeds"]:
        name, url = feed.get("name") or feed["url"], feed["url"]
        try:
            xml_text = http.get(url).text
            posts += parse_feed(xml_text, name, int(c["max_per_feed"]), float(c["max_age_hours"]))
        except (http.HTTPError, ET.ParseError, ValueError) as e:
            errors.append(f"{name}: {e}")
    if errors and not posts:
        raise SourceError("RSS: " + "; ".join(errors))
    for e in errors:
        log.warning("[rss] %s", e)
    return posts
