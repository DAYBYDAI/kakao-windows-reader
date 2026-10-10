"""Per-user polling state for local, read-only KakaoTalk snapshots.

This module does not register scheduled tasks, launch KakaoTalk, open chat rooms,
print messages, or communicate with a server. Call ``scan`` from a user-owned
scheduler. Its results remain partial local-cache observations.
"""
from __future__ import annotations

import contextlib
import ctypes
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from . import state
from .collection import collect
from .export import write_exports
from .recovery import recover_keys
from .winproc import list_processes

_FORMAT = 1
_STALE_SECONDS = 6 * 60 * 60


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _time(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timestamp must include a timezone")
    return parsed


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    descriptor = None
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = None
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _json_object(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as handle:
        result = json.load(handle)
    if not isinstance(result, dict):
        raise ValueError("INVALID_STATE: Expected a JSON object in " + path.name)
    return result


def _process_alive(pid: int) -> bool | None:
    """Return False only when process death is positively established."""
    if not isinstance(pid, int) or isinstance(pid, bool) or not 0 < pid <= 0xFFFFFFFF:
        return None
    if pid == os.getpid():
        return True
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except (PermissionError, OSError):
            return None
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE, no mutation rights.
    if not handle:
        return False if ctypes.get_last_error() == 87 else None
    try:
        status = kernel.WaitForSingleObject(handle, 0)
        return False if status == 0 else True if status == 258 else None
    finally:
        kernel.CloseHandle(handle)


class ScanBusy(ValueError):
    pass


@contextlib.contextmanager
def _scan_lock(directory: Path, now: dt.datetime) -> Iterator[None]:
    """O_EXCL lock with serialized, conservative stale-lock recovery.

    A short recovery gate serializes *all* acquisitions, preventing two stale
    contenders from deleting a newly acquired lock. A gate abandoned by a crash
    is deliberately not reclaimed automatically; manual inspection is safer.
    """
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / "scan.lock"
    gate = directory / "scan.lock.recovery"
    token = secrets.token_hex(16)
    payload = {"format": _FORMAT, "pid": os.getpid(), "created_at": now.isoformat(), "token": token}
    gate_descriptor = None
    lock_descriptor = None
    acquired = False
    try:
        try:
            gate_descriptor = os.open(gate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise ScanBusy("SCAN_BUSY: Another scan is acquiring its lock. A persistent scan.lock.recovery requires manual inspection.") from None
        os.write(gate_descriptor, json.dumps(payload).encode("utf-8"))
        if lock.exists():
            try:
                previous = _json_object(lock)
                age = (now - _time(previous["created_at"])).total_seconds()
                dead = _process_alive(previous["pid"]) is False
                if age <= _STALE_SECONDS or not dead:
                    raise ScanBusy("SCAN_BUSY: A scan may still be running; its lock was preserved.")
            except ScanBusy:
                raise
            except (OSError, ValueError, KeyError, TypeError):
                raise ScanBusy("SCAN_BUSY: The existing lock cannot safely be reclaimed; inspect scan.lock manually.") from None
            lock.unlink()
        try:
            lock_descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise ScanBusy("SCAN_BUSY: Another scan holds the lock.") from None
        os.write(lock_descriptor, json.dumps(payload).encode("utf-8"))
        os.fsync(lock_descriptor)
        acquired = True
    finally:
        if gate_descriptor is not None:
            os.close(gate_descriptor)
            gate.unlink(missing_ok=True)
        if not acquired and lock_descriptor is not None:
            os.close(lock_descriptor)
            lock.unlink(missing_ok=True)
    try:
        yield
    finally:
        if lock_descriptor is not None:
            os.close(lock_descriptor)
        # A live owner cannot have its lock reclaimed through this module.
        # Still require our token before removing a possibly replaced file.
        try:
            if _json_object(lock).get("token") == token:
                lock.unlink()
        except (OSError, ValueError):
            pass


def _profile(directory: Path, config: dict, status: dict | None = None) -> None:
    profile_path = config.get("profile_path")
    if not profile_path:
        return
    profile = {"format": _FORMAT, "account_id": config.get("account_id"),
               "state_directory": str(directory.resolve()), "config_file": str((directory / "config.json").resolve()),
               "configured_at": config["configured_at"], "windows_user_scope": "current_user",
               "latest_status_file": str((directory / "latest.json").resolve())}
    if status:
        referenced_files = status.get("files") or (status.get("last_successful_scan") or {}).get("files", {})
        profile.update({"account_id": status.get("account_id", config.get("account_id")),
                        "status": status.get("status"), "last_scan_end": status.get("last_scan_end"),
                        "summary_path": referenced_files.get("summary"),
                        "last_attempt_at": status.get("attempted_at")})
    _atomic_json(Path(profile_path), profile)


def setup_config(state_dir: Path, account_id: str | None, rooms: list[str] | None = None,
                 *, profile_path: Path | None = None, kakao_root: Path | None = None) -> dict:
    """Write personal configuration; scheduling and interactive consent are caller-owned."""
    if account_id is not None and (not isinstance(account_id, str) or not account_id or any(c in account_id for c in ("/", "\\", ":")) or account_id in (".", "..")):
        raise ValueError("INVALID_ACCOUNT: Use one exact account_id from accounts.")
    if rooms is not None and (not isinstance(rooms, list) or any(not isinstance(room, str) or not room for room in rooms)):
        raise ValueError("INVALID_ROOMS: Use a list of exact room names or chat IDs.")
    directory = Path(state_dir).resolve()
    configured_at = _now()
    with _scan_lock(directory, configured_at):
        previous = _json_object(directory / "config.json") if (directory / "config.json").exists() else {}
        selected_profile = str(Path(profile_path).resolve()) if profile_path is not None else previous.get("profile_path")
        if selected_profile and Path(selected_profile) in {directory / name for name in ("config.json", "index.json", "latest.json", "scan.lock", "scan.lock.recovery", "index.sqlite3")}:
            raise ValueError("INVALID_PROFILE: Profile path must be separate from the reader's state files.")
        config = {"format": _FORMAT, "account_id": account_id,
                  "rooms": list(dict.fromkeys(rooms)) if rooms else None, "direction": "received",
                  "overlap_seconds": 300, "initial_lookback_hours": 2, "max_catchup_days": 7,
                  "configured_at": configured_at.isoformat(), "profile_path": selected_profile,
                  "kakao_root": str(Path(kakao_root).resolve()) if kakao_root is not None else previous.get("kakao_root")}
        _atomic_json(directory / "config.json", config)
        _profile(directory, config)
        return config


def _load_config(directory: Path) -> dict:
    if not (directory / "config.json").is_file():
        raise ValueError("SETUP_REQUIRED: Run setup before scan.")
    config = _json_object(directory / "config.json")
    if config.get("format") != _FORMAT or config.get("direction") != "received":
        raise ValueError("INVALID_CONFIG: Unsupported configuration format or direction.")
    for key, maximum in (("overlap_seconds", 86400), ("initial_lookback_hours", 168), ("max_catchup_days", 366)):
        value = config.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or not 0 < value <= maximum:
            raise ValueError("INVALID_CONFIG: " + key + " is outside the supported range.")
    if config.get("rooms") is not None and (not isinstance(config["rooms"], list) or any(not isinstance(room, str) or not room for room in config["rooms"])):
        raise ValueError("INVALID_CONFIG: rooms must contain exact names or IDs.")
    return config


def _scope(account: Path, config: dict) -> str:
    value = {"account": str(account.resolve()).casefold(), "rooms": sorted(set(config.get("rooms") or [])), "direction": config["direction"]}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=10)
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("CREATE TABLE IF NOT EXISTS seen_messages(scope TEXT NOT NULL, chat_id TEXT NOT NULL, log_id TEXT NOT NULL, first_scan_id TEXT NOT NULL, PRIMARY KEY(scope,chat_id,log_id))")
    connection.execute("CREATE TABLE IF NOT EXISTS scan_cursors(scope TEXT PRIMARY KEY, last_scan_end TEXT NOT NULL, last_scan_id TEXT NOT NULL, manifest_json TEXT NOT NULL)")
    connection.commit()
    return connection


def _failure(directory: Path, config: dict, now: dt.datetime, code: str, message: str,
             prior: dict | None = None, account_id: str | None = None) -> dict:
    # Only the selected account/configuration scope may supply a prior cursor.
    # A latest.json from a different account or room filter is not a prior scan.
    previous = prior or {}
    successful = (previous if previous.get("status") not in ("failed", "busy") else previous.get("last_successful_scan")) or None
    if successful:
        successful = {key: successful.get(key) for key in ("scan_id", "status", "window_start", "window_end", "last_scan_end", "files", "new_message_count")}
    response = {"format": _FORMAT, "status": "failed", "attempted_at": now.isoformat(),
                "account_id": account_id or config.get("account_id"),
                "error": {"code": code, "message": message}, "cursor_advanced": False,
                "last_scan_end": (successful or {}).get("last_scan_end"),
                "last_successful_scan": successful,
                "latest_status_file": str((directory / "latest.json").resolve())}
    _atomic_json(directory / "latest.json", response)
    _profile(directory, config, response)
    return response


def scan(state_dir: Path, kakao_root: Path | None = None) -> dict:
    """Refresh keys, collect a bounded local window, and publish metadata-only status.

    Capture the end time before key recovery. The SQLite index is authoritative
    for deduplication and cursor recovery; index.json/latest.json are atomic,
    human-readable mirrors. No message body is stored in the index or profile.
    """
    finish = _now()
    directory = Path(state_dir).resolve()
    try:
        with _scan_lock(directory, finish):
            config = _load_config(directory)
            connection = None
            prior = None
            account = None
            committed = False
            response = None
            try:
                root = Path(kakao_root) if kakao_root is not None else (Path(config["kakao_root"]) if config.get("kakao_root") else state.default_kakao_root())
                account = state.choose_account(root, config.get("account_id"))
                scope = _scope(account, config)
                connection = _database(directory / "index.sqlite3")
                cursor = connection.execute("SELECT last_scan_end,manifest_json FROM scan_cursors WHERE scope=?", (scope,)).fetchone()
                previous_pending = None
                if cursor:
                    last_end = _time(cursor[0])
                    prior = json.loads(cursor[1])
                    if last_end > finish:
                        return _failure(directory, config, finish, "CLOCK_MOVED_BACKWARD", "The previous scan ends in the future. Check the Windows clock before retrying.", prior, account.name)
                    begin = last_end - dt.timedelta(seconds=config["overlap_seconds"])
                    if prior.get("pending_start"):
                        previous_pending = _time(prior["pending_start"])
                        if previous_pending > last_end:
                            raise ValueError("INVALID_PENDING_WINDOW: Pending history starts after the saved cursor.")
                        begin = min(begin, previous_pending)
                else:
                    begin = finish - dt.timedelta(hours=config["initial_lookback_hours"])
                warnings = []
                if previous_pending is not None:
                    warnings.append({"code": "pending_history_retry", "pending_start": previous_pending.isoformat(),
                                     "message": "Rechecking an earlier window because a previous scan did not obtain these rooms' verified history."})
                earliest = finish - dt.timedelta(days=config["max_catchup_days"])
                if begin < earliest:
                    warnings.append({"code": "catchup_window_truncated", "requested_start": begin.isoformat(), "used_start": earliest.isoformat(),
                                     "message": "The gap exceeds the configured catch-up limit; older messages were not scanned."})
                    begin = earliest
                if not list_processes():
                    return _failure(directory, config, finish, "KAKAO_NOT_RUNNING", "KakaoTalk is not running. This scan was skipped and its cursor was preserved.", prior, account.name)
                existing = state.load_keys(directory, account)
                keys, recovery = recover_keys(account, existing=existing)
                state.save_keys(directory, account, keys)
                if not keys:
                    return _failure(directory, config, finish, "NO_DATABASE_KEYS", "No verified database keys are available. Sign in, open the required rooms, then retry.", prior, account.name)
                result = collect(account, keys, begin, finish, room_filters=config.get("rooms"), direction=config["direction"])
                scan_id = finish.strftime("%Y%m%dT%H%M%S.%fZ") + "-" + secrets.token_hex(4)
                destination = directory / "scans" / scan_id
                connection.execute("BEGIN IMMEDIATE")
                new_count = 0
                unique = set()
                for item in result["messages"]:
                    chat_id, log_id = item.get("chat_id"), item.get("log_id")
                    if not isinstance(chat_id, str) or not isinstance(log_id, str) or not chat_id or not log_id:
                        raise ValueError("INVALID_MESSAGE_IDS: Collector must return string chat_id and log_id.")
                    identity = (chat_id, log_id)
                    if identity in unique:
                        continue
                    unique.add(identity)
                    change = connection.execute("INSERT OR IGNORE INTO seen_messages(scope,chat_id,log_id,first_scan_id) VALUES(?,?,?,?)", (scope, chat_id, log_id, scan_id))
                    new_count += change.rowcount
                missing = result.get("missing_rooms", [])
                pending_start = min(begin, previous_pending).isoformat() if missing and previous_pending is not None else (begin.isoformat() if missing else None)
                historical_gaps = list((prior or {}).get("historical_gap_observations", []))
                recorded_gaps = {(item.get("chat_id"), tuple(item.get("warnings", []))) for item in historical_gaps}
                gap_codes = {"gap_flag_in_window_including_own", "adjacent_gap_flag_toward_window"}
                for item in result["summary"].get("coverage_warnings", []):
                    observed = sorted(set(item.get("warnings", [])) & gap_codes)
                    identity = (item.get("chat_id"), tuple(observed))
                    if observed and identity not in recorded_gaps:
                        historical_gaps.append({"chat_id": item.get("chat_id"), "warnings": observed,
                            "first_observed_window_start": begin.isoformat(), "first_observed_window_end": finish.isoformat()})
                        recorded_gaps.add(identity)
                summary = result["summary"]
                summary.update({"new_message_count": new_count, "scan_id": scan_id, "overlap_seconds": config["overlap_seconds"],
                                "scan_warnings": warnings, "pending_start": pending_start, "backfill_pending": bool(missing),
                                "historical_gap_observations": historical_gaps, "deduplication_basis": "scope_chat_id_log_id", "all_received_messages_complete": False})
                if any(warning["code"] == "catchup_window_truncated" for warning in warnings) or missing:
                    summary["status"] = "partial"
                status = summary.get("status", "partial")
                if status not in ("partial", "local_snapshot_checked"):
                    status = "partial"
                    summary["status"] = status
                files = write_exports(result, destination)
                missing_path = destination / "missing-rooms.json"
                _atomic_json(missing_path, {"scan_id": scan_id, "missing_rooms": missing, "coverage_warnings": summary.get("coverage_warnings", []),
                                            "note": "Open these rooms in KakaoTalk if you want the client to load available keys/history; opening may mark messages read."})
                files["missing_rooms"] = str(missing_path.resolve())
                response = {"format": _FORMAT, "scan_id": scan_id, "status": status, "attempted_at": finish.isoformat(),
                    "account_id": account.name, "scope": scope, "window_start": begin.isoformat(), "window_end": finish.isoformat(),
                    "last_scan_end": finish.isoformat(), "cursor_advanced": True, "new_message_count": new_count,
                    "pending_start": pending_start, "historical_gap_observations": historical_gaps,
                    "summary": summary, "missing_rooms": missing, "needs_attention": bool(missing or status == "partial"),
                    "files": files, "recovery": recovery, "latest_status_file": str((directory / "latest.json").resolve())}
                encoded = json.dumps(response, ensure_ascii=False)
                connection.execute("INSERT INTO scan_cursors(scope,last_scan_end,last_scan_id,manifest_json) VALUES(?,?,?,?) ON CONFLICT(scope) DO UPDATE SET last_scan_end=excluded.last_scan_end,last_scan_id=excluded.last_scan_id,manifest_json=excluded.manifest_json", (scope, finish.isoformat(), scan_id, encoded))
                connection.commit()
                committed = True
                _atomic_json(directory / "index.json", {"format": _FORMAT, "scope": scope, "account_id": account.name,
                    "last_scan_end": finish.isoformat(), "last_scan_id": scan_id, "latest_status_file": response["latest_status_file"],
                    "deduplication_index": str((directory / "index.sqlite3").resolve())})
                _atomic_json(directory / "latest.json", response)
                _profile(directory, config, response)
                return response
            except Exception as error:
                if connection is not None and not committed:
                    connection.rollback()
                if committed and response is not None:
                    # Export + canonical cursor are already durable. A mirror/profile
                    # publishing error must not misreport that the cursor was preserved.
                    response["publication_warning"] = {"code": "STATUS_MIRROR_WRITE_FAILED", "error_type": type(error).__name__,
                                                       "message": "The scan is saved; a status mirror or profile could not be updated."}
                    response["needs_attention"] = True
                    return response
                code = str(error).split(":", 1)[0] if ":" in str(error) else type(error).__name__
                # Arbitrary exception text could contain input or key material. Keep
                # status errors fixed, and expose only the exception class/code.
                if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,47}", code):
                    code = type(error).__name__
                return _failure(directory, config, finish, code, "The scan failed before committing its cursor. Check setup, KakaoTalk login, and available room keys, then retry.", prior, account.name if account else None)
            finally:
                if connection is not None:
                    connection.close()
    except ScanBusy as error:
        # Do not replace latest.json while a concurrent scan owns it.
        return {"format": _FORMAT, "status": "busy", "attempted_at": finish.isoformat(), "cursor_advanced": False,
                "error": {"code": "SCAN_BUSY", "message": str(error)}, "latest_status_file": str((directory / "latest.json").resolve())}
