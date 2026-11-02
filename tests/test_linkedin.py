import json

import pytest

from daily_brief.config import Config
from daily_brief.sources.base import SourceError
from daily_brief.sources.linkedin import collect, looks_like_login_wall
from daily_brief.sources.linkedin.mcp_client import free_port, package_spec, parse_response
from daily_brief.sources.linkedin.parser import parse_posts
from daily_brief.sources.linkedin.saved import parse_saved_text

FEED = {"sections": {"feed": """Feed post
Jane Doe
Principal Engineer at Acme | Kubernetes
3h •
We cut deploy times by 60% using ArgoCD and progressive delivery. Details: https://acme.dev/post
Like
Feed post
Profile viewers
Start a post
nothing useful in this chunk but long enough to pass the length bar
"""}}

SAVED = """Skip to main content
Saved posts
John Smith
View John Smith's profile
• 2nd
Staff Engineer at Foo
2w • Visible to everyone
Great notes on Terraform state locking and remote backends with real examples https://foo.dev/tf
Like
Comment
"""


def test_parse_feed_extracts_fields_and_drops_chrome():
    posts = parse_posts(FEED, "feed")
    assert len(posts) == 1
    p = posts[0]
    assert p["author"] == "Jane Doe" and p["timestamp"] == "3h"
    assert p["links"] == ["https://acme.dev/post"] and p["origin"] == "linkedin"


def test_parse_saved_text():
    items = parse_saved_text(SAVED, "saved")
    assert len(items) == 1
    assert items[0]["author"] == "John Smith" and items[0]["_priority"] == "high"
    assert items[0]["links"] == ["https://foo.dev/tf"]


def test_parse_saved_text_garbage_returns_empty():
    assert parse_saved_text("totally different markup\n" * 50, "saved") == []


def test_login_wall_detection():
    assert looks_like_login_wall("Please sign in to continue")
    assert not looks_like_login_wall("x" * 5000 + " sign in")


def test_parse_response_sse_and_json():
    sse = 'event: message\ndata: {"jsonrpc":"2.0","id":7,"result":{"ok":1}}\n\n'
    assert parse_response(sse, 7)["result"] == {"ok": 1}
    assert parse_response(json.dumps({"id": 3}), 3) == {"id": 3}
    assert parse_response(sse, 8) is None


def test_package_spec_pinning():
    assert package_spec("p", "1.2.3") == "p==1.2.3"
    assert package_spec("p", "") == "p@latest"


def test_free_port_is_usable():
    assert 1024 < free_port() < 65536


def test_collect_requires_explicit_tos_acceptance():
    cfg = Config()
    cfg.data["sources"]["linkedin"]["enabled"] = True
    with pytest.raises(SourceError, match="accept_tos"):
        collect(cfg)


def test_feed_permalinks_attached_from_references():
    raw = {"sections": FEED["sections"], "references": {"feed": [
        {"kind": "feed_post", "url": "/feed/update/urn:li:activity:1/",
         "text": "We cut deploy times by 60% using ArgoCD"}]}}
    posts = parse_posts(raw, "feed")
    assert posts[0]["url"] == "https://www.linkedin.com/feed/update/urn:li:activity:1/"


def test_saved_permalinks_use_smallest_unambiguous_ancestor():
    from daily_brief.sources.linkedin.parser import _norm
    from daily_brief.sources.linkedin.saved import attach_saved_permalinks
    items = parse_saved_text(SAVED, "saved") + [{"body": "A completely different second saved post about cats"}]
    first, second = _norm(items[0]["body"]), _norm(items[1]["body"])
    anchors = [
        # inner level: header only; next level: just post 1; outer: both (never reached)
        {"url": "https://www.linkedin.com/posts/one", "levels": ["johnsmith2w", first, first + second]},
        # only ever sees both posts -> ambiguous -> skipped
        {"url": "https://www.linkedin.com/posts/blob", "levels": [first + second]},
        {"url": "https://www.linkedin.com/posts/two", "levels": [second]},
    ]
    assert attach_saved_permalinks(items, anchors) == 2
    assert items[0]["url"].endswith("/one") and items[1]["url"].endswith("/two")


def test_prompt_links_lead_with_permalink(cfg):
    from daily_brief.llm import prompt as P
    from tests.conftest import make_post
    p = make_post(url="https://www.linkedin.com/posts/x", links=["https://ext.io/a"])
    assert P._slim(p, 500, 10)["links"] == ["https://www.linkedin.com/posts/x", "https://ext.io/a"]


def test_feed_permalink_matched_from_slug_and_never_guessed():
    from daily_brief.sources.linkedin.parser import attach_permalinks, slug_words
    assert slug_words("/posts/jane-doe_only-include-resource-detectors-you-need-share-752-T0gM") == \
        "onlyincluderesourcedetectorsyouneed"
    assert slug_words("/feed/update/urn:li:share:1/") == ""
    posts = [{"body": "Only include resource detectors you need. Here is why they matter a lot."},
             {"body": "Something else entirely, with no matching reference at all in the list."}]
    refs = [{"url": "/feed/update/urn:li:share:1/"},
            {"url": "/posts/jane-doe_only-include-resource-detectors-you-need-share-752-T0gM"}]
    assert attach_permalinks(posts, refs) == 1
    assert posts[0]["url"].endswith("T0gM") and "url" not in posts[1]
