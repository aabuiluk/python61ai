"""Сховище на SQLite (стандартна бібліотека): питання, оцінки, відповіді людини, налаштування.

Таблиці:
- questions  — кожне питання до бота: режим відповіді, джерела, оцінка 👍/👎,
               чи розглянуто адміністратором;
- overrides  — відповіді, які написала людина; їх бот віддає, коли те саме
               питання ставлять знову (human-in-the-loop);
- settings   — зміни налаштувань з адмінки (ключ → значення JSON).

Файл БД лежить у RAG_DATA_DIR, тобто поза репозиторієм і поза деплоєм.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    question TEXT NOT NULL,
    norm TEXT NOT NULL,
    mode TEXT NOT NULL,
    answer TEXT,
    source_ids TEXT,
    llm_error TEXT,
    latency_ms INTEGER,
    feedback INTEGER,            -- 1 = корисно, -1 = не допомогло, NULL = без оцінки
    reviewed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_questions_created ON questions(created_at);
CREATE INDEX IF NOT EXISTS idx_questions_norm ON questions(norm);

CREATE TABLE IF NOT EXISTS overrides (
    norm TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    from_question_id INTEGER
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)


def normalize_question(text: str) -> str:
    """Нормалізує питання для порівняння: нижній регістр, без пунктуації й зайвих пробілів."""
    lowered = _PUNCT_RE.sub(" ", text.lower())
    return re.sub(r"\s+", " ", lowered).strip()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # --- питання -----------------------------------------------------------

    def log_question(self, question: str, mode: str, answer: str,
                     source_ids: List[str], llm_error: Optional[str],
                     latency_ms: int) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO questions (created_at, question, norm, mode, answer,"
                " source_ids, llm_error, latency_ms) VALUES (?,?,?,?,?,?,?,?)",
                (utc_now(), question, normalize_question(question), mode, answer,
                 json.dumps(source_ids), llm_error, latency_ms),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def set_feedback(self, question_id: int, value: int) -> bool:
        if value not in (1, -1):
            raise ValueError("feedback має бути 1 або -1")
        with self._lock:
            cur = self._conn.execute("UPDATE questions SET feedback=? WHERE id=?",
                                     (value, question_id))
            self._conn.commit()
            return cur.rowcount > 0

    def get_question(self, question_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM questions WHERE id=?",
                                     (question_id,)).fetchone()
        return dict(row) if row else None

    def list_questions(self, limit: int = 100, only_review: bool = False) -> List[Dict[str, Any]]:
        """Останні питання. only_review — лише ті, що потребують уваги людини."""
        where = ("WHERE reviewed=0 AND (mode='no_match' OR feedback=-1)"
                 if only_review else "")
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM questions {where} ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_reviewed(self, question_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE questions SET reviewed=1 WHERE id=?", (question_id,))
            self._conn.commit()

    def stats(self, days: int = 14) -> Dict[str, Any]:
        """Зведена статистика для дашборду адмінки."""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
            by_mode = {r[0]: r[1] for r in self._conn.execute(
                "SELECT mode, COUNT(*) FROM questions GROUP BY mode")}
            likes = self._conn.execute(
                "SELECT SUM(feedback=1), SUM(feedback=-1) FROM questions").fetchone()
            pending = self._conn.execute(
                "SELECT COUNT(*) FROM questions WHERE reviewed=0 AND (mode='no_match' OR feedback=-1)"
            ).fetchone()[0]
            per_day = {r[0]: r[1] for r in self._conn.execute(
                "SELECT substr(created_at,1,10), COUNT(*) FROM questions"
                " WHERE created_at >= ? GROUP BY substr(created_at,1,10)", (since,))}
            top = [dict(r) for r in self._conn.execute(
                "SELECT norm AS question, COUNT(*) AS count FROM questions"
                " GROUP BY norm ORDER BY count DESC LIMIT 10")]
            avg_latency = self._conn.execute(
                "SELECT AVG(latency_ms) FROM questions WHERE created_at >= ?", (since,)
            ).fetchone()[0]
        return {
            "total": total,
            "by_mode": by_mode,
            "useful": likes[0] or 0,
            "not_useful": likes[1] or 0,
            "pending_review": pending,
            "per_day": per_day,
            "top_questions": top,
            "avg_latency_ms": round(avg_latency or 0),
            "days": days,
        }

    # --- відповіді людини --------------------------------------------------

    def find_override(self, question: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM overrides WHERE norm=?",
                                     (normalize_question(question),)).fetchone()
        return dict(row) if row else None

    def list_overrides(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM overrides ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]

    def save_override(self, question: str, answer: str,
                      from_question_id: Optional[int] = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO overrides (norm, question, answer, updated_at, from_question_id)"
                " VALUES (?,?,?,?,?) ON CONFLICT(norm) DO UPDATE SET"
                " question=excluded.question, answer=excluded.answer,"
                " updated_at=excluded.updated_at, from_question_id=excluded.from_question_id",
                (normalize_question(question), question, answer, utc_now(), from_question_id),
            )
            self._conn.commit()

    def delete_override(self, norm: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM overrides WHERE norm=?", (norm,))
            self._conn.commit()
            return cur.rowcount > 0

    # --- налаштування ------------------------------------------------------

    def get_settings(self) -> Dict[str, Any]:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: json.loads(r["value"]) for r in rows}

    def set_setting(self, key: str, value: Any) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
            self._conn.commit()
