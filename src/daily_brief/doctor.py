"""`daily-brief doctor`: check the setup and say what to fix."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import requests

from .config import Config
from .llm.providers import LLMError, resolve
from .sources import enabled_sources


@dataclass
class Check:
    status: str  # ok | warn | fail
    name: str
    detail: str = ""


def run_checks(cfg: Config, online: bool = False, model: str | None = None) -> list[Check]:
    out: list[Check] = []
    add = lambda s, n, d="": out.append(Check(s, n, d))  # noqa: E731

    add("ok" if sys.version_info >= (3, 10) else "fail", "Python", sys.version.split()[0])
    add("ok" if cfg.path else "warn", "Config",
        str(cfg.path) if cfg.path else "using built-in defaults (run `daily-brief init`)")
    try:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        probe = cfg.data_dir / ".write-test"
        probe.write_text("x")
        probe.unlink()
        add("ok", "Data directory writable", str(cfg.data_dir))
    except OSError as e:
        add("fail", "Data directory writable", str(e))

    srcs = enabled_sources(cfg)
    add("ok" if srcs else "fail", "Enabled sources", ", ".join(srcs) or "none")

    spec = model or cfg.get("llm.model")
    try:
        prov, mdl = resolve(spec, cfg)
    except LLMError as e:
        add("fail", "LLM model", str(e))
        prov, mdl = "", ""
    if prov == "offline":
        add("ok", "LLM", "offline digest (no LLM)")
    elif prov == "ollama":
        host = (os.environ.get("OLLAMA_HOST") or cfg.get("llm.ollama_host")).rstrip("/")
        host = host if host.startswith("http") else "http://" + host
        try:
            names = [m["name"] for m in requests.get(f"{host}/api/tags", timeout=3).json()["models"]]
            have = mdl in names or f"{mdl}:latest" in names
            add("ok" if have else "fail", f"Ollama model {mdl}",
                "available" if have else f"not pulled: ollama pull {mdl}")
        except (requests.RequestException, KeyError, ValueError):
            add("fail", "Ollama", f"not reachable at {host}: run `ollama serve`")
    elif prov:
        env = {"claude": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY",
               "gemini": "GOOGLE_API_KEY"}[prov]
        mod = {"claude": "anthropic", "openai": "openai", "gemini": "google.genai"}[prov]
        add("ok" if os.environ.get(env) else "fail", f"{env}",
            "set" if os.environ.get(env) else "missing (add to .env)")
        has = importlib.util.find_spec(mod.split(".")[0]) is not None
        add("ok" if has else "fail", f"{mod} package",
            "installed" if has else f"pip install 'daily-brief[{prov}]'")

    if "linkedin" in srcs:
        c = cfg.get("sources.linkedin")
        add("ok" if c["accept_tos"] else "fail", "LinkedIn terms acknowledgement",
            "accepted" if c["accept_tos"] else "set sources.linkedin.accept_tos after reading "
            "docs/linkedin.md")
        add("ok" if shutil.which("uvx") else "fail", "uvx", "found" if shutil.which("uvx")
            else "install uv: https://docs.astral.sh/uv/")
        for mod in ("aiohttp", "patchright"):
            has = importlib.util.find_spec(mod) is not None
            add("ok" if has else "fail", mod, "installed" if has
                else "pip install 'daily-brief[linkedin]'")
        sd = Path(c["session_dir"]).expanduser()
        add("ok" if sd.exists() else "fail", "LinkedIn session",
            str(sd) if sd.exists() else "missing: run `daily-brief linkedin login`")
        add("ok" if c["mcp_version"] else "warn", "MCP server version pinned",
            c["mcp_version"] or "unpinned (sources.linkedin.mcp_version)")

    if online:
        for name, url in (("Hacker News API", "https://hn.algolia.com/api/v1/search?hitsPerPage=1"),
                          ("GitHub API", "https://api.github.com/rate_limit")):
            try:
                requests.get(url, timeout=5).raise_for_status()
                add("ok", name, "reachable")
            except requests.RequestException as e:
                add("fail", name, str(e))
    return out
