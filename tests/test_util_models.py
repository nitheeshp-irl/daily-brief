from daily_brief.models import normalize_post, origin_of
from daily_brief.util import clip, domain_of, estimate_tokens, extract_links, normalize_url, post_id


def test_normalize_url_strips_noise():
    a = normalize_url("https://www.Example.com/path/?utm_source=x&id=3#frag")
    b = normalize_url("http://example.com/path?id=3")
    assert a == b == "example.com/path?id=3"


def test_extract_links_cleans_and_dedupes():
    t = "see https://a.io/x. and (https://a.io/x) plus lnkd.in/abc, github.com/o/r!"
    assert extract_links(t) == ["https://a.io/x", "lnkd.in/abc", "github.com/o/r"]


def test_post_id_stable_and_origin_scoped():
    a = {"origin": "hackernews", "url": "https://x.io/a?utm_medium=z"}
    b = {"origin": "hackernews", "url": "https://www.x.io/a"}
    c = {"origin": "reddit", "url": "https://x.io/a"}
    assert post_id(a) == post_id(b) != post_id(c)
    assert len(post_id(a)) == 10


def test_post_id_without_url_uses_author_and_body():
    assert post_id({"origin": "linkedin", "author": "A", "body": "x" * 200}) == \
        post_id({"origin": "linkedin", "author": "A", "body": "x" * 200 + "different tail"})


def test_origin_of_legacy_sources():
    assert origin_of({"_source": "feed"}) == "linkedin"
    assert origin_of({"_source": "search:aws"}) == "linkedin"
    assert origin_of({"_source": "reddit:r/aws"}) == "reddit"


def test_normalize_post_defaults():
    p = normalize_post({"_source": "saved", "body": "b" * 50, "author": "x", "links": ["https://a.io"]})
    assert p["origin"] == "linkedin" and p["_priority"] == "high" and p["url"] == "https://a.io"
    assert p["id"]


def test_misc():
    assert domain_of("https://www.a.io/x") == "a.io"
    assert clip("abcdef", 4) == "abc…"
    assert estimate_tokens("a" * 400) == 100
