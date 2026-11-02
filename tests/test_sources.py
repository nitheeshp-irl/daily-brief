import datetime as dt

import pytest

from daily_brief import http
from daily_brief.config import Config
from daily_brief.sources import collect_all, enabled_sources, github, hackernews, reddit, rss
from daily_brief.sources.base import SourceError

NOW = dt.datetime(2026, 11, 2, 12, tzinfo=dt.timezone.utc)


def test_hn_parse_filters_by_points():
    data = {"hits": [
        {"objectID": "1", "title": "Big", "url": "https://a.io", "points": 300, "author": "x",
         "num_comments": 5, "created_at": "2026-11-02T01:00:00Z"},
        {"objectID": "2", "title": "Small", "points": 3},
        {"objectID": "3", "title": "Ask HN: something", "points": 100, "story_text": "<p>Hi &amp; bye</p>"},
    ]}
    posts = hackernews.parse_hits(data, 50)
    assert [p["title"] for p in posts] == ["Big", "Ask HN: something"]
    assert posts[1]["url"].startswith("https://news.ycombinator.com/item?id=3")
    assert posts[1]["body"] == "Hi & bye"
    assert posts[0]["links"] == ["https://a.io", "https://news.ycombinator.com/item?id=1"]


REDDIT = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><author><name>/u/alice</name></author><title>Terraform drift tool</title>
<link href="https://www.reddit.com/r/devops/comments/1/x/"/><updated>2026-11-02T01:00:00+00:00</updated>
<content type="html">&lt;p&gt;We built a thing&lt;/p&gt; submitted by /u/alice &lt;a href="https://ext.io/tool"&gt;[link]&lt;/a&gt; &lt;a href="https://www.reddit.com/r/devops/comments/1/x/"&gt;[comments]&lt;/a&gt;</content></entry>
<entry><author><name>/u/bob</name></author><title>Self post</title>
<link href="https://www.reddit.com/r/devops/comments/2/y/"/><updated>2026-11-02T02:00:00+00:00</updated>
<content type="html">&lt;p&gt;Just text&lt;/p&gt; &lt;a href="https://www.reddit.com/r/devops/comments/2/y/"&gt;[comments]&lt;/a&gt;</content></entry>
</feed>"""


def test_reddit_atom_prefers_external_link():
    posts = reddit.parse_atom(REDDIT, "devops", 10)
    assert posts[0]["url"] == "https://ext.io/tool" and len(posts[0]["links"]) == 2
    assert posts[0]["author"] == "alice" and posts[0]["body"] == "We built a thing"
    assert posts[1]["url"].startswith("https://www.reddit.com/r/devops/comments/2")
    assert posts[1]["_source"] == "reddit:r/devops" and len(posts[1]["links"]) == 1


def test_reddit_limit():
    assert len(reddit.parse_atom(REDDIT, "devops", 1)) == 1


RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>New thing</title><link>https://b.io/1</link><description>&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt;&lt;/p&gt;</description>
<pubDate>Mon, 02 Nov 2026 08:00:00 +0000</pubDate></item>
<item><title>Old thing</title><link>https://b.io/2</link><pubDate>Mon, 01 Jan 2024 08:00:00 +0000</pubDate></item>
</channel></rss>"""
ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Atom post</title><link rel="alternate" href="https://c.io/1"/>
<updated>2026-11-02T09:00:00Z</updated><summary>Sum</summary><author><name>Sam</name></author></entry></feed>"""


def test_rss_and_age_filter():
    posts = rss.parse_feed(RSS, "Blog", 10, 48, now=NOW)
    assert [p["title"] for p in posts] == ["New thing"]
    assert posts[0]["body"] == "Hello world"


def test_atom():
    posts = rss.parse_feed(ATOM, "Blog", 10, 48, now=NOW)
    assert posts[0]["url"] == "https://c.io/1" and posts[0]["author"] == "Sam"


def test_rss_rejects_xml_bomb():
    bomb = ('<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
            "<rss><channel><item><title>&b;</title><link>https://x.io</link></item></channel></rss>")
    from defusedxml import DefusedXmlException

    with pytest.raises(DefusedXmlException):
        rss.parse_feed(bomb, "x", 5, 0, now=NOW)


def test_github_parse():
    data = {"items": [{"full_name": "o/r", "owner": {"login": "o"}, "description": "A tool",
                       "topics": ["llm"], "html_url": "https://github.com/o/r",
                       "stargazers_count": 500, "language": "Go", "created_at": "2026-10-30T00:00:00Z"}]}
    p = github.parse_repos(data)[0]
    assert p["title"] == "o/r" and p["points"] == 500 and "Topics: llm" in p["body"]


def test_collect_uses_http(monkeypatch):
    monkeypatch.setattr(http, "get_json", lambda *a, **k: {"hits": [
        {"objectID": "1", "title": "Kubernetes thing", "points": 99, "url": "https://a.io"}]})
    cfg = Config()
    assert hackernews.collect(cfg)[0]["title"] == "Kubernetes thing"


def test_reddit_all_failed_raises(monkeypatch):
    def boom(*a, **k):
        raise http.HTTPError("429")
    monkeypatch.setattr(http, "get", boom)
    with pytest.raises(SourceError, match="rate-limit"):
        cfg = Config()
        cfg.data["sources"]["reddit"]["delay_seconds"] = 0
        reddit.collect(cfg)


def test_one_failing_source_does_not_stop_others(monkeypatch):
    cfg = Config()
    for n in ("reddit", "rss", "github_trending", "linkedin"):
        cfg.data["sources"][n]["enabled"] = False
    cfg.data["sources"]["rss"]["enabled"] = True
    monkeypatch.setattr(http, "get_json", lambda *a, **k: {"hits": [
        {"objectID": "1", "title": "ok", "points": 99, "url": "https://a.io"}]})

    def broken(*a, **k):
        raise http.HTTPError("down")
    monkeypatch.setattr(http, "get", broken)
    out = collect_all(cfg)
    assert out["hackernews"].ok and len(out["hackernews"].posts) == 1
    assert not out["rss"].ok and "RSS" in out["rss"].error


def test_enabled_sources_default_excludes_linkedin():
    assert "linkedin" not in enabled_sources(Config())
