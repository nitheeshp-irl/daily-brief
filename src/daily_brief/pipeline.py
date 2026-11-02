"""collect -> normalise -> dedupe -> filter -> score -> triage -> synthesise -> verify -> save."""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from . import __version__, triage
from .config import Config
from .llm import prompt as prompt_mod
from .llm.cost import Usage, compute_cost, format_usage, preflight
from .llm.offline import render as render_offline
from .llm.providers import LLMError, get_provider, resolve
from .llm.safety import (
    add_missing_refs,
    drop_duplicate_cards,
    drop_empty_sections,
    link_unlinked_items,
    strip_template_echo,
    verify_links,
)
from .models import normalize_post
from .processing.filter import PostFilter
from .processing.scoring import Scorer
from .sources import SourceResult, collect_all
from .store import Store
from .util import clip, domain_of, estimate_tokens, normalize_url

log = logging.getLogger(__name__)
RAW_SCHEMA = 2


class RunError(Exception):
    pass


@dataclass
class RunOptions:
    model: str | None = None
    dry_run: bool = False
    refetch: bool = False
    force: bool = False
    sample: bool = False
    estimate_only: bool = False
    redact: bool | None = None
    only: list[str] | None = None
    use_history: bool = True
    today: str = field(default_factory=lambda: dt.date.today().isoformat())


@dataclass
class RunResult:
    date: str
    brief: str = ""
    brief_path: Path | None = None
    usage: Usage | None = None
    source_errors: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    skipped: str = ""


# ── raw cache ────────────────────────────────────────────────────────────────

def save_raw(cfg: Config, today: str, results: dict[str, SourceResult]) -> Path:
    cfg.raw_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.raw_dir / f"{today}.json"
    payload = {"schema": RAW_SCHEMA, "date": today, "sources": {
        n: {"error": r.error, "extra": r.extra, "posts": r.posts} for n, r in results.items()}}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    log.info("Raw data -> %s", path)
    return path


def load_raw(path: Path) -> dict[str, SourceResult] | None:
    data = json.loads(path.read_text())
    if data.get("schema") != RAW_SCHEMA:
        log.warning("Ignoring %s (old cache format); re-collecting", path.name)
        return None
    return {n: SourceResult(name=n, posts=s.get("posts", []), error=s.get("error", ""),
                            extra=s.get("extra", {})) for n, s in data["sources"].items()}


def load_sample() -> dict[str, SourceResult]:
    text = resources.files("daily_brief").joinpath("sample_raw.json").read_text(encoding="utf-8")
    data = json.loads(text)
    return {n: SourceResult(name=n, posts=s["posts"]) for n, s in data["sources"].items()}


# ── processing ───────────────────────────────────────────────────────────────

def dedupe(posts: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for p in posts:
        key = normalize_url(p.get("url", "")) or p["id"]
        if p["id"] in seen or key in seen:
            continue
        seen.update({p["id"], key})
        out.append(p)
    return out


def prepare(cfg: Config, store: Store, results: dict[str, SourceResult], today: str,
            use_history: bool = True) -> tuple[list[dict], dict[str, int]]:
    """Returns (ranked signal posts, counts)."""
    raw = [normalize_post(p) for r in results.values() for p in r.posts]
    posts = dedupe(raw)
    counts = {"collected": len(raw), "after_dedupe": len(posts)}

    if use_history:
        days = int(cfg.get("history.dedup_days"))
        fresh = [p for p in posts
                 if p.get("_priority") == "high" or not store.seen_before(p["id"], today, days)]
        counts["already_seen"] = len(posts) - len(fresh)
        posts = fresh

    signal, noise = PostFilter(cfg).apply(posts)
    counts["filtered_out"] = len(noise)
    reasons: dict[str, int] = {}
    for n in noise:
        reasons[n["_filtered_reason"]] = reasons.get(n["_filtered_reason"], 0) + 1
    if reasons:
        log.info("Filtered out: %s", ", ".join(f"{k}={v}" for k, v in sorted(reasons.items())))

    ranked = Scorer(cfg, store.weights()).annotate_all(signal)
    counts["signal"] = len(ranked)
    return ranked, counts


# ── assembly ─────────────────────────────────────────────────────────────────

def _links_cell(p: dict) -> str:
    """Every link we hold for an item (its own permalink first), so none depends on the LLM."""
    urls = list(dict.fromkeys(x for x in [p.get("url"), *(p.get("links") or [])] if x))
    out = []
    for i, u in enumerate(urls[:4], 1):
        label = "post" if "linkedin.com" in u and i == 1 else domain_of(u)[:24] or f"link {i}"
        out.append(f"[{label}]({u})")
    return " · ".join(out)


def sources_index(posts: list[dict]) -> str:
    rows = ["## 🧾 Sources index", "",
            "Rate items to tune future rankings: `daily-brief feedback up|down <ref>` · "
            "add to reading list: `daily-brief backlog add <ref>`", "",
            "| ref | source | item | links |", "|---|---|---|---|"]
    for p in posts:
        title = (p.get("title") or clip(p.get("body", "").replace("\n", " "), 70)
                 or p.get("author", "")).replace("|", "\\|")
        rows.append(f"| `{p['id'][:7]}` | {p.get('_source', '')[:30]} | {clip(title, 80)} "
                    f"| {_links_cell(p) or '—'} |")
    return "\n".join(rows)


def assemble(body: str, backlog_md: str, posts: list[dict], usage: Usage,
             errors: dict[str, str], removed: list[str], note: str = "") -> str:
    parts = [body.rstrip()]
    if backlog_md:
        parts.append(backlog_md)
    parts.append(sources_index(posts))
    foot = [f"_daily-brief {__version__} · {format_usage(usage)}_"]
    if removed:
        foot.append(f"_{len(removed)} unverified link(s) removed from the model output._")
    if note:
        foot.append(f"_{note}_")
    for name, err in errors.items():
        foot.append(f"> ⚠️ Source **{name}** unavailable: {err}")
    parts.append("\n\n".join(foot))
    return "\n\n".join(parts) + "\n"


def synthesise(cfg: Config, bundle: prompt_mod.PromptBundle, spec: str, posts: list[dict],
               today: str) -> tuple[str, Usage, str]:
    """Returns (markdown, usage, note). Falls back to the offline digest on LLM failure."""
    note = ""
    try:
        provider = get_provider(spec, cfg)
    except LLMError as e:
        raise RunError(str(e)) from e

    if provider is not None:
        try:
            comp = provider.complete(bundle.system, bundle.user, int(cfg.get("llm.max_output_tokens")))
            text = comp.text
            if bundle.redactor:
                text = bundle.redactor.restore(text)
            overrides = cfg.get("llm.pricing")
            cost = compute_cost(provider.name, provider.model, comp.input_tokens,
                                comp.output_tokens, overrides)
            usage = Usage(provider.model, provider.name, comp.input_tokens, comp.output_tokens,
                          comp.estimated, cost)
            return text, usage, note
        except LLMError as e:
            if not cfg.get("llm.fallback_offline"):
                raise RunError(str(e)) from e
            log.error("LLM failed (%s); writing the offline digest instead", e)
            note = f"LLM unavailable ({clip(str(e), 120)}); this is the offline digest."

    text = render_offline(posts, list(cfg.topics), today)
    return text, Usage("extractive", "offline", 0, 0, False, 0.0), note


# ── orchestration ────────────────────────────────────────────────────────────

def run(cfg: Config, opts: RunOptions, store: Store) -> RunResult:
    today = opts.today
    res = RunResult(date=today)
    brief_path = cfg.briefs_dir / f"{today}.md"
    spec = opts.model or cfg.get("llm.model")
    provider_name, model_name = resolve(spec, cfg)  # fail fast on a bad --model

    if brief_path.exists() and not (opts.force or opts.dry_run or opts.sample or opts.estimate_only):
        res.skipped = f"{brief_path} already exists (use --force to regenerate)"
        res.brief_path = brief_path
        return res

    # 1. collect (or reuse today's cache)
    raw_path = cfg.raw_dir / f"{today}.json"
    results: dict[str, SourceResult] | None = None
    if opts.sample:
        results = load_sample()
    elif raw_path.exists() and not opts.refetch:
        log.info("Using cached data %s (--refetch to re-collect)", raw_path)
        results = load_raw(raw_path)
    if results is None:
        fresh = collect_all(cfg, opts.only)
        if not fresh:
            raise RunError("No sources are enabled. Edit sources.* in config.yaml.")
        results = fresh
        if opts.only and raw_path.exists():  # --only refreshes just those sources
            results = {**(load_raw(raw_path) or {}), **{n: r for n, r in fresh.items()}}
        save_raw(cfg, today, results)
    res.source_errors = {n: r.error for n, r in results.items() if r.error}
    if all(r.error for r in results.values()):
        raise RunError("Every source failed: " + "; ".join(
            f"{n}: {e}" for n, e in res.source_errors.items()))

    # 2. process
    posts, res.counts = prepare(cfg, store, results, today, opts.use_history and not opts.sample)
    if not posts:
        raise RunError("No posts left after filtering. Loosen `topics`/`filters`, or run with "
                       "--refetch / --no-history.")
    saved = [p for p in posts if p.get("_priority") == "high"]
    if saved and not opts.sample:
        res.counts["backlog_new"] = triage.sync_saved(store, cfg, saved, today)

    # 3. prompt + preflight
    bundle = prompt_mod.build_prompt(cfg, posts, today,
                                     profile=next((r.extra.get("profile") for r in results.values()
                                                   if r.extra.get("profile")), None),
                                     redact=opts.redact)
    res.counts["sent_to_llm"] = len(bundle.included)
    provider, model = resolve(spec, cfg)
    if provider != "offline":
        log.info(preflight(spec, provider, model, bundle.input_tokens,
                           int(cfg.get("llm.max_output_tokens")), cfg.get("llm.pricing")))
    if opts.dry_run or opts.estimate_only:
        res.usage = Usage(model, provider, bundle.input_tokens, 0, True,
                          compute_cost(provider, model, bundle.input_tokens, 0, cfg.get("llm.pricing")))
        return res

    # 4. synthesise + verify
    text, usage, note = synthesise(cfg, bundle, spec, bundle.included, today)
    text, removed = verify_links(text, bundle.included)
    if usage.provider != "offline":  # the offline digest already prints refs
        text = link_unlinked_items(add_missing_refs(text, bundle.included), bundle.included)
        text = drop_empty_sections(drop_duplicate_cards(strip_template_echo(text)))
    brief = assemble(text, triage.render_markdown(store, cfg, today), bundle.included, usage,
                     res.source_errors, removed, note)

    # 5. save + remember
    if not opts.sample:
        cfg.briefs_dir.mkdir(parents=True, exist_ok=True)
        brief_path.write_text(brief)
        store.mark_seen([p["id"] for p in bundle.included], today)
        store.record_items(today, bundle.included)
        store.record_run(today, f"{usage.provider}:{usage.model}", usage.input_tokens,
                         usage.output_tokens, usage.cost_usd, usage.estimated,
                         len(bundle.included))
        res.brief_path = brief_path
    res.brief, res.usage = brief, usage
    return res


def estimate_text_tokens(text: str) -> int:  # re-exported for the CLI
    return estimate_tokens(text)
