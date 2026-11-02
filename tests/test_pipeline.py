import json

import pytest

from daily_brief import pipeline
from daily_brief.llm import providers
from daily_brief.llm.providers import Completion, LLMError, Provider
from daily_brief.pipeline import RunError, RunOptions, run
from tests.conftest import TODAY


class FakeProvider(Provider):
    name = "claude"

    def __init__(self, text):
        self.model = "claude-test"
        self._text = text

    def complete(self, system, prompt, max_tokens):
        self.seen_prompt = prompt
        return Completion(self._text, 2000, 400)


def opts(**kw):
    return RunOptions(model="none", today=TODAY, **kw)


def test_sample_run_writes_nothing(cfg, store):
    res = run(cfg, opts(sample=True), store)
    assert "Daily Brief" in res.brief and res.brief_path is None
    assert not cfg.briefs_dir.exists() and store.latest_items() == []


def test_full_run_saves_brief_items_and_run_ledger(seeded_cache, store):
    cfg = seeded_cache
    res = run(cfg, opts(), store)
    assert (cfg.briefs_dir / f"{TODAY}.md").read_text() == res.brief
    assert "Sources index" in res.brief and res.counts["sent_to_llm"] == 8
    assert "(ref: " in res.brief and "(ref: " + res.brief.split("(ref: ")[1].split(")")[0] + ") (ref:" not in res.brief
    assert len(store.latest_items()) == 8
    assert store.runs()[0]["model"] == "offline:extractive"
    assert store.backlog()[0]["origin"] == "linkedin"  # saved item synced to the reading list


def test_second_run_is_idempotent_unless_forced(seeded_cache, store):
    run(seeded_cache, opts(), store)
    assert "already exists" in run(seeded_cache, opts(), store).skipped
    assert run(seeded_cache, opts(force=True, use_history=False), store).brief


def test_history_hides_items_seen_on_earlier_days(seeded_cache, store):
    cfg = seeded_cache
    run(cfg, opts(), store)
    store.db.execute("UPDATE seen SET first_seen='2026-11-01', last_seen='2026-11-01'")
    store.db.commit()
    res = run(cfg, opts(force=True), store)
    assert res.counts["already_seen"] == 7  # everything except the saved item
    assert res.counts["signal"] == 1


def test_dry_run_and_estimate_stop_before_llm(seeded_cache, store, monkeypatch):
    monkeypatch.setattr(pipeline, "get_provider", lambda *a: pytest.fail("LLM must not run"))
    r = run(seeded_cache, RunOptions(model="claude", today=TODAY, dry_run=True), store)
    assert r.usage.input_tokens > 0 and r.brief == "" and not seeded_cache.briefs_dir.exists()


def test_llm_output_gets_link_verification_and_cost(seeded_cache, store, monkeypatch):
    fake = FakeProvider("# Brief\n[bad](https://evil.io/x) and [good](https://example.com/snapstart)")
    monkeypatch.setattr(pipeline, "get_provider", lambda *a: fake)
    r = run(seeded_cache, RunOptions(model="claude", today=TODAY), store)
    assert "evil.io" not in r.brief and "https://example.com/snapstart" in r.brief
    assert "1 unverified link(s) removed" in r.brief
    assert r.usage.input_tokens == 2000 and r.usage.cost_usd is None  # unknown price, not invented
    assert "cost unknown" in r.brief
    assert "<untrusted_data" in fake.seen_prompt
    assert "[good](https://example.com/snapstart) (ref: " in r.brief  # ref appended to bare link


def test_llm_failure_falls_back_to_offline(seeded_cache, store, monkeypatch):
    class Boom(FakeProvider):
        def complete(self, *a):
            raise LLMError("Ollama is not reachable")
    monkeypatch.setattr(pipeline, "get_provider", lambda *a: Boom(""))
    r = run(seeded_cache, RunOptions(model="ollama", today=TODAY), store)
    assert "offline digest" in r.brief and "Ollama is not reachable" in r.brief


def test_llm_failure_without_fallback_raises(seeded_cache, store, monkeypatch):
    seeded_cache.data["llm"]["fallback_offline"] = False

    class Boom(FakeProvider):
        def complete(self, *a):
            raise LLMError("nope")
    monkeypatch.setattr(pipeline, "get_provider", lambda *a: Boom(""))
    with pytest.raises(RunError):
        run(seeded_cache, RunOptions(model="ollama", today=TODAY), store)


def test_redact_restores_names_in_output_but_not_in_prompt(seeded_cache, store, monkeypatch):
    fake = FakeProvider("Person A wrote something")
    monkeypatch.setattr(pipeline, "get_provider", lambda *a: fake)
    r = run(seeded_cache, RunOptions(model="claude", today=TODAY, redact=True), store)
    assert "example_user" not in fake.seen_prompt and "Example Author" not in fake.seen_prompt
    assert "Person A" not in r.brief.split("Sources index")[0]  # restored to a real name


def test_bad_model_fails_fast(seeded_cache, store):
    with pytest.raises(LLMError):
        run(seeded_cache, RunOptions(model="nonsense", today=TODAY), store)


def test_all_sources_failing_raises(cfg, store, monkeypatch):
    from daily_brief.sources import SourceResult
    monkeypatch.setattr(pipeline, "collect_all",
                        lambda *a: {"hackernews": SourceResult("hackernews", error="down")})
    with pytest.raises(RunError, match="Every source failed"):
        run(cfg, opts(), store)


def test_partial_failure_is_reported_in_brief(cfg, store, monkeypatch):
    from daily_brief.sources import SourceResult
    posts = json.loads(json.dumps(pipeline.load_sample()["hackernews"].posts))
    monkeypatch.setattr(pipeline, "collect_all", lambda *a: {
        "hackernews": SourceResult("hackernews", posts=posts),
        "reddit": SourceResult("reddit", error="429 rate limited")})
    r = run(cfg, opts(), store)
    assert "Source **reddit** unavailable: 429 rate limited" in r.brief
    assert r.source_errors == {"reddit": "429 rate limited"}


def test_old_cache_format_is_ignored(cfg, store, monkeypatch):
    cfg.raw_dir.mkdir(parents=True)
    (cfg.raw_dir / f"{TODAY}.json").write_text(json.dumps({"date": TODAY, "feed_posts": []}))
    assert pipeline.load_raw(cfg.raw_dir / f"{TODAY}.json") is None


def test_providers_module_exports():
    assert providers.OFFLINE == ("none", "offline")


def test_only_refetch_merges_into_existing_cache(seeded_cache, store, monkeypatch):
    from daily_brief.sources import SourceResult
    cfg = seeded_cache
    fresh_post = {"origin": "hackernews", "_source": "hn", "title": "Fresh Kubernetes item here",
                  "author": "z", "body": "", "url": "https://fresh.io/1", "links": ["https://fresh.io/1"]}
    monkeypatch.setattr(pipeline, "collect_all",
                        lambda *a: {"hackernews": SourceResult("hackernews", posts=[fresh_post])})
    run(cfg, RunOptions(model="none", today=TODAY, only=["hackernews"], refetch=True, dry_run=True), store)
    saved = json.loads((cfg.raw_dir / f"{TODAY}.json").read_text())["sources"]
    assert set(saved) == {"hackernews", "reddit", "rss", "github_trending", "linkedin"}  # others kept
    assert [p["url"] for p in saved["hackernews"]["posts"]] == ["https://fresh.io/1"]  # one replaced
