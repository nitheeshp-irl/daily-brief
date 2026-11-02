"""Reading-list triage: tag, age and flag stale items (your saved posts are a backlog)."""

from __future__ import annotations

import datetime as dt

from .config import Config
from .store import Store


def sync_saved(store: Store, cfg: Config, posts: list[dict], today: str) -> int:
    """Add saved items to the backlog with auto-tags from topic groups. Returns # new."""
    new = 0
    for p in posts:
        tags = list(p.get("matched_topics", [])) or ["untagged"]
        if store.upsert_backlog(p, today, tags):
            new += 1
    return new


def age_days(item: dict, today: str) -> int:
    return (dt.date.fromisoformat(today) - dt.date.fromisoformat(item["first_seen"])).days


def stale_items(store: Store, cfg: Config, today: str) -> list[dict]:
    limit = int(cfg.get("triage.stale_days"))
    out = [i for i in store.backlog("unread") if age_days(i, today) >= limit]
    return sorted(out, key=lambda i: age_days(i, today), reverse=True)


def summary(store: Store, cfg: Config, today: str) -> dict:
    unread = store.backlog("unread")
    stale = stale_items(store, cfg, today)
    by_tag: dict[str, int] = {}
    for i in unread:
        for t in i["tags"]:
            by_tag[t] = by_tag.get(t, 0) + 1
    return {"unread": len(unread), "stale": len(stale),
            "stale_days": int(cfg.get("triage.stale_days")),
            "by_tag": dict(sorted(by_tag.items(), key=lambda kv: -kv[1])),
            "oldest": stale[: int(cfg.get("triage.show_in_brief"))]
            or unread[: int(cfg.get("triage.show_in_brief"))]}


def render_markdown(store: Store, cfg: Config, today: str) -> str:
    s = summary(store, cfg, today)
    if not s["unread"]:
        return ""
    lines = ["## 📚 Reading backlog", "",
             f"{s['unread']} unread · {s['stale']} stale (unread for ≥ {s['stale_days']} days)"]
    if s["by_tag"]:
        lines.append("Tags: " + ", ".join(f"{t} ({n})" for t, n in list(s["by_tag"].items())[:6]))
    lines.append("")
    for i in s["oldest"]:
        ref = i["id"][:7]
        link = f"[{i['title'][:80] or i['url']}]({i['url']})" if i["url"] else i["title"][:80]
        lines.append(f"- {link} — {age_days(i, today)}d old (ref: {ref}) "
                     f"`daily-brief backlog read {ref}`")
    return "\n".join(lines)
