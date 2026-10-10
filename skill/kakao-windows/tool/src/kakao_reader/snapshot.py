"""Read-only, in-memory database snapshots with committed WAL replay.

Related upstream: https://github.com/is-theo/kakao-cli-win (MIT),
Copyright (c) 2026 theo; see THIRD_PARTY_NOTICES.md. Snapshot/HMAC safeguards were added here.
File stability checks are best effort, not a SQLite lock or cross-file transaction.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
import sqlite3
import time

from .crypto import PAGE_SIZE, ZERO_PAGE_POLICY, decrypt_database
from .wal import apply_committed_wal

MAX_SNAPSHOT_BYTES = 1024 * 1024 * 1024
MAX_WAL_BYTES = 2 * MAX_SNAPSHOT_BYTES


class SnapshotError(ValueError):
    """No stable, valid local database snapshot could be obtained."""


def _stamp(path: Path):
    try:
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns, stat.st_ino
    except FileNotFoundError:
        return None


def _bounded_read(path: Path, limit: int) -> bytes:
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise SnapshotError("snapshot_size_limit_exceeded")
    return data


def _stable_read(path: Path, attempts: int = 4):
    wal_path = Path(str(path) + "-wal")
    journal_path = Path(str(path) + "-journal")
    for attempt in range(1, attempts + 1):
        before = tuple(_stamp(p) for p in (path, wal_path, journal_path))
        if before[0] is None:
            raise FileNotFoundError("database_file_not_found")
        try:
            data = _bounded_read(path, MAX_SNAPSHOT_BYTES)
            wal = _bounded_read(wal_path, MAX_WAL_BYTES) if before[1] else b""
            journal = b""
            if before[2] and before[2][0]:
                with journal_path.open("rb") as handle:
                    journal = handle.read(28)
        except FileNotFoundError:
            # A concurrent checkpoint can remove/recreate the WAL or journal.
            if attempt == attempts:
                raise SnapshotError("files_changed_during_snapshot") from None
            time.sleep(0.1)
            continue
        after = tuple(_stamp(p) for p in (path, wal_path, journal_path))
        if before == after:
            if journal[:8] == bytes.fromhex("d9d505f920a163d7"):
                raise SnapshotError("active_rollback_journal")
            return data, wal, attempt
        if attempt != attempts:
            time.sleep(0.1)
    raise SnapshotError("database_changing_during_snapshot")


def read_database(path: Path, key: bytes) -> tuple[sqlite3.Connection, dict]:
    """Return a query-only in-memory SQLite connection and non-sensitive checks.

    The caller owns and must close the connection. No plaintext database is
    written to disk. Missing or unauthenticated pages, including zero pages,
    cause failure. Invalid WAL tails retain the last valid commit and remain
    visible in diagnostics; this does not establish history completeness.
    """
    data, wal, attempts = _stable_read(Path(path))
    merged, wal_diag = apply_committed_wal(data, wal)
    plaintext = bytearray(decrypt_database(merged, key))
    # An in-memory deserialized DB cannot attach the source WAL file.
    plaintext[18:20] = b"\x01\x01"
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(plaintext)
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise SnapshotError("sqlite_quick_check_failed")
        connection.text_factory = bytes
        diagnostics = {
            "hmac_verified": True,
            "authenticated_pages": len(merged) // PAGE_SIZE,
            "zero_page_policy": ZERO_PAGE_POLICY,
            "sqlite_quick_check": "ok",
            "wal": wal_diag,
            "stable_read_attempts": attempts,
            "read_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "snapshot_consistency": "best_effort_file_stability_not_global_transaction",
            "history_complete": False,
        }
        return connection, diagnostics
    except Exception:
        connection.close()
        raise
