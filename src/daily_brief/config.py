"""Configuration: built-in defaults deep-merged with the user's config.yaml."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "profile": {
        "name": "",
        "role": "",
        "location": "",
        "interests": [],  # free-text interests, added to topic keywords for scoring
    },
    "paths": {"data_dir": "data", "logs_dir": "logs"},
    # Topic groups. The union of all keywords is the allow-list; each group name is
    # also used as a tag for posts, a brief section, and a scoring feature.
    # A trailing * is a prefix wildcard ("observabilit*").
    "topics": {
        "aws": [
            "aws", "amazon web services", "s3", "lambda", "ec2", "eks", "fargate", "cdk",
            "cloudformation", "bedrock", "step functions", "dynamodb", "sqs", "sns",
        ],
        "devops": [
            "devops", "kubernetes", "k8s", "terraform", "gitops", "helm", "argo*", "ci/cd",
            "cicd", "platform engineer*", "sre", "infrastructure as code", "iac", "docker",
            "container*", "observabilit*", "monitoring", "opentofu", "pulumi",
        ],
        "ai": [
            "llm", "gpt", "claude", "gemini", "ai agent*", "rag", "prompt engineer*",
            "llmops", "mlops", "vibe cod*", "cursor", "copilot", "ollama", "langchain",
            "langgraph", "mcp", "model context protocol", "strands", "agentic",
        ],
        "opensource": ["open source", "open-source", "release v*"],
        "architecture": [
            "cloud architect*", "serverless", "microservice*", "event-driven",
            "distributed system*", "api gateway",
        ],
    },
    "filters": {
        "block": [],  # extra terms that always drop a post
        "block_regex": [],  # extra raw regexes that always drop a post
        "block_presets": ["promoted", "job_ads"],  # promoted, job_ads, engagement_bait, ai_hype
        "drop_flagged": False,  # also drop posts flagged engagement_bait / generic_ai_hype
        "min_chars": 25,
        # sources that skip the topic allow-list (curated by you)
        "skip_topic_for": ["saved", "saved_article", "rss"],
    },
    "sources": {
        "hackernews": {"enabled": True, "max_items": 30, "min_points": 50},
        "reddit": {
            "enabled": True,
            "subreddits": ["devops", "aws", "kubernetes", "LocalLLaMA"],
            "max_per_sub": 10,
            "delay_seconds": 2,
        },
        "rss": {
            "enabled": True,
            "max_per_feed": 10,
            "max_age_hours": 72,
            "feeds": [
                {"name": "AWS News Blog", "url": "https://aws.amazon.com/blogs/aws/feed/"},
                {"name": "AWS What's New",
                 "url": "https://aws.amazon.com/about-aws/whats-new/recent/feed/"},
                {"name": "Kubernetes Blog", "url": "https://kubernetes.io/feed.xml"},
                {"name": "GitHub Blog", "url": "https://github.blog/feed/"},
                {"name": "Simon Willison", "url": "https://simonwillison.net/atom/everything/"},
            ],
        },
        "github_trending": {
            "enabled": True,
            "since_days": 7,
            "min_stars": 100,
            "max_items": 15,
            "topics": [],  # e.g. ["llm", "devops"]; empty = any
            "languages": [],
        },
        "linkedin": {
            "enabled": False,  # opt-in. Read docs/linkedin.md first.
            "accept_tos": False,  # must be true: you accept the risk (see docs/linkedin.md)
            "mcp_package": "mcp-server-linkedin",
            "mcp_version": "4.29.0",  # pinned on purpose; "" = latest (not recommended)
            "session_dir": "~/.linkedin-mcp/profile",
            "use_feed": True,
            "use_search": True,
            "use_saved": True,
            "max_feed_posts": 40,
            "max_search_posts_per_query": 8,
            "saved_posts_max_scrolls": 10,
            "search_queries": [],
            "delay_seconds": [2.0, 5.0],  # random pause between LinkedIn requests
            "retries": 2,
        },
    },
    "history": {"dedup_days": 7},  # don't re-show non-saved items seen in the last N days
    "triage": {"stale_days": 30, "show_in_brief": 5},
    "feedback": {"learning_rate": 0.5, "max_weight": 3.0, "max_bonus": 6.0},
    "llm": {
        "model": "ollama",
        "max_output_tokens": 6000,
        "temperature": 0.2,  # low = follows the output template more reliably
        "max_input_tokens": 12000,
        "max_items": 60,
        "max_body_chars": 1000,
        "min_chars_for_detail": 300,  # thinner items get a one-liner, never an invented card
        "redact": False,
        "fallback_offline": True,  # if the LLM fails, still write a no-LLM brief
        "ollama_host": "http://localhost:11434",
        "ollama_num_ctx": 16384,
        "ollama_timeout": 600,
        # Defaults for provider shorthands. Model names change often: override freely.
        "models": {
            "ollama": "gemma4:e2b",
            "claude": "claude-haiku-5-5",
            "claude-sonnet": "claude-sonnet-5-5",
            "claude-opus": "claude-opus-5-5",
            "gpt4o": "gpt-4o",
            "gpt4o-mini": "gpt-4o-mini",
            "gemini": "gemini-2.0-flash",
            "gemini-pro": "gemini-2.5-pro",
        },
        # USD per 1M tokens: {"model-name": [input, output]}. Merged over the built-ins.
        "pricing": {},
        "prompt_file": "",  # optional path to your own prompt template
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Config:
    data: dict[str, Any] = field(default_factory=lambda: copy.deepcopy(DEFAULTS))
    path: Path | None = None
    base_dir: Path = field(default_factory=Path.cwd)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def _path(self, value: str) -> Path:
        p = Path(value).expanduser()
        return p if p.is_absolute() else self.base_dir / p

    @property
    def data_dir(self) -> Path:
        return self._path(self.get("paths.data_dir"))

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def briefs_dir(self) -> Path:
        return self.data_dir / "briefs"

    @property
    def logs_dir(self) -> Path:
        return self._path(self.get("paths.logs_dir"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "daily-brief.db"

    @property
    def topics(self) -> dict[str, list[str]]:
        return {k: list(v) for k, v in self.get("topics", {}).items()}


def find_config(explicit: str | None = None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    elif os.environ.get("DAILY_BRIEF_CONFIG"):
        candidates.append(Path(os.environ["DAILY_BRIEF_CONFIG"]).expanduser())
    else:
        candidates += [
            Path.cwd() / "config.yaml",
            Path.home() / ".config" / "daily-brief" / "config.yaml",
        ]
    for c in candidates:
        if c.is_file():
            return c
    if explicit:
        raise FileNotFoundError(f"Config file not found: {explicit}")
    return None


def load_config(explicit: str | None = None) -> Config:
    """Load .env (if present next to the config / cwd) and the YAML config."""
    from dotenv import load_dotenv

    path = find_config(explicit)
    base_dir = path.parent if path else Path.cwd()
    load_dotenv(base_dir / ".env", override=False)
    load_dotenv(Path.cwd() / ".env", override=False)

    user: dict = {}
    if path:
        with open(path) as f:
            user = yaml.safe_load(f) or {}
        if not isinstance(user, dict):
            raise ValueError(f"{path}: top level must be a mapping")
    return Config(data=deep_merge(DEFAULTS, user), path=path, base_dir=base_dir)
