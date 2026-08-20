"""Small SQLite persistence layer for Bots, conversations, and handoffs."""

from __future__ import annotations

import json
import sqlite3
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
        base = "-".join(name.lower().strip().split()) or "new-bot"
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
