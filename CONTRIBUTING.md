# Contributing

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check . && pytest
```

- Tests must not use the network or any account. Use fixtures / `monkeypatch` (see `tests/test_sources.py`).
- New source? Add `src/daily_brief/sources/<name>.py` with `collect(cfg) -> list[dict]` (raise `SourceError`
  for failures the user should see), register it in `sources/__init__.py`, add config defaults in `config.py`
  and `config.example.yaml`, and a parser test with a canned payload.
- New LLM provider? Subclass `Provider` in `llm/providers.py` and register it. Report real token usage.
- Never log post content, and never commit anything from `data/`, `logs/`, `.env`, or `config.yaml`.
- Scraping-related changes (LinkedIn): keep the opt-in gate, don't add detection-evasion features.
