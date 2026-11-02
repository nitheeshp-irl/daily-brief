"""Privacy + safety: redaction before sending to a cloud LLM, link verification after."""

from __future__ import annotations

import re

from ..util import extract_links, normalize_url

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{8,}\d(?!\w)")


def _scrub(text: str) -> str:
    return _PHONE.sub("[phone]", _EMAIL.sub("[email]", text or ""))


def _label(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return f"Person {s}"


class Redactor:
    """Best-effort: swaps author names for pseudonyms and scrubs emails/phones.

    This is NOT anonymisation (post text can still identify people); it reduces what leaves
    your machine. Names are restored in the final brief, which stays local.
    """

    def __init__(self) -> None:
        self.mapping: dict[str, str] = {}

    def alias(self, name: str) -> str:
        if not name:
            return ""
        if name not in self.mapping:
            self.mapping[name] = _label(len(self.mapping))
        return self.mapping[name]

    def redact_post(self, post: dict) -> dict:
        out = dict(post)
        out["author"] = self.alias(post.get("author", ""))
        out["headline"] = ""
        for k in ("title", "body"):
            text = _scrub(post.get(k, ""))
            for real, fake in self.mapping.items():
                text = re.sub(re.escape(real), fake, text, flags=re.I)
            out[k] = text
        return out

    def restore(self, text: str) -> str:
        for real, fake in self.mapping.items():
            text = text.replace(fake, real)
        return text


def allowed_urls(posts: list[dict]) -> set[str]:
    allowed: set[str] = set()
    for p in posts:
        for u in [p.get("url", ""), *p.get("links", [])]:
            if u:
                allowed.add(normalize_url(u))
    return allowed


_MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")


def verify_links(text: str, posts: list[dict]) -> tuple[str, list[str]]:
    """Strip any URL the model produced that was not in the source data (hallucinated or
    injected). Returns (clean_text, removed_urls)."""
    allowed = allowed_urls(posts)
    removed: list[str] = []

    def md(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        if normalize_url(url) in allowed:
            return m.group(0)
        removed.append(url)
        return label or "[link removed]"

    text = _MD_LINK.sub(md, text)
    for url in extract_links(text):
        if normalize_url(url) not in allowed:
            removed.append(url)
            text = text.replace(url, "[link removed]")
    return text, removed


_REF = re.compile(r"\s*\(ref:\s*([^)\s]*)\s*\)")


def add_missing_refs(text: str, posts: list[dict]) -> str:
    """Make every `(ref: …)` trustworthy. Small models skip or reuse refs, so on any line that
    links to a known item the item's own ref replaces whatever the model wrote."""
    by_url: dict[str, str] = {}
    for p in posts:
        for u in [p.get("url", ""), *p.get("links", [])]:
            if u:
                by_url.setdefault(normalize_url(u), p["id"][:7])
    known = {p["id"][:7] for p in posts}
    out = []
    for line in text.splitlines():
        ref = next((by_url[n] for n in map(normalize_url, extract_links(line)) if n in by_url), None)
        if ref:
            line = f"{_REF.sub('', line)} (ref: {ref})"
        else:  # no linkable item on this line: drop refs that don't exist rather than mislead
            line = _REF.sub(lambda m: m.group(0) if m.group(1) in known else "", line)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_PLACEHOLDER = re.compile(r"\s*\((?:link|url)\)\s*$", re.I)


def _item_url(post: dict) -> str:
    return next((u for u in [post.get("url"), *post.get("links", [])] if u), "")


def _norm_title(t: str) -> str:
    return re.sub(r"\W+", " ", t).strip().lower()


def link_unlinked_items(text: str, posts: list[dict]) -> str:
    """Give every card its item's own link. Links come from the data, never from the model.

    Each `###` card heading is rebuilt as `[title](url) · source` from the item whose `(ref: …)`
    appears in the card, unless the model already linked it correctly (small models write
    `Title(link)` or put a category name there). Other lines naming an item `(ref: …)` without a
    link get a trailing [link], unless their card heading already has one.
    """
    from .prompt import display_title

    by_id = {p["id"][:7]: p for p in posts}
    by_ref = {r: _item_url(p) for r, p in by_id.items()}
    lines = text.splitlines()
    card_ref: dict[int, str] = {}  # heading line index -> ref found later in the same card
    start = None
    for n, line in enumerate(lines + ["# end"]):
        if _HEADING.match(line):
            start = n if line.startswith("###") else None
        elif start is not None and (m := _REF.search(line)) and m.group(1) in by_ref:
            card_ref.setdefault(start, m.group(1))

    by_title = {_norm_title(display_title(p)): r for r, p in by_id.items()}

    out, card_linked, skip_echo = [], False, False
    for n, line in enumerate(lines):
        h = _HEADING.match(line)
        if h:
            card_linked, skip_echo = "](" in line, False
            ref = card_ref.get(n) or (m.group(1) if (m := _REF.search(line)) else "")
            if not ref and line.startswith("###"):  # card with no ref at all: match by title
                head = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", h.group(2)).split(" · ")[0]
                ref = by_title.get(_norm_title(_PLACEHOLDER.sub("", head)), "")
            url = by_ref.get(ref, "")
            if line.startswith("###") and url and not (card_linked and f"]({url})" in line):
                post = by_id[ref]
                src = (post.get("_source") or "").split(":")[-1]
                line = f"{h.group(1)} [{display_title(post)}]({url})" + (f" · {src}" if src else "")
                card_linked, skip_echo = True, True
        elif skip_echo and line.startswith("**"):
            skip_echo = False  # the model repeated the title/url under the heading; drop it
            continue
        elif (m := _REF.search(line)):
            url = by_ref.get(m.group(1), "")
            if url and "](" not in line and not extract_links(line) and not card_linked:
                line = f"{line} [link]({url})"
        if line.strip():
            skip_echo = False
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


_ECHO = re.compile(r"(?m)^[ \t]*Items about .*same item format as above\.?[ \t]*\n?")


def strip_template_echo(text: str) -> str:
    """Small models sometimes copy the prompt's per-section instruction into the brief."""
    return _ECHO.sub("", text)


_EMPTY = re.compile(r"^\s*[*_]*\(?\s*no items?\b.*$", re.I)


def drop_empty_sections(text: str) -> str:
    """Remove `##` sections whose body is blank or only a 'no items' placeholder."""
    parts = re.split(r"(?m)^(?=## )", text)
    keep = []
    for part in parts:
        body = part.split("\n", 1)[1] if part.startswith("## ") and "\n" in part else None
        if body is not None and all(not ln.strip() or _EMPTY.match(ln) for ln in body.splitlines()):
            continue
        keep.append(part)
    return "".join(keep)


def drop_duplicate_cards(text: str) -> str:
    """Small models repeat an item under several topic sections. Keep a card's first appearance
    (a card = a `###` block, identified by its `(ref: …)`) and drop the later copies."""
    parts = re.split(r"(?m)^(?=#{1,3} )", text)
    seen: set[str] = set()
    keep = []
    for part in parts:
        if part.startswith("### "):
            m = _REF.search(part)
            if m and m.group(1):
                if m.group(1) in seen:
                    continue
                seen.add(m.group(1))
        keep.append(part)
    return "".join(keep)
