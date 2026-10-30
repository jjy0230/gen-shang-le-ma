"""SQLite migrations and consistent transactions; no destructive schema reset."""

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class Database:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        if Path(path).exists():
            with sqlite3.connect(path) as source:
                version = source.execute("PRAGMA user_version").fetchone()[0]
                if version < 4:
                    backup = str(path) + (
                        ".pre-v3.bak" if version < 3 else ".pre-v4.bak"
                    )
                    if not Path(backup).exists():
                        with sqlite3.connect(backup) as target:
                            source.backup(target)
        with self.connect() as c:
            c.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS rooms(code TEXT PRIMARY KEY,title TEXT,token TEXT,created REAL,closed INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS people(id TEXT PRIMARY KEY,room TEXT REFERENCES rooms(code) ON DELETE CASCADE,name TEXT,token TEXT,seen REAL,joined REAL);
            CREATE TABLE IF NOT EXISTS steps(id TEXT PRIMARY KEY,room TEXT REFERENCES rooms(code) ON DELETE CASCADE,title TEXT,created REAL);
            CREATE TABLE IF NOT EXISTS statuses(step TEXT REFERENCES steps(id) ON DELETE CASCADE,person TEXT REFERENCES people(id) ON DELETE CASCADE,state TEXT,updated REAL,PRIMARY KEY(step,person));
            CREATE TABLE IF NOT EXISTS issues(id TEXT PRIMARY KEY,step TEXT REFERENCES steps(id) ON DELETE CASCADE,title TEXT,resolved INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS votes(issue TEXT REFERENCES issues(id) ON DELETE CASCADE,person TEXT REFERENCES people(id) ON DELETE CASCADE,PRIMARY KEY(issue,person));
            CREATE TABLE IF NOT EXISTS roles(id TEXT PRIMARY KEY,room TEXT REFERENCES rooms(code) ON DELETE CASCADE,name TEXT,token TEXT,role TEXT,revoked INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS answers(id TEXT PRIMARY KEY,issue TEXT REFERENCES issues(id) ON DELETE CASCADE,body TEXT,link TEXT,public INTEGER,created REAL,author TEXT);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,room TEXT REFERENCES rooms(code) ON DELETE CASCADE,kind TEXT,entity TEXT,actor TEXT,created REAL,detail TEXT);
            CREATE TABLE IF NOT EXISTS requests(room TEXT REFERENCES rooms(code) ON DELETE CASCADE,actor TEXT,key TEXT,fingerprint TEXT,result TEXT,PRIMARY KEY(room,actor,key));
            CREATE TABLE IF NOT EXISTS tails(step TEXT REFERENCES steps(id) ON DELETE CASCADE,person TEXT REFERENCES people(id) ON DELETE CASCADE,recovered INTEGER DEFAULT 0,updated REAL,PRIMARY KEY(step,person));
            """)
            fields = {
                "rooms": {
                    "expected": "INTEGER DEFAULT 0",
                    "revision": "INTEGER DEFAULT 0",
                    "announcement": "TEXT DEFAULT ''",
                    "timer_end": "REAL DEFAULT 0",
                },
                "people": {
                    "joined": "REAL DEFAULT 0",
                    "left_at": "REAL",
                    "seat": "TEXT DEFAULT ''",
                    "environment": "TEXT DEFAULT 'unspecified'",
                },
                "steps": {
                    "criteria": "TEXT DEFAULT ''",
                    "link": "TEXT DEFAULT ''",
                    "reasons": "TEXT DEFAULT '[]'",
                    "tips": "TEXT DEFAULT ''",
                    "options": "TEXT DEFAULT '{}'",
                    "phase": "TEXT DEFAULT 'active'",
                    "position": "INTEGER DEFAULT 0",
                    "opened": "REAL",
                    "frozen": "TEXT",
                },
                "statuses": {"version": "INTEGER DEFAULT 1"},
                "issues": {
                    "stage": "TEXT DEFAULT 'open'",
                    "assignee": "TEXT DEFAULT ''",
                    "merged_into": "TEXT",
                    "created": "REAL DEFAULT 0",
                    "version": "INTEGER DEFAULT 1",
                },
                "votes": {
                    "active": "INTEGER DEFAULT 1",
                    "updated": "REAL DEFAULT 0",
                    "version": "INTEGER DEFAULT 1",
                },
            }
            for table, columns in fields.items():
                existing = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
                for name, definition in columns.items():
                    if name not in existing:
                        c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            if c.execute("PRAGMA user_version").fetchone()[0] < 3:
                for r in c.execute("SELECT code FROM rooms").fetchall():
                    steps = c.execute(
                        "SELECT * FROM steps WHERE room=? ORDER BY created,rowid",
                        (r[0],),
                    ).fetchall()
                    for idx, s in enumerate(steps):
                        frozen = None
                        if idx < len(steps) - 1:
                            end = steps[idx + 1]["created"]
                            people = c.execute(
                                "SELECT id FROM people WHERE room=? AND joined<=?",
                                (r[0], end),
                            ).fetchall()
                            states = dict(
                                c.execute(
                                    "SELECT person,state FROM statuses WHERE step=?",
                                    (s["id"],),
                                )
                            )
                            frozen = json.dumps(
                                {
                                    "total": len(people),
                                    "counts": {
                                        k: sum(
                                            states.get(p[0], "waiting") == k
                                            for p in people
                                        )
                                        for k in ("done", "working", "stuck", "waiting")
                                    },
                                    "ended": end,
                                }
                            )
                        c.execute(
                            "UPDATE steps SET position=?,opened=created,phase=?,frozen=? WHERE id=?",
                            (idx, "frozen" if frozen else "active", frozen, s["id"]),
                        )
                c.execute(
                    "UPDATE issues SET stage=CASE WHEN resolved=1 THEN 'answered' ELSE 'open' END"
                )
                c.execute("PRAGMA user_version=3")

            c.execute("PRAGMA user_version=4")

    @contextmanager
    def connect(self, write=False):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            c.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def backup(self, target):
        with sqlite3.connect(self.path) as source, sqlite3.connect(target) as dest:
            source.backup(dest)
