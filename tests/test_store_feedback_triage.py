import datetime as dt

import pytest

from daily_brief import feedback, triage
from daily_brief.processing.filter import PostFilter
from daily_brief.processing.scoring import Scorer
from daily_brief.store import AmbiguousRef
from tests.conftest import make_post


def test_seen_before_window(store):
    store.mark_seen(["a"], "2026-11-01")
    assert not store.seen_before("a", "2026-11-01", 7)  # same day = not "earlier"
    assert store.seen_before("a", "2026-11-03", 7)
    assert not store.seen_before("a", "2026-11-20", 7)  # outside window
    assert not store.seen_before("zzz", "2026-11-03", 7)


def test_resolve_prefix_and_ambiguity(store):
    store.record_items("2026-11-01", [{"id": "abc1234567", "title": "t"},
                                      {"id": "abc9999999", "title": "u"}])
    assert store.resolve("items", "abc1")["id"] == "abc1234567"
    with pytest.raises(AmbiguousRef):
        store.resolve("items", "abc")
    with pytest.raises(KeyError):
        store.resolve("items", "nope")


def _shown(cfg, store, **kw):
    p = make_post(**kw)
    PostFilter(cfg).apply([p])
    Scorer(cfg).annotate(p)
    store.record_items("2026-11-01", [p])
    return p


def test_feedback_loop_learns_and_reranks(cfg, store):
    liked = _shown(cfg, store, author="alice", url="https://good.io/1")
    out = feedback.vote(store, cfg, liked["id"][:6], +1)
    assert out["delta"] == 0.5
    w = store.weights()
    assert w["author:alice"] == 0.5 and w["domain:good.io"] == 0.5

    a = make_post(author="alice", url="https://good.io/2")
    b = make_post(author="bob", url="https://other.io/2")
    PostFilter(cfg).apply([a, b])
    ranked = Scorer(cfg, store.weights()).annotate_all([b, a])
    assert ranked[0]["author"] == "alice"


def test_feedback_down_and_weight_cap(cfg, store):
    p = _shown(cfg, store, author="spam", url="https://spam.io/1")
    for _ in range(20):
        feedback.vote(store, cfg, p["id"], -1)
    assert store.weights()["author:spam"] == -3.0  # clamped
    store.reset_weights()
    assert store.weights() == {}


def test_feedback_rejects_bad_direction(cfg, store):
    p = _shown(cfg, store)
    with pytest.raises(ValueError):
        feedback.vote(store, cfg, p["id"], 2)


def test_backlog_tags_and_staleness(cfg, store):
    old = make_post(origin="linkedin", _source="saved", _priority="high",
                    body="kubernetes saved thing worth reading later", url="https://s.io/1")
    PostFilter(cfg).apply([old])
    assert triage.sync_saved(store, cfg, [old], "2026-09-01") == 1
    assert triage.sync_saved(store, cfg, [old], "2026-11-01") == 0  # refresh, not new
    item = store.backlog()[0]
    assert "devops" in item["tags"] and item["first_seen"] == "2026-09-01"

    today = "2026-11-02"
    assert [i["id"] for i in triage.stale_items(store, cfg, today)] == [old["id"]]
    s = triage.summary(store, cfg, today)
    assert s["unread"] == 1 and s["stale"] == 1 and s["by_tag"]["devops"] == 1
    assert "Reading backlog" in triage.render_markdown(store, cfg, today)

    store.set_backlog(old["id"], status="read")
    assert triage.stale_items(store, cfg, today) == []
    assert triage.render_markdown(store, cfg, today) == ""


def test_fresh_items_not_stale(cfg, store):
    p = make_post(origin="linkedin", _source="saved", _priority="high", url="https://s.io/2")
    today = dt.date.today().isoformat()
    triage.sync_saved(store, cfg, [p], today)
    assert triage.stale_items(store, cfg, today) == []


def test_runs_ledger(store):
    store.record_run("2026-11-02", "claude:x", 1000, 200, 0.01, False, 5)
    store.record_run("2026-11-02", "claude:y", 1000, 200, None, True, 5)
    rows = store.runs()
    assert len(rows) == 2 and rows[0]["cost_usd"] is None and rows[1]["cost_usd"] == 0.01
