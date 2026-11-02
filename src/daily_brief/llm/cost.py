"""Token + cost reporting.

Prices are USD per 1M tokens (input, output). The built-in table only lists models whose
public price we're confident about and WILL go stale — verify with your provider and override
via `llm.pricing` in config.yaml. Unknown models report tokens but no dollar figure.
Ollama (local) is free.
"""

from __future__ import annotations

from dataclasses import dataclass

BUILTIN_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-opus-4-5": (5.00, 25.00),
    "gemini-2.0-flash": (0.10, 0.40),
}


@dataclass
class Usage:
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    estimated: bool  # True when counts are our ~4 chars/token estimate, not the provider's
    cost_usd: float | None  # None = unknown price


def price_for(model: str, overrides: dict | None = None) -> tuple[float, float] | None:
    table = {**BUILTIN_PRICES, **{k: tuple(v) for k, v in (overrides or {}).items()}}
    if model in table:
        return table[model]
    matches = [k for k in table if model.startswith(k)]  # dated snapshots: "gpt-4o-2024-08-06"
    return table[max(matches, key=len)] if matches else None


def compute_cost(provider: str, model: str, input_tokens: int, output_tokens: int,
                 overrides: dict | None = None) -> float | None:
    if provider in ("ollama", "offline"):
        return 0.0
    p = price_for(model, overrides)
    if p is None:
        return None
    return round(input_tokens / 1e6 * p[0] + output_tokens / 1e6 * p[1], 6)


def format_usage(u: Usage) -> str:
    tilde = "~" if u.estimated else ""
    if u.cost_usd is None:
        cost = "cost unknown (no price for this model; set llm.pricing in config.yaml)"
    elif u.cost_usd == 0:
        cost = "free (local)"
    else:
        cost = f"${u.cost_usd:.4f}"
    return (f"{u.provider}:{u.model} · {tilde}{u.input_tokens:,} in / "
            f"{tilde}{u.output_tokens:,} out tokens · {cost}")


def preflight(model_label: str, provider: str, model: str, input_tokens: int,
              max_output: int, overrides: dict | None = None) -> str:
    """Upper-bound estimate shown before the call (output assumed = max_output)."""
    worst = compute_cost(provider, model, input_tokens, max_output, overrides)
    tail = ("free (local)" if worst == 0 else
            "cost unknown" if worst is None else f"at most ~${worst:.4f}")
    return f"{model_label}: ~{input_tokens:,} input tokens → {tail}"
