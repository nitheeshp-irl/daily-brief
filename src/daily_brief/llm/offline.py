"""No-LLM brief: a deterministic digest. Used for --model none, tests, and LLM failures."""

from __future__ import annotations

from ..util import clip
from .prompt import TOPIC_TITLES


def _line(p: dict, show_why: bool = True) -> str:
    title = p.get("title") or clip(p.get("body", "").replace("\n", " "), 90) or "(untitled)"
    link = f"[{title}]({p['url']})" if p.get("url") else title
    who = f" — {p['author']}" if p.get("author") else ""
    snippet = clip((p.get("body") or "").replace("\n", " "), 180)
    why = ""
    if show_why and p.get("signals"):
        why = f"\n  *Why:* {p['signals'][0]['reason']} _(heuristic)_"
    return (f"- **{link}**{who}" + (f"\n  {snippet}" if snippet else "") + why
            + f" (ref: {p['id'][:7]})")


def render(posts: list[dict], topics: list[str], today: str, top_n: int = 5,
           per_group: int = 4) -> str:
    out = [f"# Daily Brief — {today}", "",
           "_Generated without an LLM (extractive digest). Re-run with `--model ollama` or a "
           "cloud model for summaries._", ""]
    saved = [p for p in posts if p.get("_priority") == "high"]
    rest = [p for p in posts if p.get("_priority") != "high" and not p.get("quality_flags")]
    used: set[str] = set()

    def take(items: list[dict], n: int) -> list[dict]:
        picked = [p for p in items if p["id"] not in used][:n]
        used.update(p["id"] for p in picked)
        return picked

    if top := take(rest, top_n):
        out += ["## 🔥 Top signals today", "", *(_line(p) for p in top), ""]
    for g in topics:
        group = [p for p in rest if g in p.get("matched_topics", [])]
        if items := take(group, per_group):
            out += [f"## {TOPIC_TITLES.get(g, g.title())}", "", *(_line(p) for p in items), ""]
    if saved:
        out += ["## 🔖 From your reading list", "", *(_line(p, False) for p in saved[:10]), ""]
    if repos := take([p for p in rest if p.get("origin") == "github"], 5):
        out += ["## 🔧 New tools & releases", "", *(_line(p, False) for p in repos), ""]
    if skim := take(rest, 8):
        out += ["## 👀 Worth skimming", "", *(_line(p, False) for p in skim), ""]
    return "\n".join(out)
