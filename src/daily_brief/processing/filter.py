"""Config-driven filtering. Nothing about any particular person's interests is hardcoded."""

from __future__ import annotations

import re
from collections.abc import Iterable

from ..config import Config
from ..models import origin_of

# Optional presets, enabled via filters.block_presets
PRESETS: dict[str, re.Pattern] = {
    "promoted": re.compile(r"\b(Promoted|Sponsored)\b", re.I),
    "job_ads": re.compile(
        r"(we.re hiring|now hiring|\bhiring\b.*\bapply\b|send (your )?(resume|CV)|"
        r"DM (me )?(your )?(CV|resume)|job opportunity for jobseekers)", re.I),
    "engagement_bait": re.compile(
        r"(comment (below|yes|no|agree)|tag someone|double tap|follow for more|like if you|"
        r"repost if|who else (agrees?|feels?)|am i the only one|hot take:|unpopular opinion:)",
        re.I),
    "ai_hype": re.compile(
        r"(\d+\s+AI tools (you )?(need|must know|should use)|"
        r"AI (is |will |has )?(chang|transform|revolution|replac)|"
        r"the future of (work|AI|tech) is here|embrace AI (or|today)|AI tools? you NEED)", re.I),
}
# Soft flags: always annotated, only dropped if filters.drop_flagged is true.
_FLAG_PRESETS = {"engagement_bait": "engagement_bait", "ai_hype": "generic_ai_hype"}


def compile_terms(terms: Iterable[str]) -> re.Pattern | None:
    """Whole-word, case-insensitive matcher. A trailing * makes a term a prefix match."""
    parts = []
    for t in terms:
        t = t.strip()
        if not t:
            continue
        wild = t.endswith("*")
        body = re.escape(t.rstrip("*").strip())
        parts.append(body + (r"\w*" if wild else ""))
    if not parts:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)", re.I)


def post_text(post: dict) -> str:
    return f"{post.get('title', '')}\n{post.get('body', '')}".strip()


class PostFilter:
    def __init__(self, cfg: Config):
        f = cfg.get("filters")
        self.min_chars = int(f["min_chars"])
        self.drop_flagged = bool(f["drop_flagged"])
        self.skip_topic_for = set(f["skip_topic_for"])
        self.block_terms = compile_terms(f["block"])
        self.block_regexes = [re.compile(r, re.I) for r in f["block_regex"]]
        self.presets = {n: PRESETS[n] for n in f["block_presets"] if n in PRESETS}
        unknown = [n for n in f["block_presets"] if n not in PRESETS]
        if unknown:
            raise ValueError(f"Unknown filters.block_presets: {unknown}. "
                             f"Valid: {sorted(PRESETS)}")
        self.topics = cfg.topics
        self.topic_re = compile_terms(k for kws in self.topics.values() for k in kws)
        self.topic_group_re = {g: compile_terms(kws) for g, kws in self.topics.items()}

    def _skips_topic(self, post: dict) -> bool:
        src = post.get("_source", "")
        return (src in self.skip_topic_for or origin_of(post) in self.skip_topic_for
                or src.split(":")[0] in self.skip_topic_for)

    def reason_to_drop(self, post: dict) -> str | None:
        text = post_text(post)
        is_saved = post.get("_source") in ("saved", "saved_article")
        for name, rx in self.presets.items():
            if name == "job_ads" and is_saved:
                continue  # you saved it on purpose
            if rx.search(text):
                return name
        if self.block_terms and self.block_terms.search(text):
            return "blocked_term"
        if any(rx.search(text) for rx in self.block_regexes):
            return "blocked_regex"
        if len(text) < self.min_chars:
            return "too_short"
        if self.drop_flagged and classify_flags(post):
            return "flagged"
        if self.topic_re and not is_saved and not self._skips_topic(post) \
                and not self.topic_re.search(text):
            return "off_topic"
        return None

    def apply(self, posts: list[dict]) -> tuple[list[dict], list[dict]]:
        """Returns (signal, noise). Signal posts get quality_flags and matched_topics."""
        signal, noise = [], []
        for p in posts:
            reason = self.reason_to_drop(p)
            if reason:
                noise.append({**p, "_filtered_reason": reason})
                continue
            p["quality_flags"] = classify_flags(p)
            p["matched_topics"], p["matched_keywords"] = self.match_topics(p)
            signal.append(p)
        return signal, noise

    def match_topics(self, post: dict) -> tuple[list[str], list[str]]:
        text = post_text(post)
        groups, keywords = [], []
        for g, rx in self.topic_group_re.items():
            if rx:
                hits = {m.group(0).lower() for m in rx.finditer(text)}
                if hits:
                    groups.append(g)
                    keywords += sorted(hits)
        return groups, keywords


def classify_flags(post: dict) -> list[str]:
    text = post_text(post)
    flags = [flag for preset, flag in _FLAG_PRESETS.items() if PRESETS[preset].search(text)]
    if post.get("origin", origin_of(post)) == "linkedin" and len(post.get("body", "")) < 100:
        flags.append("low_substance")
    return flags
