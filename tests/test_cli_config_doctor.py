import pytest

from daily_brief import cli
from daily_brief.config import DEFAULTS, Config, deep_merge, load_config
from daily_brief.doctor import run_checks
from tests.conftest import TODAY


def test_deep_merge_keeps_unspecified_defaults():
    merged = deep_merge(DEFAULTS, {"sources": {"hackernews": {"min_points": 5}}})
    assert merged["sources"]["hackernews"] == {"enabled": True, "max_items": 30, "min_points": 5}
    assert merged["sources"]["reddit"]["enabled"] is True
    assert DEFAULTS["sources"]["hackernews"]["min_points"] == 50  # not mutated


def test_load_config_resolves_paths_relative_to_file(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("paths: {data_dir: mydata}\nllm: {model: none}\n")
    cfg = load_config(str(f))
    assert cfg.data_dir == tmp_path / "mydata" and cfg.get("llm.model") == "none"
    assert cfg.get("nope.nothing", 7) == 7


def test_load_config_missing_explicit_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(str(tmp_path / "missing.yaml"))


def test_shipped_example_config_is_valid(tmp_path, monkeypatch):
    from importlib import resources
    (tmp_path / "config.yaml").write_text(
        resources.files("daily_brief").joinpath("config.example.yaml").read_text())
    cfg = load_config(str(tmp_path / "config.yaml"))
    assert cfg.get("sources.linkedin.enabled") is False
    assert cfg.get("sources.linkedin.accept_tos") is False
    assert cfg.get("sources.linkedin.mcp_version")  # pinned by default


def test_doctor_flags_unsafe_linkedin_setup(cfg):
    cfg.data["sources"]["linkedin"].update(enabled=True, session_dir=str(cfg.base_dir / "nope"))
    checks = {c.name: c for c in run_checks(cfg, model="none")}
    assert checks["LinkedIn terms acknowledgement"].status == "fail"
    assert checks["LinkedIn session"].status == "fail"
    assert checks["LLM"].status == "ok"


def test_doctor_missing_api_key(cfg, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    checks = {c.name: c for c in run_checks(cfg, model="claude")}
    assert checks["ANTHROPIC_API_KEY"].status == "fail"


# ── CLI end to end (no network: today's cache is the bundled sample) ─────────────────────────

@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("daily_brief.logging_setup.setup", lambda *a, **k: None)
    (tmp_path / "config.yaml").write_text("llm: {model: none}\n")
    cfg = Config(base_dir=tmp_path)
    cfg.raw_dir.mkdir(parents=True)
    from importlib import resources
    (cfg.raw_dir / f"{TODAY}.json").write_text(
        resources.files("daily_brief").joinpath("sample_raw.json").read_text())
    import datetime
    real = datetime.date

    class D(real):
        @classmethod
        def today(cls):
            return real.fromisoformat(TODAY)
    monkeypatch.setattr("daily_brief.pipeline.dt.date", D)
    monkeypatch.setattr("daily_brief.cli.dt.date", D)
    return tmp_path


def run_cli(*argv):
    with pytest.raises(SystemExit) as e:
        cli.main(list(argv))
    return e.value.code


def test_cli_run_sample_and_default_command(work, capsys):
    assert run_cli("run", "--sample", "--model", "none") == 0
    out = capsys.readouterr().out
    assert "Daily Brief" in out and not (work / "data" / "briefs").exists()


def test_cli_init_refuses_overwrite(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("daily_brief.logging_setup.setup", lambda *a, **k: None)
    assert run_cli("init") == 0 and (tmp_path / "config.yaml").exists()
    assert run_cli("init") == 1


def test_cli_feedback_and_backlog_flow(work, capsys):
    assert run_cli("run", "--model", "none", "--no-history") == 0
    capsys.readouterr()

    assert run_cli("feedback", "list") == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    ref = lines[0].split()[0]
    assert len(ref) == 7

    assert run_cli("feedback", "up", ref) == 0
    assert run_cli("feedback", "weights") == 0
    assert "+0.5" in capsys.readouterr().out
    assert run_cli("feedback", "up", "zzzzzzz") == 1

    assert run_cli("backlog", "list") == 0
    out = capsys.readouterr().out
    assert "1 item(s)" in out
    bref = out.split()[0]
    assert run_cli("backlog", "tag", bref, "must-read") == 0
    assert run_cli("backlog", "read", bref) == 0
    capsys.readouterr()
    assert run_cli("backlog", "list") == 0
    assert "0 item(s)" in capsys.readouterr().out
    assert run_cli("backlog", "list", "--status", "read", "--tag", "must-read") == 0
    assert "1 item(s)" in capsys.readouterr().out
    assert run_cli("backlog", "add", ref) == 0
    assert run_cli("cost") == 0


def test_cli_bad_model_reports_error(work, capsys):
    assert run_cli("run", "--sample", "--model", "wat") == 1
    assert "Unknown model" in capsys.readouterr().err
