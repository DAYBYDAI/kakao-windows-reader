"""Read-only SQLCipher raw-key candidate recovery for KakaoTalk.

Adapted from https://github.com/is-theo/kakao-cli-win (MIT),
Copyright (c) 2026 theo; see THIRD_PARTY_NOTICES.md. The x64 layout was validated on KakaoTalk
26.7.1.5263; the x86 branch follows SQLCipher's layout and has synthetic tests
only. This is a version-specific heuristic, not a stable KakaoTalk API.
"""
from __future__ import annotations

from bisect import bisect_right
from pathlib import Path, PurePosixPath, PureWindowsPath
import struct

from .crypto import PAGE_SIZE, verify_first_page
from . import winproc

_CODEC_PATTERN = struct.pack("<8I", 256000, 2, 16, 32, 16, 16, 4096, 0x63)


def candidate_keys_from_regions(regions: list[tuple[int, bytes]], bitness: int) -> tuple[set[bytes], int]:
    """Extract candidates from supplied buffers only; never print or persist them."""
    if bitness not in (32, 64):
        raise ValueError("unsupported_target_bitness")
    ordered = sorted(regions, key=lambda item: item[0])
    bases = [base for base, _ in ordered]

    def read(address: int, count: int):
        index = bisect_right(bases, address) - 1
        if index < 0:
            return None
        base, data = ordered[index]
        offset = address - base
        if offset < 0 or offset + count > len(data):
            return None
        return data[offset:offset + count]

    pointer_size = bitness // 8
    pointer_format = "<Q" if bitness == 64 else "<I"
    codec_offsets = (96, 104) if bitness == 64 else (76, 80)
    keys: set[bytes] = set()
    contexts: set[int] = set()
    for base, data in ordered:
        position = 0
        while True:
            position = data.find(_CODEC_PATTERN, position)
            if position < 0:
                break
            address = base + position - 4
            position += 4
            raw = read(address, 60)
            if raw is None:
                continue
            values = struct.unpack("<15I", raw)
            if values[7] != PAGE_SIZE or values[9] != 80 or values[10] != 64 or not values[14] & 1:
                continue
            if address in contexts:
                continue
            contexts.add(address)
            for offset in codec_offsets:
                pointer = read(address + offset, pointer_size)
                if pointer is None:
                    continue
                cipher_address = struct.unpack(pointer_format, pointer)[0]
                cipher_header = read(cipher_address, 8 + pointer_size)
                if cipher_header is None:
                    continue
                derive, pass_size = struct.unpack("<ii", cipher_header[:8])
                if derive not in (0, 1) or not 0 <= pass_size <= 4096:
                    continue
                key_address = struct.unpack(pointer_format, cipher_header[8:])[0]
                key = read(key_address, 32)
                if key and key != bytes(32):
                    keys.add(key)
    return keys, len(contexts)


def _normalize_relative(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid_cached_key_path")
    normalized = value.replace("\\", "/")
    if (PureWindowsPath(value).drive or PureWindowsPath(value).is_absolute()
            or PurePosixPath(normalized).is_absolute()
            or ".." in PurePosixPath(normalized).parts):
        raise ValueError("cached_key_path_must_be_relative")
    return PurePosixPath(normalized).as_posix()


def recover_keys(account_dir: Path, existing: dict[str, str] | None = None) -> tuple[dict[str, str], dict]:
    """Return currently HMAC-verified per-file keys and aggregate diagnostics.

    No key file, process-memory dump or plaintext is written or logged. Cached
    candidates are checked against each current database; merely being cached
    never marks a key verified. Account selection and key-file persistence are
    the caller's responsibility. Only the running KakaoTalk.exe is read.
    """
    account = Path(account_dir).resolve(strict=True)
    if not account.is_dir():
        raise ValueError("account_directory_required")
    if existing is not None and not isinstance(existing, dict):
        raise ValueError("existing_keys_must_be_a_mapping")
    prior: dict[str, str] = {}
    candidates: set[bytes] = set()
    for relative, hex_key in (existing or {}).items():
        relative = _normalize_relative(relative)
        if not isinstance(hex_key, str):
            raise ValueError("invalid_cached_key_encoding")
        try:
            raw = bytes.fromhex(hex_key)
        except ValueError:
            raise ValueError("invalid_cached_key_encoding") from None
        if len(raw) != 32 or raw == bytes(32):
            raise ValueError("invalid_cached_key_length_or_value")
        prior[relative] = raw.hex()
        candidates.add(raw)
    diagnostics = {
        "targets": 0, "candidate_keys": len(candidates),
        "existing_keys_loaded": len(prior), "existing_keys_revalidated": 0,
        "existing_keys_rejected": 0, "recovered_keys": 0,
        "process_count": 0, "codec_contexts": 0, "architectures": [],
        "target_read_errors": 0, "process_read_errors": [],
        "warnings": ["version_specific_codec_layout", "only_open_database_keys_may_be_resident"],
        "live_validated_target": "KakaoTalk 26.7.1.5263 x64",
        "x86_validation": "synthetic_only", "history_complete": False,
    }
    targets = []
    for path in sorted(account.rglob("*.edb")):
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(account) or not resolved.is_file():
                continue
            with resolved.open("rb") as handle:
                page = handle.read(PAGE_SIZE)
            if len(page) != PAGE_SIZE:
                diagnostics["target_read_errors"] += 1
                continue
            targets.append((path.relative_to(account).as_posix(), page))
        except OSError:
            diagnostics["target_read_errors"] += 1
    diagnostics["targets"] = len(targets)
    keys: dict[str, str] = {}

    def validate(keys_to_try):
        for relative, page in targets:
            if relative in keys:
                continue
            for key in keys_to_try:
                if verify_first_page(page, key):
                    keys[relative] = key.hex()
                    break

    validate(candidates)
    if len(keys) < len(targets):
        try:
            processes = winproc.list_processes()
        except OSError as error:
            processes = []
            diagnostics["process_read_errors"].append({"stage": "process_listing", "error": type(error).__name__})
        diagnostics["process_count"] = len(processes)
        for process in processes:
            if PureWindowsPath(process.get("exe", "")).name.lower() != "kakaotalk.exe":
                continue
            bitness = process.get("bitness")
            if bitness not in (32, 64):
                diagnostics["process_read_errors"].append({"pid": process["pid"], "error": "unknown_process_architecture"})
                continue
            if bitness not in diagnostics["architectures"]:
                diagnostics["architectures"].append(bitness)
            try:
                regions = winproc.read_process_regions(process["pid"], bitness)
                found, count = candidate_keys_from_regions(regions, bitness)
                del regions
                diagnostics["codec_contexts"] += count
                new_candidates = found - candidates
                candidates.update(found)
                validate(new_candidates)
            except OSError as error:
                diagnostics["process_read_errors"].append({"pid": process["pid"], "error": type(error).__name__})
    diagnostics["candidate_keys"] = len(candidates)
    diagnostics["recovered_keys"] = len(keys)
    diagnostics["existing_keys_revalidated"] = sum(keys.get(relative) == key for relative, key in prior.items())
    diagnostics["existing_keys_rejected"] = len(prior) - diagnostics["existing_keys_revalidated"]
    if not targets:
        diagnostics["warnings"].append("no_readable_edb_targets")
    if len(keys) < len(targets):
        diagnostics["warnings"].append("some_database_keys_unavailable")
    return keys, diagnostics
