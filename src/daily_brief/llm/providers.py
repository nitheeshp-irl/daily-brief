"""LLM providers behind one tiny interface.

Spec strings (--model / llm.model):
  none | offline            no LLM; deterministic digest
  ollama[:<model>]          local
  claude | claude-sonnet | claude-opus | claude:<model>
  gpt4o | gpt4o-mini | openai:<model>
  gemini | gemini-pro | gemini:<model>
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

import requests

from ..config import Config
from ..util import estimate_tokens

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


@dataclass
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    estimated: bool = False


class Provider:
    name = ""

    def __init__(self, model: str, cfg: Config):
        self.model = model
        self.cfg = cfg

    def complete(self, system: str, prompt: str, max_tokens: int) -> Completion:
        raise NotImplementedError


def _retry(fn, attempts: int = 3):
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except LLMError:
            raise
        except Exception as e:  # SDK-specific transient errors vary; surface the last one
            last = e
            if i < attempts - 1:
                time.sleep(2 ** (i + 1))
    raise LLMError(f"request failed after {attempts} attempts: {last}")


class OllamaProvider(Provider):
    name = "ollama"

    def complete(self, system: str, prompt: str, max_tokens: int) -> Completion:
        host = (os.environ.get("OLLAMA_HOST") or self.cfg.get("llm.ollama_host")).rstrip("/")
        if not host.startswith("http"):
            host = "http://" + host
        try:
            tags = requests.get(f"{host}/api/tags", timeout=5)
            tags.raise_for_status()
            names = [m.get("name", "") for m in tags.json().get("models", [])]
        except requests.RequestException as e:
            raise LLMError(f"Ollama is not reachable at {host} ({e}). "
                           "Start it with `ollama serve`.") from e
        if self.model not in names and f"{self.model}:latest" not in names:
            raise LLMError(f"Ollama model '{self.model}' is not pulled. "
                           f"Run: ollama pull {self.model}")

        def call():
            r = requests.post(f"{host}/api/chat", timeout=float(self.cfg.get("llm.ollama_timeout")),
                              json={"model": self.model, "stream": False,
                                    "messages": [{"role": "system", "content": system},
                                                 {"role": "user", "content": prompt}],
                                    "options": {"num_ctx": int(self.cfg.get("llm.ollama_num_ctx")),
                                                "temperature": float(self.cfg.get("llm.temperature")),
                                                "num_predict": max_tokens}})
            r.raise_for_status()
            return r.json()

        data = _retry(call, attempts=2)
        text = data["message"]["content"]
        return Completion(text, data.get("prompt_eval_count") or estimate_tokens(system + prompt),
                          data.get("eval_count") or estimate_tokens(text),
                          estimated="prompt_eval_count" not in data)


class AnthropicProvider(Provider):
    name = "claude"

    def complete(self, system: str, prompt: str, max_tokens: int) -> Completion:
        try:
            import anthropic
        except ImportError as e:
            raise LLMError("anthropic not installed: pip install 'daily-brief[claude]'") from e
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise LLMError("ANTHROPIC_API_KEY is not set (put it in .env)")
        client = anthropic.Anthropic()
        msg = _retry(lambda: client.messages.create(
            model=self.model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": prompt}]))
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return Completion(text, msg.usage.input_tokens, msg.usage.output_tokens)


class OpenAIProvider(Provider):
    name = "openai"

    def complete(self, system: str, prompt: str, max_tokens: int) -> Completion:
        try:
            from openai import OpenAI
        except ImportError as e:
            raise LLMError("openai not installed: pip install 'daily-brief[openai]'") from e
        if not os.environ.get("OPENAI_API_KEY"):
            raise LLMError("OPENAI_API_KEY is not set (put it in .env)")
        client = OpenAI()
        resp = _retry(lambda: client.chat.completions.create(
            model=self.model, max_completion_tokens=max_tokens,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": prompt}]))
        u = resp.usage
        return Completion(resp.choices[0].message.content or "", u.prompt_tokens,
                          u.completion_tokens)


class GeminiProvider(Provider):
    name = "gemini"

    def complete(self, system: str, prompt: str, max_tokens: int) -> Completion:
        try:
            from google import genai
            from google.genai import types
        except ImportError as e:
            raise LLMError("google-genai not installed: pip install 'daily-brief[gemini]'") from e
        key = os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise LLMError("GOOGLE_API_KEY is not set (put it in .env)")
        client = genai.Client(api_key=key)
        resp = _retry(lambda: client.models.generate_content(
            model=self.model, contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system,
                                               max_output_tokens=max_tokens)))
        um = resp.usage_metadata
        text = resp.text or ""
        return Completion(text, getattr(um, "prompt_token_count", None) or estimate_tokens(prompt),
                          getattr(um, "candidates_token_count", None) or estimate_tokens(text))


_PROVIDERS: dict[str, type[Provider]] = {
    "ollama": OllamaProvider, "claude": AnthropicProvider,
    "openai": OpenAIProvider, "gemini": GeminiProvider,
}
_ALIASES = {"claude-sonnet": "claude", "claude-opus": "claude", "gpt4o": "openai",
            "gpt4o-mini": "openai", "gemini-pro": "gemini"}
OFFLINE = ("none", "offline")


def resolve(spec: str, cfg: Config) -> tuple[str, str]:
    """Spec -> (provider_name, model_name). Raises LLMError for unknown specs."""
    spec = (spec or "").strip()
    if spec in OFFLINE:
        return "offline", "extractive"
    models = cfg.get("llm.models")
    if spec in models:  # shorthand: "claude", "gpt4o-mini", "ollama", ...
        return _ALIASES.get(spec, spec), models[spec]
    if ":" in spec:
        prov, model = spec.split(":", 1)
        if prov in _PROVIDERS and model:
            return prov, model
    raise LLMError(
        f"Unknown model '{spec}'. Use: none | ollama[:model] | claude[-sonnet|-opus] | "
        "claude:<model> | gpt4o | gpt4o-mini | openai:<model> | gemini[-pro] | gemini:<model>")


def get_provider(spec: str, cfg: Config) -> Provider | None:
    """None means offline (no LLM)."""
    prov, model = resolve(spec, cfg)
    return None if prov == "offline" else _PROVIDERS[prov](model, cfg)
