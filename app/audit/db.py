"""审计模块(设计文档 §4.7):每次问答全链路落库,支撑回放与反馈飞轮。"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    session_id TEXT,
    datasource TEXT,
    question TEXT,
    rewritten_question TEXT,
    final_sql TEXT,
    attempts TEXT,          -- JSON: [{sql, error}] 各次重试
    row_count INTEGER,
    duration_ms INTEGER,
    status TEXT,            -- success / failed / clarify / rejected / needs_confirm
    answer TEXT,
    feedback TEXT,          -- adopted / wrong / NULL
    llm_calls TEXT          -- JSON: [{model, prompt/completion/total_tokens}](设计文档 §4.7 token 消耗)
)
"""


class AuditLog:
    def __init__(self, data_dir: str):
        os.makedirs(data_dir, exist_ok=True)
        self._path = os.path.join(data_dir, "audit.db")
        self._lock = threading.Lock()
        with self._conn() as conn:
            conn.execute(_SCHEMA)
            try:    # 旧库迁移
                conn.execute("ALTER TABLE audit ADD COLUMN llm_calls TEXT")
            except sqlite3.OperationalError:
                pass

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def record(self, *, session_id: str, datasource: str, question: str,
               rewritten_question: str, final_sql: str, attempts: list[dict],
               row_count: int, duration_ms: int, status: str, answer: str,
               llm_calls: list[dict] | None = None) -> str:
        audit_id = uuid.uuid4().hex
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO audit (id, ts, session_id, datasource, question, "
                "rewritten_question, final_sql, attempts, row_count, duration_ms, "
                "status, answer, llm_calls) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (audit_id, time.time(), session_id, datasource, question,
                 rewritten_question, final_sql,
                 json.dumps(attempts, ensure_ascii=False),
                 row_count, duration_ms, status, answer,
                 json.dumps(llm_calls or [], ensure_ascii=False)),
            )
        return audit_id

    def get(self, audit_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM audit WHERE id=?", (audit_id,)).fetchone()
        return dict(row) if row else None

    def set_feedback(self, audit_id: str, feedback: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("UPDATE audit SET feedback=? WHERE id=?", (feedback, audit_id))

    def query(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM audit ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
