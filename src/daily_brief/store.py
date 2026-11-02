"""SQLite store: dedup history, items shown in briefs, feedback, learned weights, backlog, runs.

Everything is local. The DB lives in <data_dir>/daily-brief.db and is gitignored.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
  id TEXT PRIMARY KEY, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
  times_seen INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY, brief_date TEXT NOT NULL, rank INTEGER, origin TEXT, author TEXT,
  title TEXT, url TEXT, features TEXT);
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL, vote INTEGER NOT NULL,
  note TEXT, ts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS weights (feature TEXT PRIMARY KEY, weight REAL NOT NULL);
CREATE TABLE IF NOT EXISTS backlog (
  id TEXT PRIMARY KEY, origin TEXT, title TEXT, author TEXT, url TEXT, excerpt TEXT,
  first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'unread',
  tags TEXT NOT NULL DEFAULT '[]', note TEXT);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, brief_date TEXT, model TEXT,
  input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, estimated INTEGER, items INTEGER);
"""


class AmbiguousRef(Exception):
    pass


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # ── dedup history ────────────────────────────────────────────────────────
    def seen_before(self, post_id: str, today: str, within_days: int) -> bool:
        """True if this id was already shown/collected on an EARLIER day within the window."""
        row = self.db.execute("SELECT first_seen FROM seen WHERE id=?", (post_id,)).fetchone()
        if not row or row["first_seen"] >= today:
            return False
        cutoff = (dt.date.fromisoformat(today) - dt.timedelta(days=within_days)).isoformat()
        return row["first_seen"] >= cutoff

    def mark_seen(self, ids: list[str], today: str) -> None:
        for i in ids:
            self.db.execute(
                "INSERT INTO seen(id, first_seen, last_seen) VALUES(?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen, "
                "times_seen=CASE WHEN last_seen<>excluded.last_seen THEN times_seen+1 "
                "ELSE times_seen END", (i, today, today))
        self.db.commit()

    # ── items shown in briefs (feedback targets) ─────────────────────────────
    def record_items(self, brief_date: str, posts: list[dict]) -> None:
        for rank, p in enumerate(posts, 1):
            self.db.execute(
                "INSERT OR REPLACE INTO items(id, brief_date, rank, origin, author, title, url, "
                "features) VALUES(?,?,?,?,?,?,?,?)",
                (p["id"], brief_date, rank, p.get("origin"), p.get("author"),
                 p.get("title") or p.get("body", "")[:80], p.get("url"),
                 json.dumps(p.get("_features", []))))
        self.db.commit()

    def resolve(self, table: str, ref: str) -> sqlite3.Row:
        rows = self.db.execute(f"SELECT * FROM {table} WHERE id LIKE ?",  # noqa: S608 (fixed names)
                               (ref.lower() + "%",)).fetchall()
        if not rows:
            raise KeyError(f"No item matches '{ref}'")
        if len(rows) > 1:
            raise AmbiguousRef(f"'{ref}' matches {len(rows)} items; use more characters")
        return rows[0]

    def latest_items(self, limit: int = 30) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM items ORDER BY brief_date DESC, rank ASC LIMIT ?", (limit,)).fetchall()

    # ── feedback + weights ───────────────────────────────────────────────────
    def add_feedback(self, item_id: str, vote: int, note: str = "") -> None:
        self.db.execute("INSERT INTO feedback(item_id, vote, note, ts) VALUES(?,?,?,?)",
                        (item_id, vote, note, dt.datetime.now().isoformat(timespec="seconds")))
        self.db.commit()

    def weights(self) -> dict[str, float]:
        return {r["feature"]: r["weight"]
                for r in self.db.execute("SELECT feature, weight FROM weights")}

    def bump_weights(self, features: list[str], delta: float, max_weight: float) -> None:
        for f in features:
            cur = self.db.execute("SELECT weight FROM weights WHERE feature=?", (f,)).fetchone()
            w = max(-max_weight, min(max_weight, (cur["weight"] if cur else 0.0) + delta))
            self.db.execute("INSERT OR REPLACE INTO weights(feature, weight) VALUES(?,?)", (f, w))
        self.db.commit()

    def reset_weights(self) -> None:
        self.db.execute("DELETE FROM weights")
        self.db.commit()

    # ── backlog (reading list) ───────────────────────────────────────────────
    def upsert_backlog(self, post: dict, today: str, tags: list[str]) -> bool:
        """Insert or refresh. Returns True when the item is new."""
        row = self.db.execute("SELECT tags FROM backlog WHERE id=?", (post["id"],)).fetchone()
        title = post.get("title") or post.get("body", "")[:80].replace("\n", " ")
        excerpt = (post.get("body") or "")[:300]
        if row is None:
            self.db.execute(
                "INSERT INTO backlog(id, origin, title, author, url, excerpt, first_seen, "
                "last_seen, tags) VALUES(?,?,?,?,?,?,?,?,?)",
                (post["id"], post.get("origin"), title, post.get("author"), post.get("url"),
                 excerpt, today, today, json.dumps(sorted(set(tags)))))
            self.db.commit()
            return True
        merged = sorted(set(json.loads(row["tags"])) | set(tags))
        self.db.execute("UPDATE backlog SET last_seen=?, tags=? WHERE id=?",
                        (today, json.dumps(merged), post["id"]))
        self.db.commit()
        return False

    def set_backlog(self, item_id: str, *, status: str | None = None,
                    tags: list[str] | None = None, note: str | None = None) -> None:
        if status:
            self.db.execute("UPDATE backlog SET status=? WHERE id=?", (status, item_id))
        if tags is not None:
            self.db.execute("UPDATE backlog SET tags=? WHERE id=?",
                            (json.dumps(sorted(set(tags))), item_id))
        if note is not None:
            self.db.execute("UPDATE backlog SET note=? WHERE id=?", (note, item_id))
        self.db.commit()

    def backlog(self, status: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM backlog" + (" WHERE status=?" if status else "")
        q += " ORDER BY first_seen ASC, id"
        rows = self.db.execute(q, (status,) if status else ()).fetchall()
        return [{**dict(r), "tags": json.loads(r["tags"])} for r in rows]

    # ── runs / cost ──────────────────────────────────────────────────────────
    def record_run(self, brief_date: str, model: str, input_tokens: int, output_tokens: int,
                   cost_usd: float | None, estimated: bool, items: int) -> None:
        self.db.execute(
            "INSERT INTO runs(ts, brief_date, model, input_tokens, output_tokens, cost_usd, "
            "estimated, items) VALUES(?,?,?,?,?,?,?,?)",
            (dt.datetime.now().isoformat(timespec="seconds"), brief_date, model, input_tokens,
             output_tokens, cost_usd, int(estimated), items))
        self.db.commit()

    def runs(self, limit: int = 30) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
