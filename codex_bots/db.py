"""Small SQLite persistence layer for Bots, conversations, and handoffs."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .roles import DEFAULT_BOTS


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Store:
    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS bots (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    color TEXT NOT NULL,
                    shape TEXT NOT NULL,
                    codex_thread_id TEXT,
                    status TEXT NOT NULL DEFAULT 'idle',
                    status_text TEXT NOT NULL DEFAULT '',
                    is_default INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    bot_id TEXT NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    author_name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'text',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_messages_bot_created
                    ON messages(bot_id, created_at);

                CREATE TABLE IF NOT EXISTS routines (
                    id TEXT PRIMARY KEY,
                    bot_id TEXT NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    schedule_json TEXT NOT NULL DEFAULT '{}',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    next_run_at TEXT,
                    last_run_at TEXT,
                    last_status TEXT NOT NULL DEFAULT 'never',
                    lease_until TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_routines_due
                    ON routines(enabled, next_run_at);

                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    bot_id TEXT NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
                    routine_id TEXT REFERENCES routines(id) ON DELETE SET NULL,
                    trigger TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_runs_bot_created
                    ON runs(bot_id, created_at DESC);
                """
            )
            count = db.execute("SELECT COUNT(*) FROM bots").fetchone()[0]
            if count == 0:
                now = utc_now()
                for bot in DEFAULT_BOTS:
                    db.execute(
                        """
                        INSERT INTO bots (
                            id, name, title, description, color, shape,
                            is_default, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                        """,
                        (
                            bot.id,
                            bot.name,
                            bot.title,
                            bot.description,
                            bot.color,
                            bot.shape,
                            now,
                            now,
                        ),
                    )
                    self._add_message_with_db(
                        db,
                        bot_id=bot.id,
                        role="assistant",
                        author_name=bot.name,
                        content=bot.welcome,
                        kind="welcome",
                        created_at=now,
                    )
            interrupted = db.execute(
                "SELECT DISTINCT bot_id FROM runs WHERE status IN ('queued', 'working', 'stopping')"
            ).fetchall()
            if interrupted:
                now = utc_now()
                db.execute(
                    """
                    UPDATE runs SET status = 'interrupted',
                        error = 'The local worker stopped before this run finished.',
                        finished_at = ?
                    WHERE status IN ('queued', 'working', 'stopping')
                    """,
                    (now,),
                )
                for row in interrupted:
                    db.execute(
                        """
                        UPDATE bots SET status = 'error', status_text = 'Interrupted by restart',
                            updated_at = ? WHERE id = ?
                        """,
                        (now, row["bot_id"]),
                    )

    def list_bots(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT b.*,
                    (SELECT content FROM messages m WHERE m.bot_id = b.id
                     ORDER BY m.created_at DESC LIMIT 1) AS latest_message,
                    (SELECT created_at FROM messages m WHERE m.bot_id = b.id
                     ORDER BY m.created_at DESC LIMIT 1) AS latest_at
                FROM bots b
                ORDER BY COALESCE(latest_at, b.updated_at) DESC, b.created_at ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_bot(self, bot_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
        return dict(row) if row else None

    def create_bot(
        self,
        *,
        name: str,
        title: str,
        description: str,
        color: str,
        shape: str,
    ) -> dict:
        normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
        base = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")[:60] or "new-bot"
        bot_id = base
        suffix = 2
        while self.get_bot(bot_id):
            bot_id = f"{base}-{suffix}"
            suffix += 1
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO bots (
                    id, name, title, description, color, shape, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (bot_id, name, title, description, color, shape, now, now),
            )
            self._add_message_with_db(
                db,
                bot_id=bot_id,
                role="assistant",
                author_name=name,
                content=f"I’m {name}. {description}",
                kind="welcome",
                created_at=now,
            )
        return self.get_bot(bot_id) or {}

    def get_messages(self, bot_id: str, limit: int = 200) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT * FROM (
                    SELECT * FROM messages WHERE bot_id = ?
                    ORDER BY created_at DESC LIMIT ?
                ) ORDER BY created_at ASC
                """,
                (bot_id, limit),
            ).fetchall()
        messages = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            messages.append(item)
        return messages

    def recent_teammate_context(self, bot_id: str, limit: int = 8) -> str:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT author_name, content, kind FROM messages
                WHERE bot_id = ? AND kind IN ('handoff_in', 'handoff_result')
                ORDER BY created_at DESC LIMIT ?
                """,
                (bot_id, limit),
            ).fetchall()
        if not rows:
            return ""
        ordered = reversed(rows)
        return "\n".join(f"- {row['author_name']}: {row['content']}" for row in ordered)

    def add_message(
        self,
        *,
        bot_id: str,
        role: str,
        author_name: str,
        content: str,
        kind: str = "text",
        metadata: dict | None = None,
    ) -> dict:
        with self.connect() as db:
            message_id = self._add_message_with_db(
                db,
                bot_id=bot_id,
                role=role,
                author_name=author_name,
                content=content,
                kind=kind,
                metadata=metadata,
                created_at=utc_now(),
            )
        return self.get_message(message_id) or {}

    def _add_message_with_db(
        self,
        db: sqlite3.Connection,
        *,
        bot_id: str,
        role: str,
        author_name: str,
        content: str,
        kind: str,
        created_at: str,
        metadata: dict | None = None,
    ) -> str:
        message_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO messages (
                id, bot_id, role, author_name, content, kind, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                bot_id,
                role,
                author_name,
                content,
                kind,
                json.dumps(metadata or {}),
                created_at,
            ),
        )
        db.execute("UPDATE bots SET updated_at = ? WHERE id = ?", (created_at, bot_id))
        return message_id

    def get_message(self, message_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        return item

    def set_status(self, bot_id: str, status: str, status_text: str = "") -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE bots SET status = ?, status_text = ?, updated_at = ? WHERE id = ?",
                (status, status_text, utc_now(), bot_id),
            )

    def set_thread_id(self, bot_id: str, thread_id: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE bots SET codex_thread_id = ?, updated_at = ? WHERE id = ?",
                (thread_id, utc_now(), bot_id),
            )

    def create_run(
        self,
        *,
        bot_id: str,
        prompt: str,
        trigger: str = "user",
        routine_id: str | None = None,
    ) -> dict:
        run_id = str(uuid.uuid4())
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO runs (
                    id, bot_id, routine_id, trigger, prompt, status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'queued', ?)
                """,
                (run_id, bot_id, routine_id, trigger, prompt, now),
            )
        return self.get_run(run_id) or {}

    def get_run(self, run_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_runs(self, bot_id: str | None = None, limit: int = 30) -> list[dict]:
        with self.connect() as db:
            if bot_id:
                rows = db.execute(
                    "SELECT * FROM runs WHERE bot_id = ? ORDER BY created_at DESC LIMIT ?",
                    (bot_id, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(row) for row in rows]

    def set_run_status(self, run_id: str, status: str, error: str = "") -> None:
        now = utc_now()
        started_at = now if status == "working" else None
        finished_at = now if status in {"completed", "failed", "stopped", "interrupted"} else None
        with self.connect() as db:
            db.execute(
                """
                UPDATE runs SET status = ?, error = ?,
                    started_at = COALESCE(started_at, ?),
                    finished_at = COALESCE(?, finished_at)
                WHERE id = ?
                """,
                (status, error, started_at, finished_at, run_id),
            )

    def create_routine(
        self,
        *,
        bot_id: str,
        name: str,
        prompt: str,
        schedule: dict,
        next_run_at: str | None,
    ) -> dict:
        routine_id = str(uuid.uuid4())
        now = utc_now()
        enabled = 0 if schedule.get("kind") == "manual" else 1
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO routines (
                    id, bot_id, name, prompt, schedule_json, enabled,
                    next_run_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    routine_id,
                    bot_id,
                    name,
                    prompt,
                    json.dumps(schedule),
                    enabled,
                    next_run_at,
                    now,
                    now,
                ),
            )
        return self.get_routine(routine_id) or {}

    def get_routine(self, routine_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM routines WHERE id = ?", (routine_id,)).fetchone()
        return self._routine_dict(row) if row else None

    def list_routines(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT r.*, b.name AS bot_name, b.color AS bot_color, b.shape AS bot_shape
                FROM routines r JOIN bots b ON b.id = r.bot_id
                ORDER BY r.created_at DESC
                """
            ).fetchall()
        return [self._routine_dict(row) for row in rows]

    def set_routine_enabled(
        self,
        routine_id: str,
        enabled: bool,
        next_run_at: str | None,
    ) -> dict | None:
        with self.connect() as db:
            db.execute(
                """
                UPDATE routines SET enabled = ?, next_run_at = ?, lease_until = NULL,
                    updated_at = ? WHERE id = ?
                """,
                (int(enabled), next_run_at if enabled else None, utc_now(), routine_id),
            )
        return self.get_routine(routine_id)

    def delete_routine(self, routine_id: str) -> bool:
        with self.connect() as db:
            cursor = db.execute("DELETE FROM routines WHERE id = ?", (routine_id,))
        return cursor.rowcount > 0

    def claim_due_routines(self, now: str, lease_until: str, limit: int = 5) -> list[dict]:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                """
                SELECT * FROM routines
                WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= ?
                    AND (lease_until IS NULL OR lease_until <= ?)
                ORDER BY next_run_at ASC LIMIT ?
                """,
                (now, now, limit),
            ).fetchall()
            for row in rows:
                db.execute(
                    "UPDATE routines SET lease_until = ?, updated_at = ? WHERE id = ?",
                    (lease_until, now, row["id"]),
                )
        return [self._routine_dict(row) for row in rows]

    def advance_routine(self, routine_id: str, next_run_at: str | None) -> None:
        with self.connect() as db:
            db.execute(
                """
                UPDATE routines SET next_run_at = ?, lease_until = NULL, updated_at = ?
                WHERE id = ?
                """,
                (next_run_at, utc_now(), routine_id),
            )

    def set_routine_result(self, routine_id: str, status: str) -> None:
        with self.connect() as db:
            db.execute(
                """
                UPDATE routines SET last_run_at = ?, last_status = ?, lease_until = NULL,
                    updated_at = ? WHERE id = ?
                """,
                (utc_now(), status, utc_now(), routine_id),
            )

    @staticmethod
    def _routine_dict(row: sqlite3.Row) -> dict:
        item = dict(row)
        item["schedule"] = json.loads(item.pop("schedule_json") or "{}")
        item["enabled"] = bool(item["enabled"])
        return item
