"""Shared SQLite unit-of-work and JSON document persistence.

SQLite is the canonical application store.  Human-readable JSON files are
commit-time projections: a projection failure can be repaired without losing
the committed product state.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any

Projection = Callable[[], None]


class ProductDatabase:
    """One physical database and one transaction boundary for product state."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._active: ContextVar[sqlite3.Connection | None] = ContextVar(
            f"mindspace_db_{id(self)}", default=None
        )
        self._projections: ContextVar[list[Projection] | None] = ContextVar(
            f"mindspace_projections_{id(self)}", default=None
        )
        self._init_lock = RLock()
        self._initialize()

    def _new_connection(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def _initialize(self) -> None:
        with self._init_lock, self._new_connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS product_documents (
                    document_key TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    data_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_product_documents_prefix
                    ON product_documents(document_key);
                CREATE TABLE IF NOT EXISTS transaction_log (
                    transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    committed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS projection_failures (
                    failure_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    error TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    resolved_at TEXT
                );
                CREATE TABLE IF NOT EXISTS entities (
                    entity_id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    canonical_value TEXT NOT NULL,
                    canonical_normalized TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    merged_into TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(scope, entity_type, canonical_normalized)
                );
                CREATE TABLE IF NOT EXISTS entity_aliases (
                    scope TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    alias_normalized TEXT NOT NULL,
                    alias_value TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'user',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(scope, entity_type, alias_normalized),
                    FOREIGN KEY(entity_id) REFERENCES entities(entity_id)
                );
                CREATE TABLE IF NOT EXISTS conversation_runs (
                    run_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    round_num INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    terminal_event TEXT NOT NULL DEFAULT '',
                    partial_text TEXT NOT NULL DEFAULT '',
                    latest_seq INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conversation_runs_status_updated
                    ON conversation_runs(status, updated_at);
                CREATE TABLE IF NOT EXISTS conversation_run_events (
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, sequence),
                    FOREIGN KEY(run_id) REFERENCES conversation_runs(run_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS prompt_inspections (
                    run_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    layers_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Reuse an active UoW, otherwise create a short atomic transaction."""

        active = self._active.get()
        if active is not None:
            yield active
            return
        db = self._new_connection()
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def transaction(
        self, *, operation: str = "application", details: dict[str, Any] | None = None
    ) -> Iterator[sqlite3.Connection]:
        """Open an outer transaction; nested users share it without early commit."""

        active = self._active.get()
        if active is not None:
            yield active
            return
        db = self._new_connection()
        active_token = self._active.set(db)
        callbacks: list[Projection] = []
        callback_token = self._projections.set(callbacks)
        transaction_id: int | None = None
        try:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "INSERT INTO transaction_log(operation, status, details_json) VALUES(?, 'open', ?)",
                (operation, json.dumps(details or {}, ensure_ascii=False, sort_keys=True)),
            )
            transaction_id = int(cursor.lastrowid)
            yield db
            db.execute(
                "UPDATE transaction_log SET status='committed', committed_at=CURRENT_TIMESTAMP "
                "WHERE transaction_id=?",
                (transaction_id,),
            )
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            self._projections.reset(callback_token)
            self._active.reset(active_token)
            db.close()
        # Projection I/O is intentionally outside the canonical transaction.
        # A later repair can recreate it from product_documents.
        for callback in callbacks:
            try:
                callback()
            except Exception as exc:  # noqa: BLE001 - canonical commit already succeeded
                self._record_projection_failure(exc)

    def defer_projection(self, callback: Projection) -> None:
        callbacks = self._projections.get()
        if callbacks is None:
            try:
                callback()
            except Exception as exc:  # noqa: BLE001 - projection is never canonical
                self._record_projection_failure(exc)
            return
        callbacks.append(callback)

    def _record_projection_failure(self, error: Exception) -> None:
        try:
            with self._new_connection() as db:
                db.execute("INSERT INTO projection_failures(error) VALUES(?)", (str(error)[:2000],))
        except sqlite3.Error:
            pass

    def begin_projection_repair(self) -> None:
        """Close prior incidents before repositories regenerate all projections."""

        with self.connection() as db:
            db.execute(
                "UPDATE projection_failures SET resolved_at=CURRENT_TIMESTAMP "
                "WHERE resolved_at IS NULL"
            )

    @staticmethod
    def _encode(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    def has_document(self, key: str) -> bool:
        with self.connection() as db:
            return (
                db.execute(
                    "SELECT 1 FROM product_documents WHERE document_key=?", (key,)
                ).fetchone()
                is not None
            )

    def get_document(self, key: str, default: Any = None) -> Any:
        with self.connection() as db:
            row = db.execute(
                "SELECT data_json FROM product_documents WHERE document_key=?", (key,)
            ).fetchone()
        return default if row is None else json.loads(row["data_json"])

    def put_document(self, key: str, value: Any) -> None:
        schema_version = (
            str(value.get("schema_version", "1.0.0")) if isinstance(value, dict) else "1.0.0"
        )
        revision = int(value.get("revision", 0)) if isinstance(value, dict) else 0
        updated_at = str(value.get("updated_at", "")) if isinstance(value, dict) else ""
        with self.connection() as db:
            db.execute(
                """
                INSERT INTO product_documents(
                    document_key, schema_version, revision, data_json, updated_at
                ) VALUES(?, ?, ?, ?, COALESCE(NULLIF(?, ''), CURRENT_TIMESTAMP))
                ON CONFLICT(document_key) DO UPDATE SET
                    schema_version=excluded.schema_version,
                    revision=excluded.revision,
                    data_json=excluded.data_json,
                    updated_at=excluded.updated_at
                """,
                (key, schema_version, revision, self._encode(value), updated_at),
            )

    def delete_document(self, key: str) -> bool:
        with self.connection() as db:
            cursor = db.execute("DELETE FROM product_documents WHERE document_key=?", (key,))
            return cursor.rowcount > 0

    def list_documents(self, prefix: str) -> list[tuple[str, Any]]:
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self.connection() as db:
            rows = db.execute(
                "SELECT document_key, data_json FROM product_documents "
                "WHERE document_key LIKE ? ESCAPE '\\' ORDER BY document_key",
                (f"{escaped}%",),
            ).fetchall()
        return [(str(row["document_key"]), json.loads(row["data_json"])) for row in rows]

    def delete_prefix(self, prefix: str) -> int:
        keys = [key for key, _value in self.list_documents(prefix)]
        with self.connection() as db:
            for key in keys:
                db.execute("DELETE FROM product_documents WHERE document_key=?", (key,))
        return len(keys)

    def integrity_check(self) -> dict[str, Any]:
        # Do not use ``connection()`` here: its own open UoW would appear as an
        # unfinished transaction in the diagnostic being measured.
        with self._new_connection() as db:
            result = str(db.execute("PRAGMA integrity_check").fetchone()[0])
            open_transactions = int(
                db.execute("SELECT COUNT(*) FROM transaction_log WHERE status='open'").fetchone()[0]
            )
            documents = int(db.execute("SELECT COUNT(*) FROM product_documents").fetchone()[0])
            projection_failures = int(
                db.execute(
                    "SELECT COUNT(*) FROM projection_failures WHERE resolved_at IS NULL"
                ).fetchone()[0]
            )
            has_role_jobs = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='role_audit_jobs'"
            ).fetchone()
            role_jobs = (
                {
                    str(row["status"]): int(row["count"])
                    for row in db.execute(
                        "SELECT status, COUNT(*) AS count FROM role_audit_jobs GROUP BY status"
                    ).fetchall()
                }
                if has_role_jobs
                else {}
            )
        return {
            "ok": result == "ok" and open_transactions == 0,
            "sqlite": result,
            "open_transactions": open_transactions,
            "documents": documents,
            "projection_failures": projection_failures,
            "role_audit_jobs": role_jobs,
        }

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def create_conversation_run(
        self, *, run_id: str, session_id: str, round_num: int
    ) -> dict[str, Any]:
        """Create one durable run identity before model execution starts."""

        now = self._now()
        with self.connection() as db:
            existing = db.execute(
                "SELECT * FROM conversation_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["session_id"]) != session_id
                    or int(existing["round_num"]) != round_num
                ):
                    raise ValueError("request id is already bound to another turn")
                return dict(existing)
            db.execute(
                """
                INSERT INTO conversation_runs(
                    run_id, session_id, round_num, status, created_at, updated_at
                ) VALUES(?, ?, ?, 'running', ?, ?)
                """,
                (run_id, session_id, round_num, now, now),
            )
        return self.get_conversation_run(run_id) or {}

    def append_conversation_run_event(
        self,
        *,
        run_id: str,
        sequence: int,
        event: str,
        payload: str,
        terminal: bool = False,
        max_events: int = 128,
    ) -> None:
        """Persist milestone SSE events while bounding replay storage per run."""

        now = self._now()
        status = {
            "run.completed": "completed",
            "run.cancelled": "cancelled",
            "run.error": "error",
            "run.interrupted": "interrupted",
        }.get(event, "running")
        with self.connection() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO conversation_run_events(
                    run_id, sequence, event, payload, created_at
                ) VALUES(?, ?, ?, ?, ?)
                """,
                (run_id, sequence, event, payload, now),
            )
            db.execute(
                """
                UPDATE conversation_runs SET
                    latest_seq=MAX(latest_seq, ?),
                    status=CASE WHEN ? THEN ? ELSE status END,
                    terminal_event=CASE WHEN ? THEN ? ELSE terminal_event END,
                    updated_at=?
                WHERE run_id=?
                """,
                (sequence, int(terminal), status, int(terminal), event, now, run_id),
            )
            count = int(
                db.execute(
                    "SELECT COUNT(*) FROM conversation_run_events WHERE run_id=?", (run_id,)
                ).fetchone()[0]
            )
            if count > max_events:
                db.execute(
                    """
                    DELETE FROM conversation_run_events
                    WHERE run_id=? AND sequence IN (
                        SELECT sequence FROM conversation_run_events
                        WHERE run_id=? ORDER BY sequence LIMIT ?
                    )
                    """,
                    (run_id, run_id, count - max_events),
                )

    def checkpoint_conversation_run(
        self, run_id: str, partial_text: str, latest_seq: int
    ) -> None:
        with self.connection() as db:
            db.execute(
                """
                UPDATE conversation_runs SET partial_text=?,
                    latest_seq=MAX(latest_seq, ?), updated_at=? WHERE run_id=?
                """,
                (partial_text, latest_seq, self._now(), run_id),
            )

    def get_conversation_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM conversation_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def conversation_run_events(self, run_id: str, after_sequence: int = 0) -> list[str]:
        with self.connection() as db:
            rows = db.execute(
                """
                SELECT payload FROM conversation_run_events
                WHERE run_id=? AND sequence>? ORDER BY sequence
                """,
                (run_id, after_sequence),
            ).fetchall()
        return [str(row["payload"]) for row in rows]

    def recover_interrupted_runs(self) -> int:
        """Close runs left active by a previous process without re-executing them."""

        now = self._now()
        recovered = 0
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM conversation_runs WHERE status='running'"
            ).fetchall()
            for row in rows:
                # A client may have consumed a few in-memory delta sequence IDs
                # after the last 500 ms checkpoint. A large recovery epoch keeps
                # the synthetic terminal event strictly newer without persisting
                # every provider token.
                sequence = int(row["latest_seq"]) + 1_000_000
                partial = str(row["partial_text"] or "")
                if partial:
                    sequence += 1
                    replacement = {
                        "version": "1.0",
                        "event": "response.replace",
                        "seq": sequence,
                        "run_id": row["run_id"],
                        "session_id": row["session_id"],
                        "round": row["round_num"],
                        "timestamp": now,
                        "data": {"content": partial, "reason": "process_recovery"},
                    }
                    payload = (
                        f"id: {sequence}\nevent: response.replace\n"
                        f"data: {json.dumps(replacement, ensure_ascii=False)}\n\n"
                    )
                    db.execute(
                        """
                        INSERT OR REPLACE INTO conversation_run_events(
                            run_id, sequence, event, payload, created_at
                        ) VALUES(?, ?, 'response.replace', ?, ?)
                        """,
                        (row["run_id"], sequence, payload, now),
                    )
                sequence += 1
                interrupted = {
                    "version": "1.0",
                    "event": "run.interrupted",
                    "seq": sequence,
                    "run_id": row["run_id"],
                    "session_id": row["session_id"],
                    "round": row["round_num"],
                    "timestamp": now,
                    "data": {"partial_text": partial, "reason": "core_restarted"},
                }
                payload = (
                    f"id: {sequence}\nevent: run.interrupted\n"
                    f"data: {json.dumps(interrupted, ensure_ascii=False)}\n\n"
                )
                db.execute(
                    """
                    INSERT OR REPLACE INTO conversation_run_events(
                        run_id, sequence, event, payload, created_at
                    ) VALUES(?, ?, 'run.interrupted', ?, ?)
                    """,
                    (row["run_id"], sequence, payload, now),
                )
                db.execute(
                    """
                    UPDATE conversation_runs SET status='interrupted',
                        terminal_event='run.interrupted', latest_seq=?, updated_at=?
                    WHERE run_id=?
                    """,
                    (sequence, now, row["run_id"]),
                )
                recovered += 1
        return recovered

    def prune_conversation_runs(self, retention_hours: int = 24) -> int:
        cutoff = (datetime.now(UTC) - timedelta(hours=retention_hours)).isoformat()
        with self.connection() as db:
            cursor = db.execute(
                "DELETE FROM conversation_runs WHERE status!='running' AND updated_at<?",
                (cutoff,),
            )
            return int(cursor.rowcount)

    def record_prompt_inspection_metadata(
        self,
        *,
        run_id: str,
        session_id: str,
        sha256: str,
        layers: list[dict[str, Any]],
    ) -> None:
        with self.connection() as db:
            db.execute(
                """
                INSERT INTO prompt_inspections(
                    run_id, session_id, sha256, layers_json, created_at
                ) VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    sha256=excluded.sha256,
                    layers_json=excluded.layers_json,
                    created_at=excluded.created_at
                """,
                (run_id, session_id, sha256, self._encode(layers), self._now()),
            )
