"""Source registry. Each source is isolated: one failing never stops the others."""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from ..config import Config
from . import github, hackernews, reddit, rss
from .base import SourceError, SourceResult

log = logging.getLogger(__name__)


def _linkedin(cfg: Config) -> SourceResult:
    from .linkedin import collect  # lazy: needs the [linkedin] extra

    return collect(cfg)


def _simple(name: str, fn: Callable[[Config], list[dict]]) -> Callable[[Config], SourceResult]:
    def run(cfg: Config) -> SourceResult:
        return SourceResult(name=name, posts=fn(cfg))

    return run


REGISTRY: dict[str, Callable[[Config], SourceResult]] = {
    "hackernews": _simple("hackernews", hackernews.collect),
    "reddit": _simple("reddit", reddit.collect),
    "rss": _simple("rss", rss.collect),
    "github_trending": _simple("github_trending", github.collect),
    "linkedin": _linkedin,
}


def enabled_sources(cfg: Config) -> list[str]:
    return [n for n in REGISTRY if cfg.get(f"sources.{n}.enabled", False)]


def _safe(name: str, cfg: Config) -> SourceResult:
    try:
        res = REGISTRY[name](cfg)
        log.info("[%s] %d posts", name, len(res.posts))
        return res
    except SourceError as e:
        log.error("[%s] %s", name, e)
        return SourceResult(name=name, error=str(e))
    except Exception as e:  # a source bug must not kill the run
        log.exception("[%s] unexpected failure", name)
        return SourceResult(name=name, error=f"unexpected error: {e}")


def collect_all(cfg: Config, only: list[str] | None = None) -> dict[str, SourceResult]:
    names = [n for n in enabled_sources(cfg) if not only or n in only]
    if not names:
        return {}
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        futures = {n: pool.submit(_safe, n, cfg) for n in names}
        return {n: f.result() for n, f in futures.items()}
