"""Feedback loop: vote on brief items, learn per-feature weights, re-rank future briefs."""

from __future__ import annotations

import json

from .config import Config
from .store import Store


def vote(store: Store, cfg: Config, ref: str, direction: int, note: str = "") -> dict:
    """direction: +1 (useful) or -1 (not useful). Returns what was learned."""
    if direction not in (1, -1):
        raise ValueError("direction must be +1 or -1")
    item = store.resolve("items", ref)
    feats = json.loads(item["features"] or "[]")
    lr = float(cfg.get("feedback.learning_rate"))
    store.add_feedback(item["id"], direction, note)
    store.bump_weights(feats, direction * lr, float(cfg.get("feedback.max_weight")))
    return {"id": item["id"], "title": item["title"], "features": feats,
            "delta": direction * lr}


def top_weights(store: Store, n: int = 10) -> tuple[list, list]:
    w = sorted(store.weights().items(), key=lambda kv: kv[1], reverse=True)
    return [x for x in w if x[1] > 0][:n], [x for x in reversed(w) if x[1] < 0][:n]
