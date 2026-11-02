from daily_brief.processing.filter import PostFilter
from daily_brief.processing.scoring import Scorer, features_for
from tests.conftest import make_post


def _ready(cfg, **kw):
    p = make_post(**kw)
    PostFilter(cfg).apply([p])
    return p


def test_saved_outranks_everything(cfg):
    saved = _ready(cfg, _source="saved", origin="linkedin", _priority="high", url="https://x.io/s")
    hot = _ready(cfg, points=900, comments=10, url="https://x.io/h")
    ranked = Scorer(cfg).annotate_all([hot, saved])
    assert ranked[0]["id"] == saved["id"]
    assert any(s["type"] == "explicit_save" for s in ranked[0]["signals"])


def test_popularity_signal_reason(cfg):
    p = Scorer(cfg).annotate(_ready(cfg, points=412, comments=120))
    reasons = [s["reason"] for s in p["signals"]]
    assert any("412 points on Hacker News, 120 comments" in r for r in reasons)


def test_quality_flags_lower_score(cfg):
    clean = Scorer(cfg).annotate(_ready(cfg, url="https://x.io/c"))
    bait = _ready(cfg, url="https://x.io/b", body="kubernetes terraform follow for more!")
    bait = Scorer(cfg).annotate(bait)
    assert bait["score"] < clean["score"]


def test_feedback_weights_change_ranking_and_explain(cfg):
    a = _ready(cfg, url="https://x.io/a", author="alice")
    b = _ready(cfg, url="https://x.io/b", author="bob")
    ranked = Scorer(cfg, {"author:bob": 2.0}).annotate_all([a, b])
    assert ranked[0]["author"] == "bob"
    assert any(s["type"] == "your_feedback" for s in ranked[0]["signals"])


def test_feedback_bonus_is_clamped(cfg):
    s = Scorer(cfg, {"origin:hackernews": 3, "topic:devops": 3, "author:a": 3})
    assert s.feedback_bonus(["origin:hackernews", "topic:devops", "author:a"]) == 6.0


def test_features_include_origin_author_domain_topic(cfg):
    p = _ready(cfg)
    feats = features_for(p)
    assert "origin:hackernews" in feats and "author:a" in feats
    assert "domain:x.io" in feats and "topic:devops" in feats


def test_linkedin_network_signal(cfg):
    p = _ready(cfg, origin="linkedin", _source="feed", relevance_signal="Jo likes this",
               author="z", url="")
    sigs = Scorer(cfg).annotate(p)["signals"]
    assert any(s["type"] == "network_2nd_degree" for s in sigs)
