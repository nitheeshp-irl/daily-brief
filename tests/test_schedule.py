import plistlib

import pytest

from daily_brief import schedule


def test_parse_time():
    assert schedule.parse_time("08:30") == (8, 30)
    for bad in ("8", "25:00", "08:61", "ab:cd"):
        with pytest.raises(schedule.ScheduleError):
            schedule.parse_time(bad)


def test_plist_is_valid_and_uses_current_interpreter(cfg, tmp_path):
    cfg.path = tmp_path / "config.yaml"
    plist = plistlib.loads(schedule.launchd_plist(cfg, 7, 45, "ollama:llama3.2"))
    args = plist["ProgramArguments"]
    assert args[1:3] == ["-m", "daily_brief"] and args[-2:] == ["--model", "ollama:llama3.2"]
    assert "--config" in args and plist["StartCalendarInterval"] == {"Hour": 7, "Minute": 45}
    assert plist["WorkingDirectory"] == str(tmp_path.resolve())
    assert plist["EnvironmentVariables"]["PATH"]  # so uvx / ollama resolve under launchd


def test_plist_escapes_odd_paths(cfg, tmp_path):
    cfg.base_dir = tmp_path / "A & B <dir>"
    cfg.base_dir.mkdir()
    assert plistlib.loads(schedule.launchd_plist(cfg, 8, 0, None))["WorkingDirectory"].endswith("<dir>")


def test_systemd_units_are_persistent(cfg):
    service, timer = schedule.systemd_units(cfg, 8, 5, None)
    assert "Persistent=true" in timer and "OnCalendar=*-*-* 08:05:00" in timer
    assert "ExecStart=" in service and "daily_brief" in service
