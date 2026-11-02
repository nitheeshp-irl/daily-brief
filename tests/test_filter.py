import pytest

from daily_brief.processing.filter import PostFilter, compile_terms
from tests.conftest import make_post


def test_terms_are_whole_word():
    rx = compile_terms(["s3", "rag"])
    assert rx.search("we use S3 daily")
    assert not rx.search("this is a fragile storage")
    assert not rx.search("ass3t")


def test_prefix_wildcard():
    rx = compile_terms(["observabilit*", "vibe cod*"])
    assert rx.search("Observability matters") and rx.search("vibe coding is fun")
    assert not rx.search("the unobservability")


def test_empty_terms():
    assert compile_terms([]) is None


def test_off_topic_dropped_and_on_topic_kept(cfg):
    f = PostFilter(cfg)
    keep = make_post()
    drop = make_post(title="Gardening", body="roses and tulips are lovely this spring", url="https://x.io/g")
    signal, noise = f.apply([keep, drop])
    assert [p["id"] for p in signal] == [keep["id"]]
    assert noise[0]["_filtered_reason"] == "off_topic"
    assert "devops" in signal[0]["matched_topics"]


def test_rss_and_saved_skip_topic_filter(cfg):
    f = PostFilter(cfg)
    rss = make_post(origin="rss", _source="rss:Blog", title="Quarterly roadmap update for everyone",
                    body="", url="https://x.io/r")
    saved = make_post(origin="linkedin", _source="saved", _priority="high", title="",
                      body="Totally unrelated gardening thoughts " * 3, url="https://x.io/s")
    signal, _ = f.apply([rss, saved])
    assert len(signal) == 2


def test_presets_and_saved_job_ad_exemption(cfg):
    cfg.data["filters"]["block_presets"] = ["promoted", "job_ads"]
    f = PostFilter(cfg)
    job = make_post(body="We're hiring a kubernetes engineer, apply now", url="https://x.io/j")
    promoted = make_post(body="Promoted: kubernetes platform trial offer", url="https://x.io/p")
    saved_job = make_post(_source="saved", origin="linkedin", _priority="high",
                          body="We're hiring a kubernetes engineer, apply now", url="https://x.io/sj")
    signal, noise = f.apply([job, promoted, saved_job])
    assert {n["_filtered_reason"] for n in noise} == {"job_ads", "promoted"}
    assert [p["id"] for p in signal] == [saved_job["id"]]


def test_custom_block_terms_and_regex(cfg):
    cfg.data["filters"]["block"] = ["crypto"]
    cfg.data["filters"]["block_regex"] = [r"webinar \d+"]
    f = PostFilter(cfg)
    a = make_post(body="kubernetes crypto mining", url="https://x.io/1")
    b = make_post(body="kubernetes webinar 42 register", url="https://x.io/2")
    _, noise = f.apply([a, b])
    assert {n["_filtered_reason"] for n in noise} == {"blocked_term", "blocked_regex"}


def test_flags_annotate_but_do_not_drop_by_default(cfg):
    f = PostFilter(cfg)
    p = make_post(body="kubernetes tips. Follow for more! Comment below", url="https://x.io/b")
    signal, _ = f.apply([p])
    assert signal and "engagement_bait" in signal[0]["quality_flags"]
    cfg.data["filters"]["drop_flagged"] = True
    signal, noise = PostFilter(cfg).apply([make_post(
        body="kubernetes tips. Follow for more! Comment below", url="https://x.io/b")])
    assert not signal and noise[0]["_filtered_reason"] == "flagged"


def test_unknown_preset_raises(cfg):
    cfg.data["filters"]["block_presets"] = ["nope"]
    with pytest.raises(ValueError):
        PostFilter(cfg)


def test_empty_topics_disables_topic_filter(cfg):
    cfg.data["topics"] = {}
    signal, _ = PostFilter(cfg).apply([make_post(
        title="Gardening", body="roses and tulips this spring season", url="https://x.io/g")])
    assert len(signal) == 1
