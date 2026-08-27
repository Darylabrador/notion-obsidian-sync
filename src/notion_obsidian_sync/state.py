"""Local sync state, backed by SQLite for reliability and scale (thousands of
pages). Tracks enough per page to decide SKIP/CREATE/UPDATE without ever
re-fetching content that hasn't changed, and to detect local edits made
directly in Obsidian (conflict protection).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    notion_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    file_path TEXT NOT NULL,
    parent_id TEXT,
    notion_url TEXT,
    notion_last_edited_time TEXT NOT NULL,
    last_synced_at TEXT NOT NULL,
    content_checksum TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pages_file_path ON pages(file_path);
"""


@dataclass
class PageRecord:
    notion_id: str
    title: str
    file_path: str  # POSIX-style, relative to the sync root
    parent_id: str | None
    notion_url: str | None
    notion_last_edited_time: str
    last_synced_at: str
    content_checksum: str


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class StateStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            yield
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise

    def get(self, notion_id: str) -> PageRecord | None:
        row = self._conn.execute(
            "SELECT * FROM pages WHERE notion_id = ?", (notion_id,)
        ).fetchone()
        return _row_to_record(row) if row else None

    def get_by_file_path(self, file_path: str) -> PageRecord | None:
        row = self._conn.execute(
            "SELECT * FROM pages WHERE file_path = ?", (file_path,)
        ).fetchone()
        return _row_to_record(row) if row else None

    def upsert(self, record: PageRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO pages (
                notion_id, title, file_path, parent_id, notion_url,
                notion_last_edited_time, last_synced_at, content_checksum
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(notion_id) DO UPDATE SET
                title=excluded.title,
                file_path=excluded.file_path,
                parent_id=excluded.parent_id,
                notion_url=excluded.notion_url,
                notion_last_edited_time=excluded.notion_last_edited_time,
                last_synced_at=excluded.last_synced_at,
                content_checksum=excluded.content_checksum
            """,
            (
                record.notion_id,
                record.title,
                record.file_path,
                record.parent_id,
                record.notion_url,
                record.notion_last_edited_time,
                record.last_synced_at,
                record.content_checksum,
            ),
        )
        self._conn.commit()

    def delete(self, notion_id: str) -> None:
        self._conn.execute("DELETE FROM pages WHERE notion_id = ?", (notion_id,))
        self._conn.commit()

    def all_ids(self) -> set[str]:
        rows = self._conn.execute("SELECT notion_id FROM pages").fetchall()
        return {r["notion_id"] for r in rows}

    def all_records(self) -> list[PageRecord]:
        rows = self._conn.execute("SELECT * FROM pages ORDER BY file_path").fetchall()
        return [_row_to_record(r) for r in rows]

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) AS c FROM pages").fetchone()["c"]


def _row_to_record(row: sqlite3.Row) -> PageRecord:
    return PageRecord(
        notion_id=row["notion_id"],
        title=row["title"],
        file_path=row["file_path"],
        parent_id=row["parent_id"],
        notion_url=row["notion_url"],
        notion_last_edited_time=row["notion_last_edited_time"],
        last_synced_at=row["last_synced_at"],
        content_checksum=row["content_checksum"],
    )
