"""Heuristic relevance scoring + human-readable reasons.

Everything here is a *heuristic* inferred from the data we have; reasons are labelled as such
in the brief. Feedback weights (from `daily-brief feedback up/down`) nudge the ranking.
"""

from __future__ import annotations

import datetime as dt
import math
import re

from ..config import Config
from ..models import origin_of
from ..util import domain_of

_STRENGTH = {"high": 3, "medium": 2, "low": 1}
_NETWORK = {
    "likes this": "a connection liked this",
    "commented": "a connection commented on this",
    "reposted": "a connection reposted this",
    "shared": "a connection shared this",
    "followed by": "someone in your network follows this author",
    "celebrated": "a connection celebrated this",
}


def features_for(post: dict) -> list[str]:
    """Keys the feedback loop learns weights for."""
    feats = [f"origin:{origin_of(post)}"]
    if post.get("author"):
        feats.append(f"author:{post['author'].lower()}")
    if post.get("url"):
        d = domain_of(post["url"])
        if d:
            feats.append(f"domain:{d}")
    feats += [f"topic:{g}" for g in post.get("matched_topics", [])]
    feats += [f"kw:{k}" for k in post.get("matched_keywords", [])[:5]]
    return feats


class Scorer:
    def __init__(self, cfg: Config, weights: dict[str, float] | None = None,
                 now: dt.datetime | None = None):
        self.weights = weights or {}
        self.max_bonus = float(cfg.get("feedback.max_bonus"))
        self.now = now or dt.datetime.now(dt.timezone.utc)

    def feedback_bonus(self, feats: list[str]) -> float:
        total = sum(self.weights.get(f, 0.0) for f in feats)
        return max(-self.max_bonus, min(self.max_bonus, total))

    def annotate(self, post: dict) -> dict:
        signals = self._signals(post)
        feats = features_for(post)
        bonus = self.feedback_bonus(feats)
        if abs(bonus) >= 0.5:
            signals.append({
                "type": "your_feedback", "strength": "medium",
                "reason": f"Your past feedback {'boosts' if bonus > 0 else 'lowers'} this "
                          f"({bonus:+.1f})"})
        post["signals"] = signals
        post["_features"] = feats
        score = 10.0 if post.get("_priority") == "high" else 0.0
        score += sum(_STRENGTH.get(s["strength"], 0) for s in signals if s["type"] != "your_feedback")
        score += bonus
        score -= 2 * len(post.get("quality_flags", []))
        post["score"] = round(score, 2)
        return post

    def annotate_all(self, posts: list[dict]) -> list[dict]:
        for p in posts:
            self.annotate(p)
        return sorted(posts, key=lambda p: p["score"], reverse=True)

    def _signals(self, post: dict) -> list[dict]:
        out: list[dict] = []
        origin, source = origin_of(post), post.get("_source", "")

        kws = post.get("matched_keywords", [])
        if kws:
            out.append({"type": "topic_match", "strength": "high" if len(kws) >= 3 else "medium",
                        "reason": f"Matches your topics: {', '.join(kws[:6])}"})

        if origin == "linkedin":
            rs = post.get("relevance_signal", "direct")
            if rs == "direct":
                out.append({"type": "network_1st_degree", "strength": "high",
                            "reason": "From an author you follow / are connected to"})
            elif rs != "explicit_save":
                for key, desc in _NETWORK.items():
                    if key in rs.lower():
                        out.append({"type": "network_2nd_degree", "strength": "medium",
                                    "reason": f"Surfaced via your network: {desc}"})
                        break
            if source.startswith("search:"):
                out.append({"type": "interest_search", "strength": "medium",
                            "reason": f"Matched your search: '{source[7:][:60]}'"})

        if source in ("saved", "saved_article"):
            out.append({"type": "explicit_save", "strength": "high",
                        "reason": "You deliberately saved this"})

        points = post.get("points") or 0
        if points:
            label = {"hackernews": "points on Hacker News", "reddit": "upvotes on Reddit",
                     "github": "stars on GitHub"}.get(origin, "upvotes")
            strength = "high" if points >= 500 else "medium" if points >= 100 else "low"
            extra = f", {post['comments']} comments" if post.get("comments") else ""
            out.append({"type": "popularity", "strength": strength,
                        "reason": f"{points} {label}{extra}"})

        age = self._age_hours(post)
        if age is not None and age <= 24:
            out.append({"type": "recency", "strength": "low",
                        "reason": f"Posted ~{max(1, int(age))}h ago"})
        return out

    def _age_hours(self, post: dict) -> float | None:
        pub = post.get("published")
        if pub:
            try:
                d = dt.datetime.fromisoformat(pub.replace("Z", "+00:00"))
                if d.tzinfo is None:
                    d = d.replace(tzinfo=dt.timezone.utc)
                return (self.now - d).total_seconds() / 3600
            except ValueError:
                pass
        m = re.match(r"^(\d+)\s*h", post.get("timestamp", ""))  # LinkedIn "5h"
        return float(m.group(1)) if m else None


def popularity_log(points: int) -> float:
    return math.log10(points + 1)
