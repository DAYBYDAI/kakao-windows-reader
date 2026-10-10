# Related upstream: https://github.com/is-theo/kakao-cli-win (MIT),
# Copyright (c) 2026 theo; see NOTICE. WAL replay is based on SQLite fileformat2.
"""Merge committed SQLite WAL pages without decrypting or modifying source files.

Reference: https://www.sqlite.org/fileformat2.html#walformat
The WAL checksum authenticates storage framing, not encrypted page contents;
the caller must subsequently validate SQLCipher HMACs and SQLite integrity.
"""
from __future__ import annotations

import struct

MAX_SNAPSHOT_BYTES = 1024 * 1024 * 1024


class WalFormatError(ValueError):
    """The WAL header or resulting snapshot cannot be interpreted safely."""


def _checksum(data: bytes, byteorder: str, state: tuple[int, int] = (0, 0)) -> tuple[int, int]:
    if len(data) % 8:
        raise WalFormatError("Checksum input must be aligned to eight bytes")
    word = struct.Struct("<II" if byteorder == "little" else ">II")
    first, second = state
    for left, right in word.iter_unpack(data):
        first = (first + left + second) & 0xFFFFFFFF
        second = (second + right + first) & 0xFFFFFFFF
    return first, second


def apply_committed_wal(db_bytes: bytes, wal_bytes: bytes) -> tuple[bytes, dict]:
    """Overlay only frames through the final valid commit onto a main DB snapshot.

    Inputs are snapshots already obtained by the caller. This function does no
    I/O and cannot establish that the two inputs were captured atomically. A
    malformed WAL header raises WalFormatError. An obsolete, corrupt, or partial
    frame tail terminates scanning; an earlier valid commit is still usable.
    An empty WAL or a WAL without a valid commit leaves the main bytes unchanged.
    The resulting snapshot is capped at 1 GiB to reject unreasonable allocations.
    """
    db_bytes = bytes(db_bytes)
    wal_bytes = bytes(wal_bytes)
    info = {
        "wal_present": bool(wal_bytes), "wal_bytes": len(wal_bytes),
        "main_bytes": len(db_bytes), "page_size": None,
        "valid_frames": 0, "committed_frames": 0, "commit_count": 0,
        "last_commit_frame": None, "committed_database_pages": None,
        "unique_pages_applied": 0, "valid_uncommitted_frames_ignored": 0,
        "tail_reason": None, "tail_bytes_ignored": 0, "applied": False,
        "output_bytes": len(db_bytes),
    }
    if not wal_bytes:
        return db_bytes, info
    if len(wal_bytes) < 32:
        raise WalFormatError("Truncated WAL header")
    magic, version, page_size, _sequence, _salt1, _salt2, want1, want2 = struct.unpack(">8I", wal_bytes[:32])
    if magic not in (0x377F0682, 0x377F0683):
        raise WalFormatError("Unrecognized WAL magic")
    if version != 3007000:
        raise WalFormatError("Unsupported WAL format version")
    if not 512 <= page_size <= 65536 or page_size & (page_size - 1):
        raise WalFormatError("Invalid WAL page size")
    if len(db_bytes) % page_size:
        raise WalFormatError("Main snapshot is not aligned to WAL page size")
    byteorder = "little" if magic == 0x377F0682 else "big"
    checksum = _checksum(wal_bytes[:24], byteorder)
    if checksum != (want1, want2):
        raise WalFormatError("WAL header checksum mismatch")
    info.update(page_size=page_size, checksum_byteorder=byteorder)
    salt = wal_bytes[16:24]
    frame_size = 24 + page_size
    frames: list[tuple[int, int]] = []  # (database page number, WAL payload offset)
    offset = 32
    while offset + frame_size <= len(wal_bytes):
        header = wal_bytes[offset:offset + 24]
        pgno, committed_size, _s1, _s2, want1, want2 = struct.unpack(">6I", header)
        if header[8:16] != salt:
            info["tail_reason"] = "salt_mismatch"
            break
        if pgno == 0 or pgno == 0xFFFFFFFF:
            info["tail_reason"] = "invalid_page_number"
            break
        payload_offset = offset + 24
        payload = wal_bytes[payload_offset:payload_offset + page_size]
        proposed = _checksum(header[:8] + payload, byteorder, checksum)
        if proposed != (want1, want2):
            info["tail_reason"] = "frame_checksum_mismatch"
            break
        checksum = proposed
        frames.append((pgno, payload_offset))
        offset += frame_size
        if committed_size:
            if committed_size * page_size > MAX_SNAPSHOT_BYTES:
                raise WalFormatError("Committed snapshot exceeds 1 GiB limit")
            info["commit_count"] += 1
            info["last_commit_frame"] = len(frames)
            info["committed_frames"] = len(frames)
            info["committed_database_pages"] = committed_size
    if offset < len(wal_bytes) and info["tail_reason"] is None:
        info["tail_reason"] = "partial_frame"
    info["valid_frames"] = len(frames)
    info["valid_uncommitted_frames_ignored"] = len(frames) - info["committed_frames"]
    info["tail_bytes_ignored"] = len(wal_bytes) - offset
    if not info["committed_frames"]:
        return db_bytes, info
    pages = info["committed_database_pages"]
    output_size = pages * page_size
    merged = bytearray(db_bytes[:output_size])
    if len(merged) < output_size:
        merged.extend(b"\x00" * (output_size - len(merged)))
    applied = set()
    for pgno, payload_offset in frames[:info["committed_frames"]]:
        # Earlier transactions can reference pages removed by a later shrink.
        if pgno > pages:
            continue
        start = (pgno - 1) * page_size
        merged[start:start + page_size] = wal_bytes[payload_offset:payload_offset + page_size]
        applied.add(pgno)
    info.update(applied=True, unique_pages_applied=len(applied), output_bytes=len(merged))
    return bytes(merged), info
