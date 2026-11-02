"""Command line interface: `daily-brief <command>`."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path

from . import __version__, feedback, logging_setup, schedule, triage
from .config import Config, load_config
from .doctor import run_checks
from .llm.cost import format_usage
from .llm.providers import LLMError
from .pipeline import RunError, RunOptions, run
from .store import AmbiguousRef, Store

ICON = {"ok": "✓", "warn": "!", "fail": "✗"}


def _store(cfg: Config) -> Store:
    return Store(cfg.db_path)


def cmd_run(cfg: Config, a: argparse.Namespace) -> int:
    opts = RunOptions(model=a.model, dry_run=a.dry_run, refetch=a.refetch, force=a.force,
                      sample=a.sample, estimate_only=a.estimate, redact=a.redact,
                      only=a.only.split(",") if a.only else None, use_history=not a.no_history)
    store = _store(cfg)
    try:
        res = run(cfg, opts, store)
    except (RunError, LLMError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        store.close()
    if res.skipped:
        print(res.skipped)
        return 0
    c = res.counts
    print(f"Collected {c.get('collected', 0)} · filtered {c.get('filtered_out', 0)} · "
          f"already seen {c.get('already_seen', 0)} · signal {c.get('signal', 0)} · "
          f"sent to LLM {c.get('sent_to_llm', 0)}", file=sys.stderr)
    for n, e in res.source_errors.items():
        print(f"warning: source {n} failed: {e}", file=sys.stderr)
    if opts.dry_run or opts.estimate_only:
        if res.usage:
            print(f"Estimate: ~{res.usage.input_tokens:,} input tokens "
                  f"({res.usage.provider}:{res.usage.model})")
        return 0
    print(res.brief)
    if res.brief_path:
        print(f"\nSaved → {res.brief_path}", file=sys.stderr)
    if res.usage:
        print(f"Usage: {format_usage(res.usage)}", file=sys.stderr)
    return 0


def cmd_init(cfg: Config, a: argparse.Namespace) -> int:
    dest = Path.cwd() / "config.yaml"
    if dest.exists() and not a.force:
        print(f"{dest} already exists (use --force to overwrite)")
        return 1
    dest.write_text(resources.files("daily_brief").joinpath("config.example.yaml")
                    .read_text(encoding="utf-8"))
    print(f"Wrote {dest}")
    env_example = Path.cwd() / ".env.example"
    if not env_example.exists() and not (Path.cwd() / ".env").exists():
        print("Tip: cloud models need keys in a .env file (see .env.example in the repo).")
    print("Next: daily-brief doctor   then   daily-brief run --sample")
    return 0


def cmd_doctor(cfg: Config, a: argparse.Namespace) -> int:
    failed = False
    for c in run_checks(cfg, online=a.online, model=a.model):
        print(f" {ICON[c.status]} {c.name}" + (f": {c.detail}" if c.detail else ""))
        failed |= c.status == "fail"
    return 1 if failed else 0


def cmd_feedback(cfg: Config, a: argparse.Namespace) -> int:
    store = _store(cfg)
    try:
        if a.action in ("up", "down"):
            for ref in a.refs:
                r = feedback.vote(store, cfg, ref, 1 if a.action == "up" else -1, a.note or "")
                print(f"{'👍' if a.action == 'up' else '👎'} {r['id'][:7]} {r['title'][:60]} "
                      f"({len(r['features'])} features {r['delta']:+.1f})")
        elif a.action == "list":
            for i in store.latest_items(a.limit):
                print(f"{i['id'][:7]}  {i['brief_date']}  #{i['rank']:<3} {i['origin']:<10} "
                      f"{(i['title'] or '')[:70]}")
        elif a.action == "weights":
            up, down = feedback.top_weights(store)
            print("Boosted:" if up else "No learned weights yet.")
            for f, w in up:
                print(f"  {w:+.1f}  {f}")
            if down:
                print("Lowered:")
                for f, w in down:
                    print(f"  {w:+.1f}  {f}")
        elif a.action == "reset":
            store.reset_weights()
            print("Learned weights cleared.")
    except (KeyError, AmbiguousRef) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        store.close()
    return 0


def cmd_backlog(cfg: Config, a: argparse.Namespace) -> int:
    store = _store(cfg)
    today = dt.date.today().isoformat()
    try:
        if a.action in ("list", "stale"):
            items = (triage.stale_items(store, cfg, today) if a.action == "stale"
                     else store.backlog(None if a.status == "all" else a.status))
            if a.tag:
                items = [i for i in items if a.tag in i["tags"]]
            for i in items:
                print(f"{i['id'][:7]}  {triage.age_days(i, today):>3}d  {i['status']:<8} "
                      f"[{','.join(i['tags'])}] {(i['title'] or '')[:70]}")
            print(f"\n{len(items)} item(s)")
            if a.action == "list" and not items:
                print("Backlog fills from LinkedIn saved posts, or `daily-brief backlog add <ref>`.")
        elif a.action in ("read", "archive", "unread"):
            status = {"read": "read", "archive": "archived", "unread": "unread"}[a.action]
            for ref in a.refs:
                row = store.resolve("backlog", ref)
                store.set_backlog(row["id"], status=status)
                print(f"{row['id'][:7]} → {status}")
        elif a.action == "tag":
            row = store.resolve("backlog", a.refs[0])
            store.set_backlog(row["id"], tags=[*json.loads(row["tags"]), *a.refs[1:]])
            print(f"{row['id'][:7]} tagged {', '.join(a.refs[1:])}")
        elif a.action == "add":
            for ref in a.refs:
                row = store.resolve("items", ref)
                post = {"id": row["id"], "origin": row["origin"], "title": row["title"],
                        "author": row["author"], "url": row["url"], "body": ""}
                new = store.upsert_backlog(post, today, ["manual"])
                print(f"{row['id'][:7]} {'added' if new else 'already in backlog'}")
        elif a.action == "summary":
            s = triage.summary(store, cfg, today)
            print(f"{s['unread']} unread · {s['stale']} stale (≥ {s['stale_days']}d)")
            for t, n in s["by_tag"].items():
                print(f"  {t}: {n}")
    except (KeyError, AmbiguousRef) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        store.close()
    return 0


def cmd_schedule(cfg: Config, a: argparse.Namespace) -> int:
    try:
        if a.action == "install":
            print(schedule.install(cfg, a.time, a.model))
        elif a.action == "uninstall":
            print(schedule.uninstall())
        elif a.action == "run-now":
            print(schedule.run_now())
        else:
            print(schedule.status())
    except schedule.ScheduleError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_cost(cfg: Config, a: argparse.Namespace) -> int:
    store = _store(cfg)
    try:
        rows = store.runs(a.limit)
    finally:
        store.close()
    if not rows:
        print("No runs recorded yet.")
        return 0
    total, unknown = 0.0, 0
    for r in rows:
        cost = "unknown" if r["cost_usd"] is None else f"${r['cost_usd']:.4f}"
        total += r["cost_usd"] or 0
        unknown += r["cost_usd"] is None
        print(f"{r['brief_date']}  {r['model']:<32} {'~' if r['estimated'] else ' '}"
              f"{r['input_tokens']:>8,} in {r['output_tokens']:>7,} out  {cost}")
    print(f"\nTotal (last {len(rows)} runs): ${total:.4f}"
          + (f" + {unknown} run(s) with unknown price" if unknown else ""))
    return 0


def cmd_linkedin(cfg: Config, a: argparse.Namespace) -> int:
    from .sources.linkedin.mcp_client import package_spec

    c = cfg.get("sources.linkedin")
    if not shutil.which("uvx"):
        print("error: uvx not found. Install uv: https://docs.astral.sh/uv/", file=sys.stderr)
        return 1
    spec = package_spec(c["mcp_package"], c["mcp_version"])
    if a.action == "login":
        print("Opening a browser window. Log in to YOUR account normally, then close it.\n"
              "Reminder: automated access may violate LinkedIn's terms (docs/linkedin.md).")
        return subprocess.call(["uvx", spec, "--login", "--no-headless"])
    return subprocess.call(["uvx", spec, "--status"])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="daily-brief", description=__doc__)
    p.add_argument("--version", action="version", version=f"daily-brief {__version__}")
    p.add_argument("--config", help="path to config.yaml (default: ./config.yaml)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command")

    r = sub.add_parser("run", help="collect, summarise and write today's brief (default)")
    r.add_argument("--model", help="none | ollama[:m] | claude[-sonnet|-opus] | gpt4o | gemini | "
                                   "<provider>:<model>")
    r.add_argument("--dry-run", action="store_true", help="collect + save raw, skip the LLM")
    r.add_argument("--estimate", action="store_true", help="print token/cost estimate and stop")
    r.add_argument("--refetch", action="store_true", help="ignore today's cached raw data")
    r.add_argument("--force", action="store_true", help="regenerate even if today's brief exists")
    r.add_argument("--sample", action="store_true",
                   help="use bundled fake data: no network, no accounts, nothing saved")
    r.add_argument("--redact", dest="redact", action="store_true", default=None,
                   help="pseudonymise authors / hide your profile before sending to the LLM")
    r.add_argument("--no-redact", dest="redact", action="store_false")
    r.add_argument("--only", help="comma-separated source names to collect")
    r.add_argument("--no-history", action="store_true", help="don't hide items seen on earlier days")
    r.set_defaults(fn=cmd_run)

    i = sub.add_parser("init", help="write a starter config.yaml here")
    i.add_argument("--force", action="store_true")
    i.set_defaults(fn=cmd_init)

    d = sub.add_parser("doctor", help="check your setup")
    d.add_argument("--online", action="store_true", help="also test network reachability")
    d.add_argument("--model")
    d.set_defaults(fn=cmd_doctor)

    f = sub.add_parser("feedback", help="vote on brief items to tune future ranking")
    f.add_argument("action", choices=["up", "down", "list", "weights", "reset"])
    f.add_argument("refs", nargs="*", help="item refs from the brief's Sources index")
    f.add_argument("--note")
    f.add_argument("--limit", type=int, default=30)
    f.set_defaults(fn=cmd_feedback)

    b = sub.add_parser("backlog", help="triage your reading list (saved posts etc.)")
    b.add_argument("action", choices=["list", "stale", "summary", "read", "unread", "archive",
                                      "tag", "add"])
    b.add_argument("refs", nargs="*", help="item ref(s); for `tag`: <ref> <tag> [<tag>...]")
    b.add_argument("--status", default="unread", choices=["unread", "read", "archived", "all"])
    b.add_argument("--tag")
    b.set_defaults(fn=cmd_backlog)

    s = sub.add_parser("schedule", help="run daily automatically (launchd / systemd)")
    s.add_argument("action", choices=["install", "uninstall", "status", "run-now"])
    s.add_argument("--time", default="08:00", help="HH:MM (default 08:00)")
    s.add_argument("--model", help="model to schedule with (default: from config)")
    s.set_defaults(fn=cmd_schedule)

    c = sub.add_parser("cost", help="token / cost history")
    c.add_argument("--limit", type=int, default=30)
    c.set_defaults(fn=cmd_cost)

    li = sub.add_parser("linkedin", help="LinkedIn session helpers (opt-in source)")
    li.add_argument("action", choices=["login", "status"])
    li.set_defaults(fn=cmd_linkedin)
    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        args = parser.parse_args([*(argv if argv is not None else sys.argv[1:]), "run"])
    try:
        cfg = load_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    logging_setup.setup(cfg, args.verbose)
    sys.exit(args.fn(cfg, args))


if __name__ == "__main__":
    main()
