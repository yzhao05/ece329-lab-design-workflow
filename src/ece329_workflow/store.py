from __future__ import annotations

import json
import os
import sqlite3
import time
from copy import deepcopy
from contextlib import closing
from pathlib import Path
from threading import RLock
from typing import Any, Mapping, Protocol

from .models import (
    DesignSession,
    InteractionState,
    SessionConflict,
    SessionNotFound,
    WorkflowStatus,
)


SESSION_SCHEMA_VERSION = 2


class SessionStore(Protocol):
    def get(self, design_id: str) -> DesignSession: ...

    def save(self, session: DesignSession, expected_revision: int | None = None) -> None: ...

    def delete(self, design_id: str) -> None: ...

    def healthcheck(self) -> None: ...

    def runtime_info(self) -> dict[str, Any]: ...


def _session_payload(session: DesignSession) -> str:
    return json.dumps(
        {
            "session_schema_version": SESSION_SCHEMA_VERSION,
            "design_id": session.design_id,
            "interaction_state": session.interaction_state.value,
            "access_token_hash": session.access_token_hash,
            "current_stage_index": session.current_stage_index,
            "status": session.status.value,
            "revision": session.revision,
            "completed_stages": session.completed_stages,
            "design_context": session.design_context,
            "stage_outputs": session.stage_outputs,
            "history": session.history,
            "model_context": session.model_context,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _session_from_payload(payload: str) -> DesignSession:
    data = json.loads(payload)
    schema_version = int(data.get("session_schema_version", 1))
    if schema_version not in {1, SESSION_SCHEMA_VERSION}:
        raise ValueError(f"Unsupported session schema version: {schema_version}")
    return DesignSession(
        design_id=data["design_id"],
        interaction_state=InteractionState(data["interaction_state"]),
        access_token_hash=str(data.get("access_token_hash", "")),
        current_stage_index=int(data["current_stage_index"]),
        status=WorkflowStatus(data["status"]),
        revision=int(data["revision"]),
        completed_stages=list(data.get("completed_stages", [])),
        design_context=dict(data.get("design_context", {})),
        stage_outputs=dict(data.get("stage_outputs", {})),
        history=list(data.get("history", [])),
        model_context=dict(data.get("model_context", {})),
    )


class InMemorySessionStore:
    """Thread-safe development store with optimistic revision checks."""

    def __init__(self, session_ttl_days: int = 30) -> None:
        self._items: dict[str, DesignSession] = {}
        self._updated_at: dict[str, float] = {}
        self._ttl_seconds = session_ttl_days * 86_400
        self._session_ttl_days = session_ttl_days
        self._lock = RLock()

    def get(self, design_id: str) -> DesignSession:
        with self._lock:
            self._discard_expired(time.time())
            try:
                return deepcopy(self._items[design_id])
            except KeyError as exc:
                raise SessionNotFound(f"Unknown design_id: {design_id}") from exc

    def save(self, session: DesignSession, expected_revision: int | None = None) -> None:
        with self._lock:
            existing = self._items.get(session.design_id)
            if expected_revision is not None and (
                existing is None or existing.revision != expected_revision
            ):
                raise SessionConflict(
                    "The design changed during this request; reload it before retrying."
                )
            self._items[session.design_id] = deepcopy(session)
            self._updated_at[session.design_id] = time.time()

    def delete(self, design_id: str) -> None:
        with self._lock:
            if design_id not in self._items:
                raise SessionNotFound(f"Unknown design_id: {design_id}")
            self._items.pop(design_id, None)
            self._updated_at.pop(design_id, None)

    def healthcheck(self) -> None:
        with self._lock:
            self._discard_expired(time.time())

    def _discard_expired(self, now: float) -> None:
        expired = [
            design_id
            for design_id, updated_at in self._updated_at.items()
            if now - updated_at >= self._ttl_seconds
        ]
        for design_id in expired:
            self._items.pop(design_id, None)
            self._updated_at.pop(design_id, None)

    def runtime_info(self) -> dict[str, Any]:
        return {
            "provider": "memory",
            "durable": False,
            "session_ttl_days": self._session_ttl_days,
        }


class SQLiteSessionStore:
    """Single-file persistent store suitable for one deployed service instance."""

    def __init__(self, database_path: str | Path, session_ttl_days: int = 30) -> None:
        self.path = Path(database_path).expanduser().resolve()
        self._session_ttl_days = session_ttl_days
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self._prune_expired()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS design_sessions (
                        design_id TEXT PRIMARY KEY,
                        revision INTEGER NOT NULL,
                        payload TEXT NOT NULL,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )

    def get(self, design_id: str) -> DesignSession:
        self._prune_expired()
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM design_sessions WHERE design_id = ?",
                (design_id,),
            ).fetchone()
        if row is None:
            raise SessionNotFound(f"Unknown design_id: {design_id}")
        return _session_from_payload(row[0])

    def save(self, session: DesignSession, expected_revision: int | None = None) -> None:
        self._prune_expired()
        payload = _session_payload(session)
        with closing(self._connect()) as connection:
            with connection:
                if expected_revision is None:
                    connection.execute(
                        """
                        INSERT INTO design_sessions (design_id, revision, payload)
                        VALUES (?, ?, ?)
                        ON CONFLICT(design_id) DO UPDATE SET
                            revision = excluded.revision,
                            payload = excluded.payload,
                            updated_at = CURRENT_TIMESTAMP
                        """,
                        (session.design_id, session.revision, payload),
                    )
                    return
                cursor = connection.execute(
                    """
                    UPDATE design_sessions
                    SET revision = ?, payload = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE design_id = ? AND revision = ?
                    """,
                    (session.revision, payload, session.design_id, expected_revision),
                )
                if cursor.rowcount != 1:
                    raise SessionConflict(
                        "The design changed during this request; reload it before retrying."
                    )

    def delete(self, design_id: str) -> None:
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    "DELETE FROM design_sessions WHERE design_id = ?",
                    (design_id,),
                )
                if cursor.rowcount != 1:
                    raise SessionNotFound(f"Unknown design_id: {design_id}")

    def healthcheck(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "UPDATE design_sessions SET revision = revision WHERE 0"
                )
            finally:
                connection.rollback()

    def _prune_expired(self) -> None:
        modifier = f"-{self._session_ttl_days} days"
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "DELETE FROM design_sessions WHERE updated_at < datetime('now', ?)",
                    (modifier,),
                )

    def runtime_info(self) -> dict[str, Any]:
        return {
            "provider": "sqlite",
            "durable_on_current_filesystem": True,
            "host_volume_required": True,
            "session_ttl_days": self._session_ttl_days,
        }


def store_from_environment(environ: Mapping[str, str] | None = None) -> SessionStore:
    env = os.environ if environ is None else environ
    raw_ttl_days = env.get("ECE329_SESSION_TTL_DAYS", "30").strip()
    try:
        session_ttl_days = int(raw_ttl_days)
    except ValueError as exc:
        raise ValueError("ECE329_SESSION_TTL_DAYS must be an integer") from exc
    if session_ttl_days <= 0:
        raise ValueError("ECE329_SESSION_TTL_DAYS must be greater than zero")
    database_path = env.get("ECE329_DATABASE_PATH", "").strip()
    if database_path:
        return SQLiteSessionStore(database_path, session_ttl_days=session_ttl_days)
    return InMemorySessionStore(session_ttl_days=session_ttl_days)
