"""Console + rotating file logging. Post content is never logged."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import Config


def setup(cfg: Config, verbose: bool = False) -> None:
    root = logging.getLogger()
    if getattr(root, "_daily_brief_configured", False):
        return
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    level = logging.DEBUG if verbose else logging.INFO
    root.setLevel(level)
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    root.addHandler(console)
    try:
        cfg.logs_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(cfg.logs_dir / "daily-brief.log", maxBytes=1_000_000,
                                 backupCount=5, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        root.addHandler(fh)
    except OSError:
        pass  # read-only dir: console logging is enough
    for noisy in ("urllib3", "asyncio", "aiohttp", "httpx", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._daily_brief_configured = True  # type: ignore[attr-defined]
