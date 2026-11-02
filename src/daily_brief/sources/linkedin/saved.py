"""Saved posts and saved articles, read from your own logged-in browser session.

Uses patchright with the session that `daily-brief linkedin login` created. Must run AFTER the
MCP server has stopped (both want the same browser profile).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ...util import extract_links
from .errors import ParserDrift, SessionExpired
from .parser import item_key

log = logging.getLogger(__name__)

SAVED_POSTS_URL = "https://www.linkedin.com/my-items/saved-posts/"
SAVED_ARTICLES_URL = "https://www.linkedin.com/my-items/saved-posts/?filter=articles"

_SKIP_LINES = {
    "Skip to search", "Skip to main content", "Keyboard shortcuts",
    "Close jump menu", "Home", "Network", "Jobs", "Messaging",
    "Notifications", "More", "Me", "For Business", "Advertise",
    "My items", "Job tracker", "My learning", "Saved Posts",
    "Saved posts and articles", "All", "Articles",
    "0 notifications total", "new feed updates notifications",
    "Status is reachable",
    "Like", "Comment", "Repost", "Send", "Save",
    "Report this post", "Follow", "Connect",
}
_SKIP_RE = re.compile(
    r"^(• ?(1st|2nd|3rd)\+?$|\d+\+?$|\d+[hdwm]\s*•?\s*$|\d+[hdwm] Visible to everyone"
    r"|…see more|♻️ Repost to help|Status is reachable$)",
    re.IGNORECASE,
)
_STOP_WORDS = {"Like", "Comment", "Repost", "Send", "Save", "Report this post", "Follow", "Connect"}
_ROLE_KEYWORDS = [
    " at ", " | ", "Engineer", "Manager", "Director", "CEO", "Founder", "Architect", "Lead",
    "Principal", "Head of", "Advocate", "Evangelist", "Developer", "Creator", "Hero", "GTM",
    "CTO", "VP ", "Staff ", "Senior", "Consultant", "Specialist", "Analyst", "Builder",
    "Researcher", "Instructor",
]
_BOUNDARY = re.compile(r"View .+?[’'s]* profile\n?", re.IGNORECASE)
_NOT_AUTHOR = ("skip to", "keyboard", "notification", "saved post", "my items",
               "advertise", "my learning")


_ANCHORS: dict[str, list[dict]] = {}
# For every post permalink on the page: the normalised text of its first 12 ancestors, innermost
# first. Python then picks the smallest ancestor that contains exactly one saved item.
_ANCHOR_JS = """() => {
  const norm = s => (s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  const out = [], seen = new Set();
  for (const a of document.querySelectorAll('a[href*="/feed/update/"], a[href*="/posts/"]')) {
    const href = a.href.split('?')[0];
    if (seen.has(href)) continue;
    seen.add(href);
    const levels = [];
    let el = a;
    for (let i = 0; i < 12 && el.parentElement; i++) {
      el = el.parentElement;
      const t = norm(el.innerText).slice(0, 4000);
      if (!levels.length || t !== levels[levels.length - 1]) levels.push(t);
    }
    out.push({url: href, levels});
  }
  return out;
}"""


async def fetch_saved_posts(session_dir: Path, max_scrolls: int = 10) -> list[dict]:
    """Returns saved posts + saved articles (deduplicated). Raises on login wall / parser drift."""
    try:
        from patchright.async_api import async_playwright
    except ImportError as e:
        raise ParserDrift("patchright not installed: pip install 'daily-brief[linkedin]'") from e

    if not session_dir.exists():
        raise SessionExpired(f"No LinkedIn session at {session_dir}. "
                             "Run: daily-brief linkedin login")

    items: list[dict] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch_persistent_context(str(session_dir), headless=True)
        try:
            for url, source in ((SAVED_POSTS_URL, "saved"), (SAVED_ARTICLES_URL, "saved_article")):
                text = await _load_page(browser, url, max_scrolls)
                found = parse_saved_text(text, source)
                got = attach_saved_permalinks(found, _ANCHORS.get(url, []))
                log.info("[saved] %s: %d/%d with LinkedIn links", source, got, len(found))
                log.info("[saved] %s: %d items", source, len(found))
                if not found and len(text) > 500:
                    raise ParserDrift(
                        f"Loaded {url} ({len(text)} chars) but parsed 0 items; "
                        "LinkedIn's markup likely changed. Please open an issue.")
                items += found
        finally:
            await browser.close()

    seen: set[str] = set()
    unique = []
    for item in items:
        key = item.get("body", "")[:120]
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


async def _load_page(browser, url: str, max_scrolls: int) -> str:
    page = await browser.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)
        if re.search(r"/(login|authwall|checkpoint|uas/login)", page.url):
            raise SessionExpired("LinkedIn redirected to a login page. "
                                 "Run: daily-brief linkedin login")
        prev = 0
        for _ in range(max_scrolls):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)
            height = await page.evaluate("document.body.scrollHeight")
            if height == prev:
                break
            prev = height
        text = await page.inner_text("body")
        try:  # (permalink, nearby text) pairs, so saved items can link back to LinkedIn
            _ANCHORS[url] = await page.evaluate(_ANCHOR_JS)
        except Exception as e:  # permalinks are a bonus; never fail the run for them
            log.warning("[saved] could not read post links: %s", e)
            _ANCHORS[url] = []
        return text
    finally:
        await page.close()


def attach_saved_permalinks(items: list[dict], anchors: list[dict]) -> int:
    """An anchor belongs to an item when the smallest ancestor holding any item's opening words
    holds exactly that one item's. Ambiguous anchors are skipped: no link beats a wrong one."""
    keys = [item_key(i.get("body", "")) for i in items]
    n = 0
    for a in anchors:
        for level in a.get("levels", []):
            hits = [idx for idx, k in enumerate(keys) if len(k) >= 15 and k in level]
            if not hits:
                continue
            if len(hits) == 1 and not items[hits[0]].get("url"):
                items[hits[0]]["url"] = a["url"]
                n += 1
            break
    return n


def parse_saved_text(text: str, source: str) -> list[dict]:
    """Page text -> items. The author is the last clean line BEFORE each "View X's profile"."""
    items = []
    parts = _BOUNDARY.split(text)
    for i in range(1, len(parts)):
        prev = [ln.strip() for ln in parts[i - 1].splitlines() if ln.strip()]
        prev = [ln for ln in prev if ln not in _SKIP_LINES and not _SKIP_RE.match(ln)]
        if not prev:
            continue
        author = prev[-1]
        if (len(author) < 3 or re.match(r"^\d+\+?$", author)
                or any(kw in author.lower() for kw in _NOT_AUTHOR)):
            continue
        item = _parse_content(parts[i], author=author, source=source)
        if item:
            items.append(item)
    return items


def _parse_content(content: str, author: str, source: str) -> dict | None:
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    lines = [ln for ln in lines if ln not in _SKIP_LINES and not _SKIP_RE.match(ln)]
    if not lines:
        return None

    headline = ""
    body_start = 0
    for i, line in enumerate(lines[:6]):
        if re.match(r"^\d+[hdwm]", line):
            body_start = i
            break
        if len(line) < 120 and any(kw in line for kw in _ROLE_KEYWORDS):
            headline = line
            body_start = i + 1

    timestamp = ""
    for i, line in enumerate(lines[body_start: body_start + 3], start=body_start):
        m = re.match(r"^(\d+[hdwm])", line)
        if m:
            timestamp = m.group(1)
            body_start = i + 1
            break

    body_lines = []
    for line in lines[body_start:]:
        if line in _STOP_WORDS:
            break
        if re.match(r"^\d+$", line) or line.startswith("♻️"):
            continue
        body_lines.append(line)
    body = "\n".join(body_lines[:60]).strip()
    if len(body) < 40:
        return None

    title = ""
    if source == "saved_article" and body_lines:
        first = body_lines[0]
        if 15 < len(first) < 140 and first[0].isupper() and "#" not in first \
                and not re.match(r"^\d", first):
            title = first

    return {
        "origin": "linkedin",
        "author": author,
        "headline": headline,
        "timestamp": timestamp,
        "title": title,
        "relevance_signal": "explicit_save",
        "body": body,
        "links": extract_links(body),
        "_source": source,
        "_priority": "high",
    }
