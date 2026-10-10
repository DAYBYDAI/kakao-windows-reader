"""Read a bounded message window from verified, in-memory KakaoTalk snapshots.

Text decoding and title-member parsing are adapted from is-theo/kakao-cli-win
(kwin/collect.py, MIT License). See THIRD_PARTY_NOTICES.md in this distribution.
No function in this module writes a decrypted database or recovered key.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from pathlib import Path, PurePosixPath
from typing import Any

from .snapshot import read_database

_CHAT_FILE = re.compile(r"chatLogs_(\d+)\.edb", re.IGNORECASE)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        for encoding in ("utf-8", "cp949", "euc-kr"):
            try:
                return value.decode(encoding)
            except UnicodeDecodeError:
                pass
        return value.decode("utf-8", "replace")
    return str(value)


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _id(value: Any) -> str | None:
    number = _integer(value)
    return str(number) if number is not None else None


def _flag(value: Any) -> bool:
    if isinstance(value, (bytes, str)):
        return _text(value).strip().lower() not in ("", "0", "false", "none", "null")
    return bool(value)


def _json_names(value: Any) -> list[str]:
    text = _text(value)
    if not text:
        return []
    try:
        obj = json.loads(text)
    except (TypeError, ValueError):
        return [text] if text.strip() else []
    names: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key in ("nickName", "nickname", "name", "displayName", "userName"):
                candidate = item.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    names.append(candidate.strip())
                    return
            for candidate in item.values():
                walk(candidate)
        elif isinstance(item, list):
            for candidate in item:
                walk(candidate)
        elif isinstance(item, str) and item.strip():
            names.append(item.strip())

    walk(obj)
    return names


def _basename(relative: str) -> str:
    return PurePosixPath(relative.replace("\\", "/")).name


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _columns(con: sqlite3.Connection, table: str) -> dict[str, str]:
    return {_text(row[1]).lower(): _text(row[1]) for row in con.execute("PRAGMA table_info(" + _quoted(table) + ")")}


def _select_columns(columns: dict[str, str], wanted: list[str]) -> str:
    return ",".join(_quoted(columns[name.lower()]) if name.lower() in columns else "NULL" for name in wanted)


class _Reader:
    def __init__(self, account_dir: Path, keys: dict[str, str]):
        self.account = Path(account_dir).resolve()
        self.keys = keys
        self.checks: list[dict[str, Any]] = []
        self.errors: list[dict[str, str]] = []
        self.by_name: dict[str, str] = {}
        for relative in keys:
            self.path(relative)  # Reject traversal even for an unused key.
            name = _basename(relative).lower()
            if name in self.by_name and self.by_name[name] != relative:
                raise ValueError("Duplicate database filename in key map: " + name)
            self.by_name[name] = relative

    def path(self, relative: str) -> Path:
        normalized = relative.replace("\\", "/")
        if PurePosixPath(normalized).is_absolute() or ":" in normalized:
            raise ValueError("Database key paths must be relative to the selected account")
        path = (self.account / normalized).resolve()
        if not path.is_relative_to(self.account):
            raise ValueError("Database path escapes the selected account")
        return path

    def named(self, name: str) -> str | None:
        return self.by_name.get(name.lower())

    def open(self, relative: str) -> sqlite3.Connection:
        try:
            key = bytes.fromhex(self.keys[relative])
        except (TypeError, ValueError):
            raise ValueError("Invalid database key encoding") from None
        if len(key) != 32:
            raise ValueError("Expected a 32-byte database key")
        con, diagnostics = read_database(self.path(relative), key)
        self.checks.append({"file": relative, **diagnostics})
        return con

    def error(self, stage: str, error: Exception, chat_id: str | None = None) -> None:
        item = {"stage": stage, "error_type": type(error).__name__, "error": str(error)}
        if chat_id is not None:
            item["chat_id"] = chat_id
        self.errors.append(item)


def _metadata(reader: _Reader) -> tuple[dict[str, dict[str, Any]], str | None]:
    relative = reader.named("chatListInfo.edb")
    if not relative:
        raise ValueError("Missing chatListInfo.edb key. Recover keys while KakaoTalk is signed in.")
    con = reader.open(relative)
    try:
        columns = _columns(con, "chatRoomList")
        if "chatid" not in columns:
            raise ValueError("Unsupported room-list schema: chatId is missing")
        wanted = ["chatId", "type", "chatRoomTitle", "directChatMemberId", "titleDisplayMembers", "lastChatlog", "lastLogId_ByCHATLOGS", "lastLogId"]
        rooms: dict[str, dict[str, Any]] = {}
        for row in con.execute("SELECT " + _select_columns(columns, wanted) + " FROM chatRoomList"):
            cid, kind, title, direct, members, latest, local_log, last_log = row
            cid = _id(cid)
            if cid is None:
                raise ValueError("Unsupported room-list row: invalid chatId")
            try:
                latest = json.loads(_text(latest)) if latest else {}
            except (TypeError, ValueError):
                latest = {}
            if not isinstance(latest, dict):
                latest = {}
            rooms[cid] = {"chat_id": cid, "type": _text(kind), "raw_title": _text(title).strip(),
                          "direct_id": _id(direct), "member_names": _json_names(members), "latest": latest,
                          "local_last_log_id": _id(local_log), "last_log_id": _id(last_log)}
        own_ids: set[str] = set()
        member_columns = _columns(con, "chatMembers")
        for cid, room in rooms.items():
            if room["type"] != "MemoChat":
                continue
            if "chatid" in member_columns and "userid" in member_columns:
                query = "SELECT " + _quoted(member_columns["userid"]) + " FROM chatMembers WHERE " + _quoted(member_columns["chatid"]) + "=?"
                ids = {_id(row[0]) for row in con.execute(query, (int(cid),))}
                ids = {uid for uid in ids if uid is not None and int(uid) > 0}
                if len(ids) == 1:
                    own_ids.update(ids)
                elif len(ids) > 1:
                    raise ValueError("Ambiguous own-user identity in MemoChat members")
            direct = room["direct_id"]
            if direct is not None and int(direct) > 0:
                own_ids.add(direct)
        if len(own_ids) > 1:
            raise ValueError("Ambiguous own-user identity")
        return rooms, next(iter(own_ids), None)
    finally:
        con.close()


def _contacts(reader: _Reader, identifiers: set[str]) -> dict[str, str]:
    relative = reader.named("TalkUserDB.edb")
    if not relative or not identifiers:
        return {}
    con = None
    try:
        con = reader.open(relative)
        columns = _columns(con, "talkUser")
        if "userid" not in columns:
            raise ValueError("Unsupported contacts schema: userId is missing")
        wanted = ["userId", "nickName", "friendNickName"]
        result: dict[str, str] = {}
        ordered = sorted(identifiers, key=int)
        for offset in range(0, len(ordered), 400):
            batch = [int(uid) for uid in ordered[offset:offset + 400]]
            query = "SELECT " + _select_columns(columns, wanted) + " FROM talkUser WHERE " + _quoted(columns["userid"]) + " IN (" + ",".join("?" for _ in batch) + ")"
            for uid, nick, friend in con.execute(query, batch):
                result[str(uid)] = _text(friend) or _text(nick) or str(uid)
        return result
    except Exception as error:
        reader.error("contacts", error)
        return {}
    finally:
        if con is not None:
            con.close()


def _title(cid: str, room: dict[str, Any], contacts: dict[str, str]) -> str:
    if room.get("raw_title"):
        return room["raw_title"]
    if room.get("type") == "MemoChat":
        return "나와의 채팅"
    if room.get("direct_id") in contacts:
        return contacts[room["direct_id"]]
    names = room.get("member_names", [])
    return ", ".join(dict.fromkeys(names[:8])) if names else "대화방 " + cid


def _room_files(reader: _Reader) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in reader.account.rglob("*.edb"):
        match = _CHAT_FILE.fullmatch(path.name)
        if match:
            cid = str(int(match.group(1)))
            if cid in files:
                raise ValueError("Multiple chat databases for room " + cid)
            files[cid] = str(path.relative_to(reader.account))
    for name, relative in reader.by_name.items():
        match = _CHAT_FILE.fullmatch(name)
        if match:
            files[str(int(match.group(1)))] = relative
    return files


def list_rooms(account_dir: Path, keys: dict[str, str]) -> list[dict[str, Any]]:
    """List names and activity metadata, without returning message bodies."""
    reader = _Reader(account_dir, keys)
    rooms, _ = _metadata(reader)
    contacts = _contacts(reader, {r["direct_id"] for r in rooms.values() if r["direct_id"]})
    files = _room_files(reader)
    result = []
    for cid, room in rooms.items():
        relative = files.get(cid)
        result.append({"chat_id": cid, "id": cid, "title": _title(cid, room, contacts),
                       "room": _title(cid, room, contacts), "type": room["type"],
                       "last_sent_at": _integer(room["latest"].get("sendAt")),
                       "key_available": bool(reader.named("chatLogs_" + cid + ".edb")),
                       "database_present": bool(relative and reader.path(relative).is_file()),
                       "title_lookup_errors": reader.errors})
    return sorted(result, key=lambda r: (-(r["last_sent_at"] or 0), int(r["chat_id"])))


def _choose_rooms(rooms: dict[str, dict[str, Any]], files: dict[str, str], contacts: dict[str, str], filters: list[str] | None) -> set[str]:
    if not filters:
        return set(rooms) | set(files)
    chosen: set[str] = set()
    for query in filters:
        if query in rooms or query in files:
            chosen.add(query)
            continue
        matches = [cid for cid, room in rooms.items() if _title(cid, room, contacts) == query]
        if not matches:
            raise ValueError("Room was not found by exact name or chat_id: " + query)
        if len(matches) > 1:
            raise ValueError("Ambiguous room name; use chat_id: " + query + " (" + ", ".join(matches) + ")")
        chosen.add(matches[0])
    return chosen


def _direction(sender: str | None, own_id: str | None) -> str:
    if sender is None or own_id is None:
        return "unknown"
    return "sent" if sender == own_id else "received"


def _accept(direction: str, actual: str) -> bool:
    return direction == "all" or direction == actual


def collect(account_dir: Path, keys: dict[str, str], start: dt.datetime, end: dt.datetime,
            room_filters: list[str] | None = None, direction: str = "received") -> dict[str, Any]:
    """Collect an inclusive sendAt window; completeness beyond local cache is never promised."""
    if start.tzinfo is None or start.utcoffset() is None or end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("start and end must be timezone-aware datetimes")
    if end < start:
        raise ValueError("end must not precede start")
    if direction not in ("received", "sent", "all"):
        raise ValueError("direction must be received, sent, or all")
    start_ts, end_ts = start.timestamp(), end.timestamp()
    reader = _Reader(account_dir, keys)
    rooms, own_id = _metadata(reader)
    if own_id is None and direction != "all":
        raise ValueError("Unable to distinguish received and sent messages: own-user identity is unavailable")
    contacts = _contacts(reader, {r["direct_id"] for r in rooms.values() if r["direct_id"]})
    files = _room_files(reader)
    chosen = _choose_rooms(rooms, files, contacts, room_filters)
    messages: dict[tuple[str, str], dict[str, Any]] = {}
    states: dict[str, dict[str, Any]] = {}
    unknown_senders = 0
    for cid in sorted(chosen, key=int):
        relative = reader.named("chatLogs_" + cid + ".edb")
        if relative is None:
            states[cid] = {"state": "missing_key"}
            continue
        con = None
        try:
            con = reader.open(relative)
            columns = _columns(con, "chatLogs")
            if not columns:
                states[cid] = {"state": "empty_local_db"}
                continue
            needed = ["logId", "authorId", "type", "message", "sendAt"]
            if not all(name.lower() in columns for name in needed):
                raise ValueError("Unsupported chat schema: required message columns are missing")
            optional = [name for name in ("deleted", "prev_msg_missing", "next_msg_missing") if name in columns]
            gaps = [name for name in ("prev_msg_missing", "next_msg_missing") if name in columns]
            total, first_ts, last_ts, maximum = con.execute("SELECT COUNT(*),MIN(sendAt),MAX(sendAt),MAX(logId) FROM chatLogs").fetchone()
            diagnostic = {"state": "read_verified", "local_rows": int(total), "local_first_send_at": _integer(first_ts),
                          "local_last_send_at": _integer(last_ts), "local_max_log_id": _id(maximum), "gap_columns_available": gaps,
                          "window_rows_including_own": 0, "own_sent_rows_in_window": 0,
                          "gap_rows_in_window_including_own": 0, "boundary_gap_rows_toward_window": 0}
            boundary_names = ["logId", "authorId", "sendAt"] + gaps
            for label, comparison, order, boundary, gap_direction in (
                ("before_window", "<", "DESC", start_ts, "next_msg_missing"),
                ("after_window", ">", "ASC", end_ts, "prev_msg_missing"),
            ):
                query = "SELECT " + _select_columns(columns, boundary_names) + " FROM chatLogs WHERE sendAt" + comparison + "? ORDER BY sendAt " + order + ",logId " + order + " LIMIT 1"
                row = con.execute(query, (boundary,)).fetchone()
                if row is None:
                    diagnostic[label] = None
                    continue
                item = dict(zip(boundary_names, row))
                crossing = _flag(item.get(gap_direction)) if gap_direction in gaps else None
                diagnostic[label] = {"log_id": _id(item["logId"]), "send_at": _integer(item["sendAt"]),
                                     "own_sent": _id(item["authorId"]) == own_id,
                                     "prev_msg_missing": _flag(item.get("prev_msg_missing")) if "prev_msg_missing" in gaps else None,
                                     "next_msg_missing": _flag(item.get("next_msg_missing")) if "next_msg_missing" in gaps else None,
                                     "gap_toward_window": crossing}
                diagnostic["boundary_gap_rows_toward_window"] += int(bool(crossing))
            names = needed + optional
            query = "SELECT " + _select_columns(columns, names) + " FROM chatLogs WHERE sendAt>=? AND sendAt<=? ORDER BY sendAt,logId"
            for row in con.execute(query, (start_ts, end_ts)):
                item = dict(zip(names, row))
                sender = _id(item["authorId"])
                actual_direction = _direction(sender, own_id)
                gap = _flag(item.get("prev_msg_missing")) or _flag(item.get("next_msg_missing"))
                diagnostic["window_rows_including_own"] += 1
                diagnostic["gap_rows_in_window_including_own"] += int(gap)
                diagnostic["own_sent_rows_in_window"] += int(actual_direction == "sent")
                unknown_senders += int(actual_direction == "unknown")
                if not _accept(direction, actual_direction):
                    continue
                logid, ts = _id(item["logId"]), _integer(item["sendAt"])
                if logid is None or ts is None:
                    raise ValueError("Invalid message ID or timestamp")
                messages[(cid, logid)] = {"chat_id": cid, "log_id": logid, "sender_id": sender, "sent_at": ts,
                    "sent_at_iso": dt.datetime.fromtimestamp(ts, start.tzinfo).isoformat(), "type": _integer(item["type"]),
                    "message": "[삭제된 메시지]" if _flag(item.get("deleted")) else _text(item["message"]),
                    "source": "chat_db", "local_gap_flag": gap, "direction": actual_direction}
            states[cid] = diagnostic
        except Exception as error:
            reader.error("chat_database", error, cid)
            # Do not retain a partly read database's rows as verified history.
            messages = {key: value for key, value in messages.items() if key[0] != cid}
            states[cid] = {"state": "read_failed"}
        finally:
            if con is not None:
                con.close()
    initial_tails = {cid: _id(room["latest"].get("logId")) for cid, room in rooms.items()}
    refreshed = False
    try:
        refreshed_rooms, refreshed_own_id = _metadata(reader)
        if own_id != refreshed_own_id:
            raise ValueError("Own-user identity changed during collection; retry with a stable account")
        rooms = refreshed_rooms
        refreshed = True
    except Exception as error:
        reader.error("metadata_refresh", error)
    if not room_filters:
        chosen.update(rooms)
    candidates: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    unknown_metadata_time = 0
    unassessed_rooms = 0
    for cid in sorted(chosen, key=int):
        room = rooms.get(cid, {})
        latest = room.get("latest", {})
        timestamp = _integer(latest.get("sendAt"))
        state = states.get(cid, {"state": "not_read"})
        local_recent = state.get("window_rows_including_own", 0) > 0
        unknown_metadata_time += int(timestamp is None or timestamp <= 0)
        if not room_filters and not local_recent and (timestamp is None or timestamp < start_ts):
            unassessed_rooms += int(state["state"] != "read_verified")
            continue
        expected = [_integer(latest.get("logId")), _integer(room.get("local_last_log_id")), _integer(room.get("last_log_id"))]
        expected_tail = max((value for value in expected if value is not None and value > 0), default=None)
        local_tail = _integer(state.get("local_max_log_id"))
        tail = "unknown" if expected_tail is None or local_tail is None else (
            "behind_metadata" if local_tail < expected_tail else "matches_metadata" if local_tail == expected_tail else "ahead_of_metadata")
        relative = files.get(cid)
        candidate = {"chat_id": cid, "title": _title(cid, room, contacts), "room": _title(cid, room, contacts),
                     "last_sent_at": timestamp, "key_available": bool(reader.named("chatLogs_" + cid + ".edb")),
                     "database_present": bool(relative and reader.path(relative).is_file()), "read_state": state["state"],
                     "metadata_expected_tail_log_id": _id(expected_tail), "metadata_tail_comparison": tail, **state}
        candidates.append(candidate)
        if state["state"] != "read_verified":
            missing.append(dict(candidate))
        room_warnings = []
        if state["state"] == "read_verified":
            if len(state["gap_columns_available"]) < 2:
                room_warnings.append("gap_columns_unavailable")
            if state["gap_rows_in_window_including_own"]:
                room_warnings.append("gap_flag_in_window_including_own")
            if state["boundary_gap_rows_toward_window"]:
                room_warnings.append("adjacent_gap_flag_toward_window")
            if tail == "behind_metadata":
                room_warnings.append("local_tail_behind_refreshed_metadata")
            if tail == "unknown":
                room_warnings.append("metadata_tail_comparison_unavailable")
        if timestamp is not None and start_ts <= timestamp <= end_ts:
            sender, logid = _id(latest.get("authorId")), _id(latest.get("logId"))
            actual_direction = _direction(sender, own_id)
            if logid is None or int(logid) <= 0:
                room_warnings.append("invalid_preview_log_id")
            elif _accept(direction, actual_direction) and (cid, logid) not in messages:
                messages[(cid, logid)] = {"chat_id": cid, "log_id": logid, "sender_id": sender, "sent_at": timestamp,
                    "sent_at_iso": dt.datetime.fromtimestamp(timestamp, start.tzinfo).isoformat(), "type": _integer(latest.get("type")),
                    "message": "[삭제된 메시지]" if _flag(latest.get("deleted")) else _text(latest.get("message")),
                    "source": "room_list_preview", "local_gap_flag": False, "direction": actual_direction}
                if not any(item["chat_id"] == cid for item in missing):
                    missing.append({**candidate, "read_state": "latest_log_missing_in_chat_db"})
            elif actual_direction == "unknown":
                room_warnings.append("unknown_preview_sender")
        if room_warnings:
            warnings.append({"chat_id": cid, "warnings": room_warnings})
    contacts.update(_contacts(reader, {message["sender_id"] for message in messages.values() if message["sender_id"] and message["sender_id"] not in contacts}))
    ordered = sorted(messages.values(), key=lambda message: (message["sent_at"], int(message["chat_id"]), int(message["log_id"])))
    for message in ordered:
        message["room"] = _title(message["chat_id"], rooms.get(message["chat_id"], {}), contacts)
        message["sender"] = contacts.get(message["sender_id"], message["sender_id"] or "알 수 없음")
    gap_count = sum(state.get("gap_rows_in_window_including_own", 0) for state in states.values())
    boundary_count = sum(state.get("boundary_gap_rows_toward_window", 0) for state in states.values())
    wal_warnings = [{"file": check["file"], "tail_reason": check.get("wal", {}).get("tail_reason")} for check in reader.checks
                    if check.get("wal", {}).get("tail_reason") in ("frame_checksum_mismatch", "invalid_page_number", "partial_frame")]
    preview_count = sum(message["source"] == "room_list_preview" for message in ordered)
    summary = {"window_start": start.isoformat(), "window_end": end.isoformat(),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "direction": direction,
        "status": "partial" if missing or reader.errors or warnings or wal_warnings or unknown_senders else "local_snapshot_checked",
        "collection_scope": "local_pc_cache", "completeness": "not_proven", "all_received_messages_complete": False,
        "timestamp_basis": "message_sendAt_inclusive_window", "snapshot_consistency": "per_file_stable_reads_not_global_transaction",
        "message_count": len(ordered), "received_messages": sum(message["direction"] == "received" for message in ordered),
        "sent_messages": sum(message["direction"] == "sent" for message in ordered), "rooms_with_messages": len({m["chat_id"] for m in ordered}),
        "chat_db_messages": len(ordered) - preview_count, "room_list_preview_messages": preview_count,
        "recent_active_rooms": len(candidates), "recent_rooms_without_verified_history": len(missing),
        "read_errors": reader.errors, "local_gap_flags_in_window": gap_count, "local_gap_flags_at_window_boundaries": boundary_count,
        "gap_checks_include_own_sent_rows": True, "own_sent_rows_in_window": sum(s.get("own_sent_rows_in_window", 0) for s in states.values()),
        "unknown_sender_rows_in_window": unknown_senders, "own_identity_resolved": own_id is not None,
        "own_sent_messages_excluded": direction == "received", "coverage_warnings": warnings,
        "rooms_with_coverage_warnings": len(warnings), "wal_tail_warnings": wal_warnings,
        "metadata_refreshed_after_chat_reads": refreshed,
        "metadata_changed_rooms_during_read": sum(initial_tails.get(cid) != _id(r["latest"].get("logId")) for cid, r in rooms.items()),
        "rooms_with_unknown_metadata_time": unknown_metadata_time, "unassessed_rooms_without_recent_metadata": unassessed_rooms,
        "verified_databases": len({check["file"] for check in reader.checks}), "database_snapshot_checks": len(reader.checks),
        "wal_databases_applied": len({check["file"] for check in reader.checks if check.get("wal", {}).get("applied")}),
        "limitations": ["Results cover the selected PC's local cache; complete server history is not proven.",
            "The window uses message sendAt, not this PC's receipt time.",
            "Integrity checks and gap markers do not prove that messages are absent or history is complete.",
            "Files are read separately; the collection is not one global transaction.",
            "A room-list preview is only one last message, not the room's full history.",
            "Rooms without a key and without recent metadata cannot be assessed for recent activity.",
            "Actual attachment files are not collected."]}
    return {"summary": summary, "messages": ordered, "rooms": candidates, "missing_rooms": missing}
