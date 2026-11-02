import json
from importlib import resources

import pytest

from daily_brief.config import Config
from daily_brief.store import Store

TODAY = "2026-11-02"


@pytest.fixture
def cfg(tmp_path):
    return Config(base_dir=tmp_path)


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def sample_sources():
    from daily_brief.pipeline import load_sample

    return load_sample()


@pytest.fixture
def seeded_cache(cfg):
    """Today's raw cache = the bundled sample, so `run` never touches the network."""
    cfg.raw_dir.mkdir(parents=True)
    src = resources.files("daily_brief").joinpath("sample_raw.json")
    data = json.loads(src.read_text())
    (cfg.raw_dir / f"{TODAY}.json").write_text(json.dumps(data))
    return cfg


def make_post(**kw):
    p = {"origin": "hackernews", "_source": "hn", "title": "Terraform tips", "author": "a",
         "body": "Kubernetes and terraform in production, lessons learned", "url": "https://x.io/a",
         "links": ["https://x.io/a"]}
    p.update(kw)
    from daily_brief.models import normalize_post

    return normalize_post(p)
