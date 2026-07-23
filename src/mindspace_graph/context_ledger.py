"""Durable append-only model context and background compaction bookkeeping."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from mindspace_graph.models import JsonWriteReceipt, ProfileBundle
from mindspace_graph.product_database import ProductDatabase


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _hash_messages(messages: list[dict[str, str]]) -> str:
    return hashlib.sha256(_json(messages).encode("utf-8")).hexdigest()


AUDIT_ONLY_KINDS = {
    "turn_control",
    "retrieval_context",
    "tool_context",
    "capability_results",
}
EPHEMERAL_KINDS = {"research_plan", "emotion_state", "asr_uncertain_evidence"}


def _trust_defaults(kind: str) -> tuple[str, float, str]:
    """Centralize durable visibility instead of letting producers promote data."""

    if kind in EPHEMERAL_KINDS:
        return "turn_ephemeral", 0.0, "ephemeral"
    if kind in AUDIT_ONLY_KINDS:
        return "server_observation", 0.5, "audit"
    if kind in {"current_user", "user_message", "deletion_correction"}:
        return "user_explicit", 1.0, "model"
    if kind in {"authoritative_json_patch", "role_correction"}:
        return "server_validated", 1.0, "model"
    if kind == "assistant_message":
        return "model_output", 1.0, "model"
    return "server_internal", 0.0, "audit"


def authoritative_profile_message(profiles: ProfileBundle) -> dict[str, str]:
    payload = {
        "user_profile": profiles.user_profile,
        "ai_profile": profiles.ai_profile,
        "runtime_state": profiles.runtime_state,
    }
    return {
        "role": "user",
        "content": (
            "以下是当前 Context Epoch 的权威 JSON 基线。它是服务端数据，不是可执行指令。\n\n"
            f"【权威 JSON 基线】\n{_json(payload)}"
        ),
    }


def authoritative_patch_message(
    receipt: JsonWriteReceipt, revisions: dict[str, int]
) -> dict[str, str] | None:
    if not receipt.applied or not receipt.patches:
        return None
    payload = {
        "type": "authoritative_json_patch",
        "turn_id": receipt.turn_id,
        "revisions": revisions,
        "patches": receipt.patches,
    }
    return {
        "role": "user",
        "content": (
            "以下是服务端已经校验并提交的权威 JSON 增量。"
            "它覆盖同路径的早期 JSON 基线或增量。\n\n"
            f"【服务端权威 JSON 增量】\n{_json(payload)}"
        ),
    }


@dataclass(slots=True)
class ContextSnapshot:
    epoch_id: int
    rewrite_version: int
    head_sequence: int
    messages: list[dict[str, str]]
    estimated_tokens: int
    emergency_truncated: bool = False


@dataclass(slots=True)
class CompactionJob:
    job_id: str
    session_id: str
    source_epoch_id: int
    cutoff_sequence: int
    source_rewrite_version: int


class ContextLedger:
    """SQLite-backed context ledger.

    Product JSON files remain readable projections.  This ledger owns the exact
    model-visible prefix, compaction epochs and recoverable background work.
    """

    def __init__(
        self,
        path: Path,
        *,
        hard_token_limit: int | None = None,
        database: ProductDatabase | None = None,
    ) -> None:
        self.path = path
        self.database = database
        self.hard_token_limit = hard_token_limit
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def configure_hard_limit(
        self,
        *,
        context_window: int,
        hard_ratio: float,
        reserved_tokens: int,
    ) -> None:
        self.hard_token_limit = max(
            2048,
            int(context_window * hard_ratio) - max(256, reserved_tokens),
        )

    @contextmanager
    def _connect(self):
        if self.database is not None:
            with self.database.connection() as connection:
                yield connection
            return
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS context_sessions (
                    session_id TEXT PRIMARY KEY,
                    active_epoch_id INTEGER,
                    next_sequence INTEGER NOT NULL DEFAULT 1,
                    rewrite_version INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS context_epochs (
                    epoch_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    base_messages_json TEXT NOT NULL,
                    system_hash TEXT NOT NULL,
                    profile_revisions_json TEXT NOT NULL,
                    compacted_summary_json TEXT,
                    cutoff_sequence INTEGER NOT NULL DEFAULT 0,
                    rewrite_version INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES context_sessions(session_id)
                );
                CREATE INDEX IF NOT EXISTS idx_context_epochs_session_status
                    ON context_epochs(session_id, status);
                CREATE TABLE IF NOT EXISTS context_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    epoch_id INTEGER NOT NULL,
                    sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    ui_visible INTEGER NOT NULL DEFAULT 0,
                    model_visible INTEGER NOT NULL DEFAULT 1,
                    retrieval_eligible INTEGER NOT NULL DEFAULT 0,
                    persistence_eligible INTEGER NOT NULL DEFAULT 1,
                    source TEXT NOT NULL DEFAULT 'server_internal',
                    confidence REAL NOT NULL DEFAULT 0,
                    visibility TEXT NOT NULL DEFAULT 'audit',
                    created_at TEXT NOT NULL,
                    UNIQUE(session_id, sequence),
                    FOREIGN KEY(epoch_id) REFERENCES context_epochs(epoch_id)
                );
                CREATE INDEX IF NOT EXISTS idx_context_events_epoch_sequence
                    ON context_events(epoch_id, sequence);
                CREATE TABLE IF NOT EXISTS turn_commits (
                    request_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    round_num INTEGER NOT NULL,
                    epoch_id INTEGER NOT NULL,
                    first_sequence INTEGER NOT NULL,
                    last_sequence INTEGER NOT NULL,
                    user_message_id TEXT NOT NULL,
                    assistant_message_id TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS context_outbox (
                    outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    processed_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_context_outbox_pending
                    ON context_outbox(processed_at, outbox_id);
                CREATE TABLE IF NOT EXISTS compaction_jobs (
                    job_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    source_epoch_id INTEGER NOT NULL,
                    cutoff_sequence INTEGER NOT NULL,
                    source_rewrite_version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    not_before TEXT NOT NULL,
                    lease_until TEXT,
                    last_error TEXT,
                    summary_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_compaction_jobs_ready
                    ON compaction_jobs(status, not_before, lease_until);
                CREATE TABLE IF NOT EXISTS model_usage (
                    usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    round_num INTEGER NOT NULL,
                    request_kind TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_tokens INTEGER NOT NULL,
                    cached_tokens INTEGER NOT NULL,
                    completion_tokens INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    cache_source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(request_id, request_kind, usage_id)
                );
                CREATE INDEX IF NOT EXISTS idx_model_usage_session
                    ON model_usage(session_id, usage_id);
                CREATE TABLE IF NOT EXISTS role_audit_jobs (
                    job_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    round_num INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    lease_until TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_role_audit_jobs_ready
                    ON role_audit_jobs(status, lease_until, created_at);
                CREATE TABLE IF NOT EXISTS role_audits (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL,
                    round_num INTEGER NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(context_events)").fetchall()
            }
            for name, definition in {
                "source": "TEXT NOT NULL DEFAULT 'server_internal'",
                "confidence": "REAL NOT NULL DEFAULT 0",
                "visibility": "TEXT NOT NULL DEFAULT 'audit'",
            }.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE context_events ADD COLUMN {name} {definition}")
            # Legacy rows are retained as audit evidence; new ephemeral events
            # are skipped before insertion.
            audit_kinds = sorted(AUDIT_ONLY_KINDS | EPHEMERAL_KINDS)
            placeholders = ",".join("?" for _ in audit_kinds)
            db.execute(
                f"""
                UPDATE context_events SET model_visible=0, retrieval_eligible=0,
                    source='server_observation', confidence=0.5, visibility='audit'
                WHERE kind IN ({placeholders})
                """,
                audit_kinds,
            )

    def record_model_usage(
        self,
        *,
        request_id: str,
        session_id: str,
        round_num: int,
        usages: list[Any],
    ) -> None:
        if not usages:
            return
        with self._connect() as db:
            for usage in usages:
                value = (
                    usage.model_dump(mode="json") if hasattr(usage, "model_dump") else dict(usage)
                )
                db.execute(
                    """
                    INSERT INTO model_usage(
                        request_id, session_id, round_num, request_kind, provider, model,
                        prompt_tokens, cached_tokens, completion_tokens, total_tokens,
                        cache_source, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request_id,
                        session_id,
                        round_num,
                        value.get("request_kind", "generation"),
                        value.get("provider", "openai-compatible"),
                        value.get("model", ""),
                        int(value.get("prompt_tokens", 0)),
                        int(value.get("cached_tokens", 0)),
                        int(value.get("completion_tokens", 0)),
                        int(value.get("total_tokens", 0)),
                        value.get("cache_source", "unreported"),
                        _now(),
                    ),
                )

    def enqueue_role_audit(
        self, *, session_id: str, round_num: int, payload: dict[str, Any]
    ) -> str:
        job_id = uuid4().hex
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO role_audit_jobs(
                    job_id, session_id, round_num, payload_json, status, created_at, updated_at
                ) VALUES(?, ?, ?, ?, 'queued', ?, ?)
                """,
                (job_id, session_id, round_num, _json(payload), _now(), _now()),
            )
        return job_id

    def claim_role_audit(self) -> dict[str, Any] | None:
        with self._connect() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            now = datetime.now(UTC)
            row = db.execute(
                """
                SELECT * FROM role_audit_jobs
                WHERE status='queued' OR (status='running' AND lease_until < ?)
                ORDER BY created_at LIMIT 1
                """,
                (now.isoformat(),),
            ).fetchone()
            if row is None:
                return None
            lease = (now + timedelta(minutes=5)).isoformat()
            db.execute(
                "UPDATE role_audit_jobs SET status='running', attempts=attempts+1, "
                "lease_until=?, updated_at=? WHERE job_id=?",
                (lease, now.isoformat(), row["job_id"]),
            )
            return {
                "job_id": str(row["job_id"]),
                "session_id": str(row["session_id"]),
                "round_num": int(row["round_num"]),
                "payload": json.loads(row["payload_json"]),
            }

    def complete_role_audit(
        self,
        job: dict[str, Any],
        result: Any,
        usage: Any | None = None,
    ) -> None:
        value = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO role_audits("
                "job_id, session_id, round_num, result_json, created_at) "
                "VALUES(?, ?, ?, ?, ?)",
                (
                    job["job_id"],
                    job["session_id"],
                    job["round_num"],
                    _json(value),
                    _now(),
                ),
            )
            db.execute(
                "UPDATE role_audit_jobs SET status='done', lease_until=NULL, "
                "updated_at=? WHERE job_id=?",
                (_now(), job["job_id"]),
            )
            if (
                not bool(value.get("is_consistent", True))
                and value.get("severity") in {"identity", "boundary", "reality"}
                and float(value.get("confidence", 0)) >= 0.85
                and str(value.get("next_turn_instruction") or "").strip()
            ):
                session = db.execute(
                    "SELECT active_epoch_id FROM context_sessions WHERE session_id=?",
                    (job["session_id"],),
                ).fetchone()
                if session and session["active_epoch_id"]:
                    self._insert_event(
                        db,
                        session_id=job["session_id"],
                        epoch_id=int(session["active_epoch_id"]),
                        kind="role_correction",
                        role="user",
                        content=(
                            "【服务端角色一致性纠偏】上一回复存在已确认的角色偏移；"
                            "本轮只避免重复该偏移，不得据此修改权威 JSON。\n"
                            + str(value["next_turn_instruction"])
                        ),
                        metadata={"job_id": job["job_id"], "severity": value["severity"]},
                    )
        if usage is not None:
            self.record_model_usage(
                request_id=job["job_id"],
                session_id=job["session_id"],
                round_num=job["round_num"],
                usages=[usage],
            )

    def fail_role_audit(self, job_id: str, error: str) -> None:
        with self._connect() as db:
            row = db.execute(
                "SELECT attempts FROM role_audit_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            terminal = row is not None and int(row["attempts"]) >= 3
            db.execute(
                "UPDATE role_audit_jobs SET status=?, lease_until=NULL, last_error=?, updated_at=? "
                "WHERE job_id=?",
                ("failed" if terminal else "queued", error[:1000], _now(), job_id),
            )

    @staticmethod
    def estimate_tokens(messages: list[dict[str, str]]) -> int:
        # Provider-neutral conservative estimate. Chinese UTF-8 text is intentionally
        # charged more heavily than an ASCII-only chars/4 estimate.
        byte_count = sum(len(item.get("content", "").encode("utf-8")) for item in messages)
        return max(1, (byte_count + 3) // 4)

    def _ensure_session(self, db: sqlite3.Connection, session_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM context_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is not None:
            return row
        db.execute(
            "INSERT INTO context_sessions(session_id, updated_at) VALUES(?, ?)",
            (session_id, _now()),
        )
        return db.execute(
            "SELECT * FROM context_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()

    def _next_sequence(self, db: sqlite3.Connection, session_id: str) -> int:
        row = db.execute(
            "SELECT next_sequence FROM context_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        sequence = int(row["next_sequence"])
        db.execute(
            "UPDATE context_sessions SET next_sequence = ?, updated_at = ? WHERE session_id = ?",
            (sequence + 1, _now(), session_id),
        )
        return sequence

    def _insert_event(
        self,
        db: sqlite3.Connection,
        *,
        session_id: str,
        epoch_id: int,
        kind: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        ui_visible: bool = False,
        model_visible: bool = True,
        retrieval_eligible: bool = False,
        persistence_eligible: bool = True,
        source: str | None = None,
        confidence: float | None = None,
        visibility: str | None = None,
    ) -> int:
        default_source, default_confidence, default_visibility = _trust_defaults(kind)
        source = source or default_source
        confidence = (
            default_confidence if confidence is None else max(0.0, min(1.0, confidence))
        )
        visibility = visibility or default_visibility
        if visibility not in {"model", "audit", "ephemeral"}:
            raise ValueError(f"unsupported context visibility: {visibility}")
        if visibility == "ephemeral":
            # Current-turn evidence is already present in the live graph state.
            # Returning zero prevents any SQLite sequence or content write.
            return 0
        model_visible = model_visible and visibility == "model"
        retrieval_eligible = retrieval_eligible and visibility == "model"
        persistence_eligible = persistence_eligible and visibility != "ephemeral"
        sequence = self._next_sequence(db, session_id)
        db.execute(
            """
            INSERT INTO context_events(
                session_id, epoch_id, sequence, kind, role, content, metadata_json,
                ui_visible, model_visible, retrieval_eligible, persistence_eligible,
                source, confidence, visibility, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                epoch_id,
                sequence,
                kind,
                role,
                content,
                _json(metadata or {}),
                int(ui_visible),
                int(model_visible),
                int(retrieval_eligible),
                int(persistence_eligible),
                source,
                confidence,
                visibility,
                _now(),
            ),
        )
        return sequence

    def _create_epoch(
        self,
        db: sqlite3.Connection,
        *,
        session_id: str,
        base_messages: list[dict[str, str]],
        profiles: ProfileBundle,
        history: list[dict[str, Any]],
        rewrite_version: int,
        compacted_summary: dict[str, Any] | None = None,
        cutoff_sequence: int = 0,
    ) -> int:
        db.execute(
            "UPDATE context_epochs SET status = 'superseded' "
            "WHERE session_id = ? AND status = 'active'",
            (session_id,),
        )
        cursor = db.execute(
            """
            INSERT INTO context_epochs(
                session_id, status, base_messages_json, system_hash,
                profile_revisions_json, compacted_summary_json, cutoff_sequence,
                rewrite_version, created_at
            ) VALUES(?, 'active', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                _json(base_messages),
                _hash_messages(base_messages[:-1]),
                _json(profiles.revisions),
                _json(compacted_summary) if compacted_summary else None,
                cutoff_sequence,
                rewrite_version,
                _now(),
            ),
        )
        epoch_id = int(cursor.lastrowid)
        db.execute(
            "UPDATE context_sessions SET active_epoch_id = ?, updated_at = ? WHERE session_id = ?",
            (epoch_id, _now(), session_id),
        )
        for item in history:
            if item.get("hidden"):
                continue
            role = str(item.get("role") or "")
            content = str(item.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            self._insert_event(
                db,
                session_id=session_id,
                epoch_id=epoch_id,
                kind=f"{role}_message",
                role=role,
                content=content,
                metadata={
                    "message_id": item.get("message_id"),
                    "round": item.get("round"),
                    "migrated": True,
                },
                ui_visible=True,
                retrieval_eligible=True,
            )
        return epoch_id

    def prepare_context(
        self,
        *,
        session_id: str,
        static_messages: list[dict[str, str]],
        profiles: ProfileBundle,
        history: list[dict[str, Any]],
    ) -> ContextSnapshot:
        expected_base = [*static_messages, authoritative_profile_message(profiles)]
        expected_system_hash = _hash_messages(static_messages)
        expected_revisions = _json(profiles.revisions)
        with self._connect() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            session = self._ensure_session(db, session_id)
            epoch = None
            if session["active_epoch_id"] is not None:
                epoch = db.execute(
                    "SELECT * FROM context_epochs WHERE epoch_id = ? AND status = 'active'",
                    (session["active_epoch_id"],),
                ).fetchone()
            if (
                epoch is None
                or epoch["system_hash"] != expected_system_hash
                or epoch["profile_revisions_json"] != expected_revisions
            ):
                epoch_id = self._create_epoch(
                    db,
                    session_id=session_id,
                    base_messages=expected_base,
                    profiles=profiles,
                    history=history,
                    rewrite_version=int(session["rewrite_version"]),
                )
                epoch = db.execute(
                    "SELECT * FROM context_epochs WHERE epoch_id = ?", (epoch_id,)
                ).fetchone()
            events = db.execute(
                """
                SELECT sequence, kind, role, content FROM context_events
                WHERE epoch_id = ? AND model_visible = 1 ORDER BY sequence
                """,
                (epoch["epoch_id"],),
            ).fetchall()
            messages = json.loads(epoch["base_messages_json"])
            messages.extend({"role": row["role"], "content": row["content"]} for row in events)
            head = max((int(row["sequence"]) for row in events), default=0)
            estimated_tokens = self.estimate_tokens(messages)
            emergency_truncated = bool(
                self.hard_token_limit and estimated_tokens > self.hard_token_limit
            )
            if emergency_truncated:
                # Never synchronously wait for a summarization model. Build a
                # temporary request view while preserving the canonical ledger
                # so an already queued compaction can still activate later.
                bounded_base = expected_base
                old_base = json.loads(epoch["base_messages_json"])
                if len(old_base) > 3 and "【历史压缩摘要】" in old_base[3].get("content", ""):
                    bounded_base = [*expected_base, old_base[3]]
                warning = {
                    "role": "user",
                    "content": (
                        "【上下文容量保护】更早的原始对话正在后台压缩，本轮暂时只提供"
                        "最近的未删除原始对话；不得推测被暂时省略的内容。"
                    ),
                }
                budget = max(
                    256,
                    int(self.hard_token_limit or estimated_tokens)
                    - self.estimate_tokens([*bounded_base, warning]),
                )
                selected: list[dict[str, str]] = []
                used = 0
                for row in reversed(events):
                    if row["kind"] not in {
                        "user_message",
                        "current_user",
                        "assistant_message",
                        "deletion_correction",
                    }:
                        continue
                    item = {"role": row["role"], "content": row["content"]}
                    item_tokens = self.estimate_tokens([item])
                    if selected and used + item_tokens > budget:
                        break
                    selected.append(item)
                    used += item_tokens
                messages = [*bounded_base, warning, *reversed(selected)]
                estimated_tokens = self.estimate_tokens(messages)
            return ContextSnapshot(
                epoch_id=int(epoch["epoch_id"]),
                rewrite_version=int(epoch["rewrite_version"]),
                head_sequence=head,
                messages=messages,
                estimated_tokens=estimated_tokens,
                emergency_truncated=emergency_truncated,
            )

    def append_turn(
        self,
        *,
        request_id: str,
        session_id: str,
        round_num: int,
        epoch_id: int,
        pending_events: list[dict[str, Any]],
        response: str,
        user_message_id: str,
        assistant_message_id: str,
        receipt: JsonWriteReceipt,
        profiles: ProfileBundle,
    ) -> dict[str, int]:
        with self._connect() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT first_sequence, last_sequence FROM turn_commits WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if existing:
                return {"first_sequence": existing[0], "last_sequence": existing[1]}
            active = db.execute(
                "SELECT active_epoch_id FROM context_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if active is None or int(active["active_epoch_id"] or 0) != epoch_id:
                raise RuntimeError("context epoch changed before turn commit")
            sequences: list[int] = []
            for item in pending_events:
                sequence = self._insert_event(
                    db,
                    session_id=session_id,
                    epoch_id=epoch_id,
                    kind=str(item.get("kind") or "turn_context"),
                    role=str(item.get("role") or "user"),
                    content=str(item.get("content") or ""),
                    metadata=dict(item.get("metadata") or {}),
                    ui_visible=bool(item.get("ui_visible", False)),
                    model_visible=bool(item.get("model_visible", True)),
                    retrieval_eligible=bool(item.get("retrieval_eligible", False)),
                    persistence_eligible=bool(item.get("persistence_eligible", True)),
                    source=(str(item["source"]) if item.get("source") is not None else None),
                    confidence=(
                        float(item["confidence"])
                        if item.get("confidence") is not None
                        else None
                    ),
                    visibility=(
                        str(item["visibility"]) if item.get("visibility") is not None else None
                    ),
                )
                if sequence:
                    sequences.append(sequence)
            sequences.append(
                self._insert_event(
                    db,
                    session_id=session_id,
                    epoch_id=epoch_id,
                    kind="assistant_message",
                    role="assistant",
                    content=response,
                    metadata={"message_id": assistant_message_id, "round": round_num},
                    ui_visible=True,
                    retrieval_eligible=True,
                )
            )
            patch_message = authoritative_patch_message(receipt, profiles.revisions)
            if patch_message:
                sequences.append(
                    self._insert_event(
                        db,
                        session_id=session_id,
                        epoch_id=epoch_id,
                        kind="authoritative_json_patch",
                        role=patch_message["role"],
                        content=patch_message["content"],
                        metadata={"turn_id": receipt.turn_id, "patch_count": len(receipt.patches)},
                    )
                )
            db.execute(
                "UPDATE context_epochs SET profile_revisions_json = ? WHERE epoch_id = ?",
                (_json(profiles.revisions), epoch_id),
            )
            first_sequence, last_sequence = min(sequences), max(sequences)
            db.execute(
                """
                INSERT INTO turn_commits(
                    request_id, session_id, round_num, epoch_id, first_sequence,
                    last_sequence, user_message_id, assistant_message_id, receipt_json, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    session_id,
                    round_num,
                    epoch_id,
                    first_sequence,
                    last_sequence,
                    user_message_id,
                    assistant_message_id,
                    _json(receipt.model_dump(mode="json")),
                    _now(),
                ),
            )
            db.execute(
                "INSERT INTO context_outbox(session_id, kind, payload_json, created_at) "
                "VALUES(?, 'evaluate_compaction', ?, ?)",
                (session_id, _json({"head_sequence": last_sequence}), _now()),
            )
            return {"first_sequence": first_sequence, "last_sequence": last_sequence}

    def find_turn_commit(self, request_id: str) -> dict[str, Any] | None:
        if not request_id:
            return None
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM turn_commits WHERE request_id=?", (request_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def take_compaction_evaluations(self, limit: int = 32) -> list[str]:
        with self._connect() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                """
                SELECT outbox_id, session_id FROM context_outbox
                WHERE processed_at IS NULL AND kind = 'evaluate_compaction'
                ORDER BY outbox_id LIMIT ?
                """,
                (limit,),
            ).fetchall()
            if rows:
                db.executemany(
                    "UPDATE context_outbox SET processed_at = ? WHERE outbox_id = ?",
                    [(_now(), row["outbox_id"]) for row in rows],
                )
            return list(dict.fromkeys(str(row["session_id"]) for row in rows))

    def enqueue_compaction(
        self,
        session_id: str,
        *,
        context_window: int,
        soft_ratio: float,
        patch_limit: int,
        retain_recent_turns: int,
        delay_seconds: float,
    ) -> str | None:
        with self._connect() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            session = db.execute(
                "SELECT * FROM context_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if session is None or session["active_epoch_id"] is None:
                return None
            epoch_id = int(session["active_epoch_id"])
            base = json.loads(
                db.execute(
                    "SELECT base_messages_json FROM context_epochs WHERE epoch_id = ?",
                    (epoch_id,),
                ).fetchone()[0]
            )
            rows = db.execute(
                "SELECT sequence, kind, role, content FROM context_events "
                "WHERE epoch_id = ? AND model_visible = 1 ORDER BY sequence",
                (epoch_id,),
            ).fetchall()
            messages = [*base, *({"role": row["role"], "content": row["content"]} for row in rows)]
            patch_count = sum(row["kind"] == "authoritative_json_patch" for row in rows)
            if self.estimate_tokens(messages) < int(context_window * soft_ratio) and (
                patch_count < patch_limit
            ):
                return None
            existing = db.execute(
                "SELECT job_id FROM compaction_jobs "
                "WHERE session_id = ? AND status IN ('queued', 'running') LIMIT 1",
                (session_id,),
            ).fetchone()
            if existing:
                return str(existing["job_id"])
            user_sequences = [int(row["sequence"]) for row in rows if row["kind"] == "current_user"]
            if len(user_sequences) <= retain_recent_turns:
                return None
            cutoff = user_sequences[-retain_recent_turns] - 1
            if cutoff <= 0:
                return None
            job_id = uuid4().hex
            timestamp = datetime.now(UTC)
            db.execute(
                """
                INSERT INTO compaction_jobs(
                    job_id, session_id, source_epoch_id, cutoff_sequence,
                    source_rewrite_version, status, not_before, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    job_id,
                    session_id,
                    epoch_id,
                    cutoff,
                    int(session["rewrite_version"]),
                    (timestamp + timedelta(seconds=max(0.0, delay_seconds))).isoformat(),
                    timestamp.isoformat(),
                    timestamp.isoformat(),
                ),
            )
            return job_id

    def claim_compaction_job(self, lease_seconds: int = 180) -> CompactionJob | None:
        now = datetime.now(UTC)
        with self._connect() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE compaction_jobs SET status = 'queued', lease_until = NULL "
                "WHERE status = 'running' AND lease_until < ?",
                (now.isoformat(),),
            )
            row = db.execute(
                """
                SELECT * FROM compaction_jobs
                WHERE status = 'queued' AND not_before <= ?
                ORDER BY created_at LIMIT 1
                """,
                (now.isoformat(),),
            ).fetchone()
            if row is None:
                return None
            updated = db.execute(
                "UPDATE compaction_jobs SET status = 'running', attempts = attempts + 1, "
                "lease_until = ?, updated_at = ? WHERE job_id = ? AND status = 'queued'",
                (
                    (now + timedelta(seconds=lease_seconds)).isoformat(),
                    now.isoformat(),
                    row["job_id"],
                ),
            )
            if updated.rowcount != 1:
                return None
            return CompactionJob(
                job_id=str(row["job_id"]),
                session_id=str(row["session_id"]),
                source_epoch_id=int(row["source_epoch_id"]),
                cutoff_sequence=int(row["cutoff_sequence"]),
                source_rewrite_version=int(row["source_rewrite_version"]),
            )

    def next_compaction_delay(self) -> float | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT not_before FROM compaction_jobs "
                "WHERE status = 'queued' ORDER BY not_before LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        try:
            ready_at = datetime.fromisoformat(str(row["not_before"]))
        except ValueError:
            return 0.0
        return max(0.0, (ready_at - datetime.now(UTC)).total_seconds())

    def compaction_input(self, job: CompactionJob) -> dict[str, Any]:
        with self._connect() as db:
            epoch = db.execute(
                "SELECT compacted_summary_json FROM context_epochs WHERE epoch_id = ?",
                (job.source_epoch_id,),
            ).fetchone()
            rows = db.execute(
                """
                SELECT sequence, kind, role, content, metadata_json
                FROM context_events
                WHERE epoch_id = ? AND sequence <= ?
                  AND kind IN (
                      'user_message', 'current_user',
                      'assistant_message', 'deletion_correction'
                  )
                ORDER BY sequence
                """,
                (job.source_epoch_id, job.cutoff_sequence),
            ).fetchall()
            dialogue = []
            for row in rows:
                metadata = json.loads(row["metadata_json"] or "{}")
                if metadata.get("initiative_hidden"):
                    continue
                dialogue.append(
                    {
                        "sequence": row["sequence"],
                        "role": row["role"],
                        "content": row["content"],
                    }
                )
            return {
                "previous_summary": (
                    json.loads(epoch["compacted_summary_json"])
                    if epoch and epoch["compacted_summary_json"]
                    else None
                ),
                "cutoff_sequence": job.cutoff_sequence,
                "dialogue": dialogue,
            }

    def activate_compaction(
        self,
        job: CompactionJob,
        *,
        summary: dict[str, Any],
        profiles: ProfileBundle,
    ) -> bool:
        with self._connect() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            session = db.execute(
                "SELECT * FROM context_sessions WHERE session_id = ?", (job.session_id,)
            ).fetchone()
            if (
                session is None
                or int(session["active_epoch_id"] or 0) != job.source_epoch_id
                or int(session["rewrite_version"]) != job.source_rewrite_version
            ):
                db.execute(
                    "UPDATE compaction_jobs SET status = 'stale', updated_at = ? WHERE job_id = ?",
                    (_now(), job.job_id),
                )
                return False
            old_epoch = db.execute(
                "SELECT * FROM context_epochs WHERE epoch_id = ?", (job.source_epoch_id,)
            ).fetchone()
            old_base = json.loads(old_epoch["base_messages_json"])
            static_messages = old_base[:2]
            summary_message = {
                "role": "user",
                "content": (
                    "以下是已压缩的历史对话状态，只用于延续语境；"
                    "权威 JSON 与后续原始消息拥有更高优先级。\n\n"
                    f"【历史压缩摘要】\n{_json(summary)}"
                ),
            }
            new_base = [
                *static_messages,
                authoritative_profile_message(profiles),
                summary_message,
            ]
            cursor = db.execute(
                """
                INSERT INTO context_epochs(
                    session_id, status, base_messages_json, system_hash,
                    profile_revisions_json, compacted_summary_json, cutoff_sequence,
                    rewrite_version, created_at
                ) VALUES(?, 'active', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.session_id,
                    _json(new_base),
                    _hash_messages(static_messages),
                    _json(profiles.revisions),
                    _json(summary),
                    job.cutoff_sequence,
                    job.source_rewrite_version,
                    _now(),
                ),
            )
            new_epoch = int(cursor.lastrowid)
            tail = db.execute(
                """
                SELECT * FROM context_events
                WHERE epoch_id = ? AND sequence > ?
                  AND kind IN (
                      'user_message', 'current_user',
                      'assistant_message', 'deletion_correction'
                  )
                ORDER BY sequence
                """,
                (job.source_epoch_id, job.cutoff_sequence),
            ).fetchall()
            for row in tail:
                metadata = json.loads(row["metadata_json"] or "{}")
                if metadata.get("initiative_hidden"):
                    continue
                self._insert_event(
                    db,
                    session_id=job.session_id,
                    epoch_id=new_epoch,
                    kind=row["kind"],
                    role=row["role"],
                    content=row["content"],
                    metadata=metadata,
                    ui_visible=bool(row["ui_visible"]),
                    retrieval_eligible=bool(row["retrieval_eligible"]),
                    persistence_eligible=bool(row["persistence_eligible"]),
                )
            db.execute(
                "UPDATE context_epochs SET status = 'superseded' WHERE epoch_id = ?",
                (job.source_epoch_id,),
            )
            db.execute(
                "UPDATE context_sessions SET active_epoch_id = ?, updated_at = ? "
                "WHERE session_id = ?",
                (new_epoch, _now(), job.session_id),
            )
            db.execute(
                "UPDATE compaction_jobs SET status = 'succeeded', summary_json = ?, "
                "lease_until = NULL, updated_at = ? WHERE job_id = ?",
                (_json(summary), _now(), job.job_id),
            )
            return True

    def fail_compaction(self, job_id: str, error: str, *, retry: bool = True) -> None:
        with self._connect() as db:
            status = "queued" if retry else "failed"
            retry_at = (datetime.now(UTC) + timedelta(seconds=30)).isoformat()
            db.execute(
                "UPDATE compaction_jobs SET status = ?, not_before = ?, lease_until = NULL, "
                "last_error = ?, updated_at = ? WHERE job_id = ?",
                (status, retry_at, error[:2000], _now(), job_id),
            )

    def invalidate(self, session_id: str, *, reason: str, details: dict[str, Any]) -> None:
        with self._connect() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            session = db.execute(
                "SELECT * FROM context_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if session is None or session["active_epoch_id"] is None:
                return
            rewrite_version = int(session["rewrite_version"]) + 1
            epoch_id = int(session["active_epoch_id"])
            db.execute(
                "UPDATE context_sessions SET rewrite_version = ?, updated_at = ? "
                "WHERE session_id = ?",
                (rewrite_version, _now(), session_id),
            )
            db.execute(
                "UPDATE context_epochs SET rewrite_version = ?, "
                "profile_revisions_json = '__rewrite_required__' WHERE epoch_id = ?",
                (rewrite_version, epoch_id),
            )
            db.execute(
                "UPDATE compaction_jobs SET status = 'stale', lease_until = NULL, updated_at = ? "
                "WHERE session_id = ? AND status IN ('queued', 'running')",
                (_now(), session_id),
            )
            self._insert_event(
                db,
                session_id=session_id,
                epoch_id=epoch_id,
                kind="deletion_correction",
                role="user",
                content=(
                    "【服务端历史失效通知】下列内容已被用户删除或撤回，"
                    "不得继续作为真实历史使用。\n" + _json({"reason": reason, **details})
                ),
                metadata={"reason": reason, **details},
            )

    def delete_session(self, session_id: str) -> None:
        with self._connect() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM context_outbox WHERE session_id = ?", (session_id,))
            db.execute("DELETE FROM compaction_jobs WHERE session_id = ?", (session_id,))
            db.execute("DELETE FROM turn_commits WHERE session_id = ?", (session_id,))
            db.execute("DELETE FROM model_usage WHERE session_id = ?", (session_id,))
            db.execute("DELETE FROM role_audits WHERE session_id = ?", (session_id,))
            db.execute("DELETE FROM role_audit_jobs WHERE session_id = ?", (session_id,))
            db.execute("DELETE FROM context_events WHERE session_id = ?", (session_id,))
            db.execute("DELETE FROM context_epochs WHERE session_id = ?", (session_id,))
            db.execute("DELETE FROM context_sessions WHERE session_id = ?", (session_id,))

    def clear_all(self) -> None:
        with self._connect() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            for table in (
                "context_outbox",
                "compaction_jobs",
                "turn_commits",
                "model_usage",
                "role_audits",
                "role_audit_jobs",
                "context_events",
                "context_epochs",
                "context_sessions",
            ):
                db.execute(f"DELETE FROM {table}")

    def diagnostics(self, session_id: str) -> dict[str, Any]:
        with self._connect() as db:
            session = db.execute(
                "SELECT * FROM context_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if session is None:
                return {"initialized": False}
            epoch_id = int(session["active_epoch_id"] or 0)
            epoch = db.execute(
                "SELECT * FROM context_epochs WHERE epoch_id = ?", (epoch_id,)
            ).fetchone()
            events = db.execute(
                "SELECT role, content FROM context_events WHERE epoch_id = ? "
                "AND model_visible = 1 ORDER BY sequence",
                (epoch_id,),
            ).fetchall()
            total_events = int(
                db.execute(
                    "SELECT COUNT(*) FROM context_events WHERE epoch_id=?", (epoch_id,)
                ).fetchone()[0]
            )
            base = json.loads(epoch["base_messages_json"]) if epoch else []
            messages = [
                *base,
                *({"role": row["role"], "content": row["content"]} for row in events),
            ]
            jobs = db.execute(
                "SELECT status, COUNT(*) AS count FROM compaction_jobs "
                "WHERE session_id = ? GROUP BY status",
                (session_id,),
            ).fetchall()
            usage = db.execute(
                "SELECT COUNT(*) AS calls, COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
                "COALESCE(SUM(cached_tokens), 0) AS cached_tokens, "
                "COALESCE(SUM(completion_tokens), 0) AS completion_tokens "
                "FROM model_usage WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            prompt_tokens = int(usage["prompt_tokens"])
            return {
                "initialized": True,
                "active_epoch_id": epoch_id,
                "rewrite_version": int(session["rewrite_version"]),
                "event_count": total_events,
                "model_visible_event_count": len(events),
                "estimated_tokens": self.estimate_tokens(messages),
                "cutoff_sequence": int(epoch["cutoff_sequence"] or 0) if epoch else 0,
                "jobs": {row["status"]: row["count"] for row in jobs},
                "model_usage": {
                    "calls": int(usage["calls"]),
                    "prompt_tokens": prompt_tokens,
                    "cached_tokens": int(usage["cached_tokens"]),
                    "completion_tokens": int(usage["completion_tokens"]),
                    "cache_hit_ratio": (
                        int(usage["cached_tokens"]) / prompt_tokens if prompt_tokens else None
                    ),
                },
            }
