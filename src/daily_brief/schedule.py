"""Install a daily job: launchd on macOS, a systemd user timer on Linux.

Both catch up after sleep/downtime (launchd runs missed jobs on wake; the systemd timer is
Persistent=true), and `daily-brief run` is idempotent per day, so a catch-up never duplicates.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from .config import Config

LABEL = "com.daily-brief.agent"


class ScheduleError(Exception):
    pass


def parse_time(s: str) -> tuple[int, int]:
    try:
        h, m = s.split(":")
        h, m = int(h), int(m)
    except ValueError as e:
        raise ScheduleError(f"Time must be HH:MM, got '{s}'") from e
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ScheduleError(f"Time out of range: {s}")
    return h, m


def command(cfg: Config, model: str | None) -> list[str]:
    cmd = [sys.executable, "-m", "daily_brief"]
    if cfg.path:
        cmd += ["--config", str(cfg.path.resolve())]
    cmd.append("run")
    if model:
        cmd += ["--model", model]
    return cmd


def launchd_plist(cfg: Config, hour: int, minute: int, model: str | None) -> bytes:
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": command(cfg, model),
        "WorkingDirectory": str(cfg.base_dir.resolve()),
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": str(cfg.logs_dir / "launchd-stdout.log"),
        "StandardErrorPath": str(cfg.logs_dir / "launchd-stderr.log"),
        # launchd has a minimal PATH; carry over the current one so `uvx`/`ollama` resolve
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                 "HOME": str(Path.home())},
        "RunAtLoad": False,
    })


def systemd_units(cfg: Config, hour: int, minute: int, model: str | None) -> tuple[str, str]:
    exec_ = " ".join(f'"{c}"' if " " in c else c for c in command(cfg, model))
    service = (f"[Unit]\nDescription=daily-brief\n\n[Service]\nType=oneshot\n"
               f"WorkingDirectory={cfg.base_dir.resolve()}\n"
               f"Environment=PATH={os.environ.get('PATH', '/usr/bin:/bin')}\nExecStart={exec_}\n")
    timer = (f"[Unit]\nDescription=daily-brief daily timer\n\n[Timer]\n"
             f"OnCalendar=*-*-* {hour:02d}:{minute:02d}:00\nPersistent=true\n\n"
             f"[Install]\nWantedBy=timers.target\n")
    return service, timer


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _systemd_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def install(cfg: Config, time: str, model: str | None) -> str:
    hour, minute = parse_time(time)
    if sys.platform == "darwin":
        path = _plist_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(launchd_plist(cfg, hour, minute, model))
        uid = os.getuid()
        _run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"])  # replace if already loaded
        r = _run(["launchctl", "bootstrap", f"gui/{uid}", str(path)])
        if r.returncode != 0:
            raise ScheduleError(f"launchctl bootstrap failed: {r.stderr.strip()}")
        return f"Installed launchd job at {hour:02d}:{minute:02d} ({path}). Logs: {cfg.logs_dir}"
    if sys.platform.startswith("linux") and shutil.which("systemctl"):
        d = _systemd_dir()
        d.mkdir(parents=True, exist_ok=True)
        service, timer = systemd_units(cfg, hour, minute, model)
        (d / "daily-brief.service").write_text(service)
        (d / "daily-brief.timer").write_text(timer)
        _run(["systemctl", "--user", "daemon-reload"])
        r = _run(["systemctl", "--user", "enable", "--now", "daily-brief.timer"])
        if r.returncode != 0:
            raise ScheduleError(f"systemctl failed: {r.stderr.strip()}")
        return f"Installed systemd user timer at {hour:02d}:{minute:02d} ({d})"
    cron = f"{minute} {hour} * * * cd {cfg.base_dir.resolve()} && {' '.join(command(cfg, model))}"
    return "No launchd/systemd found. Add this line to your crontab (crontab -e):\n" + cron


def uninstall() -> str:
    if sys.platform == "darwin":
        path = _plist_path()
        _run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"])
        if path.exists():
            path.unlink()
            return "Uninstalled."
        return "Nothing to uninstall."
    if shutil.which("systemctl"):
        _run(["systemctl", "--user", "disable", "--now", "daily-brief.timer"])
        for n in ("daily-brief.service", "daily-brief.timer"):
            (_systemd_dir() / n).unlink(missing_ok=True)
        return "Uninstalled."
    return "Remove the daily-brief line from your crontab manually."


def run_now() -> str:
    if sys.platform == "darwin":
        r = _run(["launchctl", "kickstart", f"gui/{os.getuid()}/{LABEL}"])
    else:
        r = _run(["systemctl", "--user", "start", "daily-brief.service"])
    if r.returncode != 0:
        raise ScheduleError(f"Could not trigger the job (is it installed?): {r.stderr.strip()}")
    return "Triggered. Check the logs."


def status() -> str:
    if sys.platform == "darwin":
        r = _run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"])
        return "installed (launchd)" if r.returncode == 0 else "not installed"
    if shutil.which("systemctl"):
        r = _run(["systemctl", "--user", "is-enabled", "daily-brief.timer"])
        return f"systemd timer: {r.stdout.strip() or 'not installed'}"
    return "no scheduler detected"
