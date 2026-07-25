from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Iterator

from repositories.json_repository import RepositoryError
from utils.hashing import stable_json_sha256


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
DATABASE_SCHEMA_VERSION = 2


class SqliteDesignRepository:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._migrate_database()

    def save(self, record_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        _validate_id(record_id)
        if str(payload.get("design_id") or record_id) != record_id:
            raise RepositoryError("Record ID does not match payload design_id.")
        selected = json.loads(json.dumps(payload))
        now = _now_iso()
        with self._transaction() as connection:
            current = connection.execute(
                """
                SELECT current_revision, payload_hash, payload_json
                FROM designs WHERE id = ?
                """,
                (record_id,),
            ).fetchone()
            payload_hash = _content_hash(selected)
            if current and current["payload_hash"] == payload_hash:
                return _deserialize(current["payload_json"])
            revision_number = int(current["current_revision"]) + 1 if current else 1
            revision = dict(selected.get("revision") or {})
            parent_revision_id = (
                _current_revision_id(connection, record_id) if current else None
            )
            revision_id = f"{record_id}_revision_{revision_number}"
            revision.update(
                {
                    "revision_id": revision_id,
                    "parent_revision_id": parent_revision_id,
                    "revision_number": revision_number,
                    "created_at": revision.get("created_at") or now,
                    "created_by": revision.get("created_by") or "repository",
                    "change_type": revision.get("change_type") or "update",
                    "summary": revision.get("summary")
                    or (
                        "Initial persisted revision."
                        if revision_number == 1
                        else "Persisted design update."
                    ),
                    "changes": list(revision.get("changes") or []),
                }
            )
            selected["revision"] = revision
            payload_hash = _content_hash(selected)
            serialized = _serialize(selected)
            connection.execute(
                """
                INSERT INTO designs(
                    id, name, schema_version, current_revision, payload_json,
                    payload_hash, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    schema_version = excluded.schema_version,
                    current_revision = excluded.current_revision,
                    payload_json = excluded.payload_json,
                    payload_hash = excluded.payload_hash,
                    updated_at = excluded.updated_at
                """,
                (
                    record_id,
                    str(selected.get("name") or record_id),
                    str(selected.get("schema_version") or "unknown"),
                    revision_number,
                    serialized,
                    payload_hash,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO design_revisions(
                    design_id, revision_number, revision_id, parent_revision_id,
                    payload_json, payload_hash, created_at, created_by,
                    change_summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    revision_number,
                    revision_id,
                    parent_revision_id,
                    serialized,
                    payload_hash,
                    revision["created_at"],
                    revision["created_by"],
                    revision["summary"],
                ),
            )
        return selected

    def get(self, record_id: str) -> dict[str, Any] | None:
        _validate_id(record_id)
        with self._connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version >= 2:
                row = connection.execute(
                    "SELECT payload_json, is_archived, is_deleted, is_pinned FROM designs WHERE id = ?",
                    (record_id,),
                ).fetchone()
                if row:
                    payload = _deserialize(row["payload_json"])
                    payload["is_archived"] = bool(row["is_archived"])
                    payload["is_deleted"] = bool(row["is_deleted"])
                    payload["is_pinned"] = bool(row["is_pinned"])
                    return payload
            else:
                row = connection.execute(
                    "SELECT payload_json FROM designs WHERE id = ?",
                    (record_id,),
                ).fetchone()
                if row:
                    return _deserialize(row["payload_json"])
        return None

    def list(self, show_archived: bool = False, show_deleted: bool = False) -> list[dict[str, Any]]:
        with self._connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version >= 2:
                query = "SELECT payload_json, is_archived, is_deleted, is_pinned FROM designs WHERE 1=1"
                params = []
                if not show_deleted:
                    query += " AND is_deleted = 0"
                if not show_archived:
                    query += " AND is_archived = 0"
                query += " ORDER BY updated_at DESC, id"
                rows = connection.execute(query, params).fetchall()
                results = []
                for row in rows:
                    payload = _deserialize(row["payload_json"])
                    payload["is_archived"] = bool(row["is_archived"])
                    payload["is_deleted"] = bool(row["is_deleted"])
                    payload["is_pinned"] = bool(row["is_pinned"])
                    results.append(payload)
                return results
            else:
                rows = connection.execute(
                    "SELECT payload_json FROM designs ORDER BY updated_at DESC, id"
                ).fetchall()
                return [_deserialize(row["payload_json"]) for row in rows]

    def exists(self, record_id: str) -> bool:
        _validate_id(record_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM designs WHERE id = ?",
                (record_id,),
            ).fetchone()
        return row is not None

    def list_revisions(self, record_id: str) -> list[dict[str, Any]]:
        _validate_id(record_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT revision_number, revision_id, parent_revision_id,
                       payload_hash, created_at, created_by, change_summary
                FROM design_revisions
                WHERE design_id = ?
                ORDER BY revision_number DESC
                """,
                (record_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_revision(
        self,
        record_id: str,
        revision_number: int,
    ) -> dict[str, Any] | None:
        _validate_id(record_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM design_revisions
                WHERE design_id = ? AND revision_number = ?
                """,
                (record_id, int(revision_number)),
            ).fetchone()
        return _deserialize(row["payload_json"]) if row else None

    def archive(self, record_id: str) -> None:
        _validate_id(record_id)
        with self._transaction() as connection:
            connection.execute(
                "UPDATE designs SET is_archived = 1, updated_at = ? WHERE id = ?",
                (_now_iso(), record_id),
            )

    def unarchive(self, record_id: str) -> None:
        _validate_id(record_id)
        with self._transaction() as connection:
            connection.execute(
                "UPDATE designs SET is_archived = 0, updated_at = ? WHERE id = ?",
                (_now_iso(), record_id),
            )

    def soft_delete(self, record_id: str) -> None:
        _validate_id(record_id)
        with self._transaction() as connection:
            connection.execute(
                "UPDATE designs SET is_deleted = 1, updated_at = ? WHERE id = ?",
                (_now_iso(), record_id),
            )

    def restore(self, record_id: str) -> None:
        _validate_id(record_id)
        with self._transaction() as connection:
            connection.execute(
                "UPDATE designs SET is_deleted = 0, updated_at = ? WHERE id = ?",
                (_now_iso(), record_id),
            )

    def pin(self, record_id: str) -> None:
        _validate_id(record_id)
        with self._transaction() as connection:
            connection.execute(
                "UPDATE designs SET is_pinned = 1, updated_at = ? WHERE id = ?",
                (_now_iso(), record_id),
            )

    def unpin(self, record_id: str) -> None:
        _validate_id(record_id)
        with self._transaction() as connection:
            connection.execute(
                "UPDATE designs SET is_pinned = 0, updated_at = ? WHERE id = ?",
                (_now_iso(), record_id),
            )

    def purge(self, record_id: str) -> bool:
        _validate_id(record_id)
        with self._transaction() as connection:
            cursor = connection.execute("DELETE FROM designs WHERE id = ?", (record_id,))
            return cursor.rowcount > 0

    def record_payload_migration(
        self,
        *,
        source_id: str,
        source_version: str,
        target_version: str,
        source_hash: str,
        result_hash: str | None,
        status: str,
        report: dict[str, Any],
    ) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO migration_records(
                    source_id, source_version, target_version, source_hash,
                    result_hash, status, report_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, source_hash, target_version) DO UPDATE SET
                    result_hash = excluded.result_hash,
                    status = excluded.status,
                    report_json = excluded.report_json
                """,
                (
                    source_id,
                    source_version,
                    target_version,
                    source_hash,
                    result_hash,
                    status,
                    _serialize(report),
                    _now_iso(),
                ),
            )

    @property
    def schema_version(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def _migrate_database(self) -> None:
        with self._lock, self._connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > DATABASE_SCHEMA_VERSION:
                raise RepositoryError(
                    f"Database schema {version} is newer than supported "
                    f"schema {DATABASE_SCHEMA_VERSION}."
                )
            if version < 1:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS designs(
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        schema_version TEXT NOT NULL,
                        current_revision INTEGER NOT NULL,
                        payload_json TEXT NOT NULL,
                        payload_hash TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        is_archived INTEGER DEFAULT 0,
                        is_deleted INTEGER DEFAULT 0,
                        is_pinned INTEGER DEFAULT 0
                    );
                    CREATE TABLE IF NOT EXISTS design_revisions(
                        design_id TEXT NOT NULL,
                        revision_number INTEGER NOT NULL,
                        revision_id TEXT NOT NULL,
                        parent_revision_id TEXT,
                        payload_json TEXT NOT NULL,
                        payload_hash TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        created_by TEXT NOT NULL,
                        change_summary TEXT NOT NULL,
                        PRIMARY KEY(design_id, revision_number),
                        UNIQUE(revision_id),
                        FOREIGN KEY(design_id) REFERENCES designs(id)
                            ON DELETE CASCADE
                    );
                    CREATE TABLE IF NOT EXISTS migration_records(
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source_id TEXT NOT NULL,
                        source_version TEXT NOT NULL,
                        target_version TEXT NOT NULL,
                        source_hash TEXT NOT NULL,
                        result_hash TEXT,
                        status TEXT NOT NULL,
                        report_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE(source_id, source_hash, target_version)
                    );
                    CREATE TABLE IF NOT EXISTS run_manifests(
                        run_id TEXT PRIMARY KEY,
                        manifest_json TEXT NOT NULL,
                        manifest_hash TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_designs_schema_version
                        ON designs(schema_version);
                    CREATE INDEX IF NOT EXISTS idx_revisions_design_id
                        ON design_revisions(design_id, revision_number);
                    PRAGMA user_version = 1;
                    """
                )
                version = 1
            if version < 2:
                # Migrate to version 2 (add columns)
                try:
                    connection.execute("ALTER TABLE designs ADD COLUMN is_archived INTEGER DEFAULT 0")
                except sqlite3.OperationalError:
                    pass
                try:
                    connection.execute("ALTER TABLE designs ADD COLUMN is_deleted INTEGER DEFAULT 0")
                except sqlite3.OperationalError:
                    pass
                try:
                    connection.execute("ALTER TABLE designs ADD COLUMN is_pinned INTEGER DEFAULT 0")
                except sqlite3.OperationalError:
                    pass
                connection.execute("PRAGMA user_version = 2")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
        finally:
            connection.close()


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    return _payload_hash(payload)


def _current_revision_id(
    connection: sqlite3.Connection,
    record_id: str,
) -> str | None:
    row = connection.execute(
        """
        SELECT revision_id FROM design_revisions
        WHERE design_id = ?
        ORDER BY revision_number DESC LIMIT 1
        """,
        (record_id,),
    ).fetchone()
    return str(row["revision_id"]) if row else None


def _validate_id(record_id: str) -> None:
    if not SAFE_ID.fullmatch(str(record_id or "")):
        raise RepositoryError(
            "Record ID must use only letters, numbers, underscores, and hyphens."
        )


def _serialize(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _deserialize(value: str) -> dict[str, Any]:
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise RepositoryError("Stored payload must contain one JSON object.")
    return payload


def _payload_hash(payload: dict[str, Any]) -> str:
    return stable_json_sha256(payload)


def _content_hash(payload: dict[str, Any]) -> str:
    selected = dict(payload)
    selected.pop("revision", None)
    return _payload_hash(selected)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
