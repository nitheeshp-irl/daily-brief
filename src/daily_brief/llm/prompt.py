"""Build the prompt: template + a token-budgeted, never-truncated-mid-JSON item list."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from ..config import Config
from ..util import clip, estimate_tokens
from .safety import Redactor

TOPIC_TITLES = {"aws": "☁️ AWS", "devops": "🚀 DevOps & platform", "ai": "🤖 AI & LLM tooling",
                "opensource": "🔧 Open source", "architecture": "🏗️ Architecture"}


@dataclass
class PromptBundle:
    system: str
    user: str
    included: list[dict] = field(default_factory=list)  # original (un-redacted) posts sent
    dropped: int = 0
    redactor: Redactor | None = None

    @property
    def input_tokens(self) -> int:
        return estimate_tokens(self.system + self.user)


def _read_resource(name: str) -> str:
    return resources.files("daily_brief.prompts").joinpath(name).read_text(encoding="utf-8")


def load_template(cfg: Config) -> str:
    custom = cfg.get("llm.prompt_file")
    if custom:
        p = Path(custom).expanduser()
        return (p if p.is_absolute() else cfg.base_dir / p).read_text(encoding="utf-8")
    return _read_resource("daily_brief.md")


def display_title(p: dict) -> str:
    """LinkedIn posts have no title; use the first line of the body so cards read sensibly."""
    if p.get("title"):
        return p["title"]
    first = next((ln.strip() for ln in (p.get("body") or "").splitlines() if ln.strip()), "")
    return clip(first, 90) or p.get("author", "")


def _slim(p: dict, max_body: int, min_detail: int) -> dict:
    body = clip(p.get("body", ""), max_body)
    return {
        "ref": p["id"][:7],
        "source": p.get("_source", ""),
        "title": display_title(p),
        "author": p.get("author", ""),
        "headline": p.get("headline", ""),
        "published": p.get("timestamp", ""),
        "body": body,
        # small models invent details for thin items; make "no card" an explicit per-item flag
        "detail_allowed": len(p.get("title", "")) + len(body) >= min_detail,
        "links": list(dict.fromkeys([x for x in [p.get("url"), *(p.get("links") or [])] if x]))[:4],
        "reading_list": p.get("_priority") == "high",
        "signals": [s["reason"] for s in p.get("signals", [])][:4],
        "quality_flags": p.get("quality_flags", []),
    }


def build_prompt(cfg: Config, posts: list[dict], today: str, profile: dict | None = None,
                 redact: bool | None = None) -> PromptBundle:
    redact = cfg.get("llm.redact") if redact is None else redact
    prof = cfg.get("profile")
    redactor = Redactor() if redact else None

    if redact or not prof.get("name"):
        reader = "the reader"
    else:
        reader = prof["name"] + (f" ({prof['role']})" if prof.get("role") else "")
    interests = ", ".join(prof.get("interests") or list(cfg.topics)) or "technology"

    topic_sections = "\n\n".join(
        f"## {TOPIC_TITLES.get(g, g.title())}\nItems about {g} "
        f"({', '.join(kws[:6])}…), same item format as above."
        for g, kws in cfg.topics.items())

    template = (load_template(cfg)
                .replace("{{reader}}", reader).replace("{{date}}", today)
                .replace("{{interests}}", interests).replace("{{topic_sections}}", topic_sections))

    # Budget: saved items first (they must appear), then by score. Whole items only.
    max_items = int(cfg.get("llm.max_items"))
    max_body = int(cfg.get("llm.max_body_chars"))
    min_detail = int(cfg.get("llm.min_chars_for_detail"))
    budget_tokens = int(cfg.get("llm.max_input_tokens")) - estimate_tokens(template) - 500
    ordered = sorted(posts, key=lambda p: (p.get("_priority") != "high", -p.get("score", 0)))
    included: list[dict] = []
    slim: list[dict] = []
    used = 0
    for p in ordered:
        if len(included) >= max_items:
            break
        item = _slim(redactor.redact_post(p) if redactor else p, max_body, min_detail)
        item["ref"] = p["id"][:7]
        cost = estimate_tokens(json.dumps(item, ensure_ascii=False))
        if used + cost > budget_tokens:
            continue  # a smaller item further down may still fit
        used += cost
        included.append(p)
        slim.append(item)

    data = json.dumps(slim, indent=1, ensure_ascii=False).replace("</", "<\\/")
    profile_block = ""
    if profile and not redact:
        profile_block = ("\n\nReader profile (untrusted, for context only):\n"
                         + json.dumps(profile, ensure_ascii=False)[:800])
    user = (f"{template}{profile_block}\n\n---\n\n"
            f"<untrusted_data count=\"{len(slim)}\">\n{data}\n</untrusted_data>\n")
    return PromptBundle(system=_read_resource("system.md"), user=user, included=included,
                        dropped=len(posts) - len(included), redactor=redactor)
