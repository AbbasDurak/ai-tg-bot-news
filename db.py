"""SQLite katmanı: news_items, approvals, deliveries."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from models import NewsItem, Summary

SCHEMA = """
CREATE TABLE IF NOT EXISTS news_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    content TEXT,
    source TEXT NOT NULL,
    published_at TEXT,
    title_hash TEXT NOT NULL,
    score REAL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_news_title_hash ON news_items(title_hash);
CREATE TABLE IF NOT EXISTS approvals (
    news_id INTEGER PRIMARY KEY REFERENCES news_items(id),
    status TEXT NOT NULL,           -- pending | approved | rejected
    summary_json TEXT,
    chat_id INTEGER,
    message_id INTEGER,
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE TABLE IF NOT EXISTS deliveries (
    news_id INTEGER NOT NULL REFERENCES news_items(id),
    kind TEXT NOT NULL,             -- suggestion | story_owner | publish
    created_at TEXT NOT NULL,
    PRIMARY KEY (news_id, kind)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str):
        self.path = path
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- news_items ---
    def known_urls_and_hashes(self) -> tuple[set[str], set[str]]:
        with self._conn() as c:
            rows = c.execute("SELECT canonical_url, title_hash FROM news_items").fetchall()
        return {r[0] for r in rows}, {r[1] for r in rows}

    def recent_titles(self, limit: int = 300) -> list[str]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT title FROM news_items ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [r[0] for r in rows]

    def add_news(self, item: NewsItem) -> Optional[int]:
        """Yeni kayıt ekler; kopya ise None döner."""
        with self._conn() as c:
            try:
                cur = c.execute(
                    "INSERT INTO news_items (url, canonical_url, title, content, source,"
                    " published_at, title_hash, score, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (item.url, item.canonical_url, item.title, item.content, item.source,
                     item.published_at, item.title_hash, item.score, _now()),
                )
            except sqlite3.IntegrityError:
                return None
            return cur.lastrowid

    def get_news(self, news_id: int) -> Optional[NewsItem]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM news_items WHERE id=?", (news_id,)).fetchone()
        if not r:
            return None
        return NewsItem(**{k: r[k] for k in r.keys() if k != "created_at"})

    # --- approvals ---
    def create_approval(self, news_id: int, summary: Summary) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO approvals (news_id, status, summary_json, created_at)"
                " VALUES (?, 'pending', ?, ?)",
                (news_id, summary.model_dump_json(), _now()),
            )

    def set_message(self, news_id: int, chat_id: int, message_id: int) -> None:
        with self._conn() as c:
            c.execute("UPDATE approvals SET chat_id=?, message_id=? WHERE news_id=?",
                      (chat_id, message_id, news_id))

    def get_summary(self, news_id: int) -> Optional[Summary]:
        with self._conn() as c:
            r = c.execute("SELECT summary_json FROM approvals WHERE news_id=?",
                          (news_id,)).fetchone()
        return Summary(**json.loads(r[0])) if r and r[0] else None

    def get_status(self, news_id: int) -> Optional[str]:
        with self._conn() as c:
            r = c.execute("SELECT status FROM approvals WHERE news_id=?", (news_id,)).fetchone()
        return r[0] if r else None

    def decide(self, news_id: int, status: str) -> bool:
        """Yalnızca 'pending' durumundaki kaydı karara bağlar (atomik)."""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE approvals SET status=?, decided_at=? WHERE news_id=? AND status='pending'",
                (status, _now(), news_id),
            )
            return cur.rowcount == 1

    def pending_without_delivery(self) -> list[int]:
        """Özeti hazır ama sahibine henüz gönderilmemiş bekleyen öneriler (restart sonrası)."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT a.news_id FROM approvals a WHERE a.status='pending' AND NOT EXISTS"
                " (SELECT 1 FROM deliveries d WHERE d.news_id=a.news_id AND d.kind='suggestion')"
            ).fetchall()
        return [r[0] for r in rows]

    # --- deliveries (idempotency) ---
    def claim_delivery(self, news_id: int, kind: str) -> bool:
        """True dönerse bu gönderim ilk kez yapılıyor demektir."""
        with self._conn() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO deliveries (news_id, kind, created_at) VALUES (?,?,?)",
                (news_id, kind, _now()),
            )
            return cur.rowcount == 1

    def release_delivery(self, news_id: int, kind: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM deliveries WHERE news_id=? AND kind=?", (news_id, kind))

    def reset_to_pending(self, news_id: int) -> None:
        """Üretim başarısız olduysa onayı tekrar 'pending' yapar."""
        with self._conn() as c:
            c.execute("UPDATE approvals SET status='pending', decided_at=NULL WHERE news_id=?",
                      (news_id,))
