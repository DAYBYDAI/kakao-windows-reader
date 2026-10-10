from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import pathlib
import platform
import re
import sys
import uuid
from . import __version__
from . import state
from .export import write_exports

class ArgumentError(ValueError): pass

class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ArgumentError("INVALID_ARGUMENTS: " + message)

def parse_time(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("INVALID_TIME: ISO 날짜를 사용하세요. 예: 2026-09-11T20:00:00+09:00") from exc
    if parsed.tzinfo is None:
        raise ValueError("TIMEZONE_REQUIRED: --start/--end에 +09:00 같은 시간대를 포함하세요.")
    return parsed

def time_window(since, start, end, now=None):
    finish = now or dt.datetime.now().astimezone()
    if since:
        if start or end:
            raise ValueError("INVALID_TIME_WINDOW: --since와 --start/--end는 함께 쓸 수 없습니다.")
        match = re.fullmatch(r"([1-9][0-9]*)(m|h|d)", since)
        if not match:
            raise ValueError("INVALID_DURATION: --since는 30m, 2h, 7d 형식입니다.")
        seconds = int(match[1]) * {"m": 60, "h": 3600, "d": 86400}[match[2]]
        if seconds > 366 * 86400:
            raise ValueError("WINDOW_TOO_LARGE: 한 번에 366일 이내를 지정하세요.")
        begin = finish - dt.timedelta(seconds=seconds)
    else:
        if not start or not end:
            raise ValueError("TIME_WINDOW_REQUIRED: --since 2h 또는 --start와 --end를 지정하세요.")
        begin, finish = parse_time(start), parse_time(end)
        if finish <= begin or (finish - begin).total_seconds() > 366 * 86400:
            raise ValueError("INVALID_TIME_WINDOW: 종료는 시작 이후이고 범위는 366일 이하여야 합니다.")
    return begin, finish

def make_parser():
    parser = Parser(prog="kakao-reader", description="Windows 카카오톡 로컬 DB 읽기 전용 조회. 메시지 전송/서버 접속 없음.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    for name, help_text in [
        ("doctor", "Windows 환경과 앱 상태 확인"), ("accounts", "로컬 계정 선택 정보"),
        ("recover", "실행 중인 카카오톡에서 키를 찾아 DPAPI 저장"),
        ("rooms", "방 이름/ID와 키 상태 조회"), ("read", "지정 시간 범위 조회"),
        ("setup", "개인 계정과 조회 범위 설정"), ("scan", "개인 설정으로 증분 수집"),
        ("latest", "마지막 스캔 상태 조회"), ("schedule", "매시간 Windows 예약 작업 관리"),
        ("search", "기간 내 대화에서 키워드 찾기")]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--kakao-root", type=pathlib.Path, help="기본 자동 탐지 경로를 바꿀 때만 사용")
        p.add_argument("--state-dir", type=pathlib.Path, help="기본 개인 저장 폴더를 바꿀 때만 사용")
        p.add_argument("--account", help="accounts에 표시된 정확한 account_id")
        if name == "setup":
            p.add_argument("--room", action="append")
            p.add_argument("--profile-file", type=pathlib.Path)
            p.add_argument("--hourly", action="store_true")
        if name == "schedule":
            action=p.add_mutually_exclusive_group()
            action.add_argument("--enable", action="store_true")
            action.add_argument("--disable", action="store_true")
            action.add_argument("--status", action="store_true")
        if name == "latest":
            p.add_argument("--include-messages", action="store_true")
        if name == "recover":
            p.add_argument("--reset-cache", action="store_true", help="기존 캐시를 무시하고 현재 키만 다시 저장")
        if name == "rooms":
            p.add_argument("--query", help="방 이름 일부로 검색")
            p.add_argument("--limit", type=int, default=100)
            p.add_argument("--active-since", help="마지막 메시지 시간이 최근인 방만 필터. 예: 2h")
        if name in ("read", "search"):
            if name == "search":
                p.add_argument("--query", required=True, help="본문 키워드(대소문자 구분 없음)")
                p.add_argument("--limit", type=int, default=50)
            p.add_argument("--since")
            p.add_argument("--start")
            p.add_argument("--end")
            p.add_argument("--all-rooms", action="store_true", help="개인 설정의 방 제한을 이번 조회에서 해제")
            p.add_argument("--room", action="append", help="정확한 방 이름 또는 chat_id. 반복 가능")
            p.add_argument("--direction", choices=["received", "sent", "all"], default="all" if name=="search" else "received")
            p.add_argument("--refresh-keys", action="store_true")
            p.add_argument("--out", type=pathlib.Path, help="새 결과 폴더. 생략하면 개인 exports 폴더")
            p.add_argument("--include-messages", action="store_true", help="파일 외에 stdout JSON에도 본문 포함")
    return parser

def emit(obj):
    if sys.stdout is not None:
        print(json.dumps(obj, ensure_ascii=False, indent=2))

def run(args):
    store = args.state_dir or state.default_state_dir()
    config={}
    if args.command in ("read","search","rooms","recover") and (store/"config.json").exists():
        config=json.loads((store/"config.json").read_text(encoding="utf-8"))
    root = args.kakao_root or (pathlib.Path(config["kakao_root"]) if config.get("kakao_root") else state.default_kakao_root())
    selected_account = args.account or config.get("account_id")
    if args.command == "scan":
        from .automation import scan
        result=scan(store,kakao_root=args.kakao_root)
        return result, 2 if result.get("status") in ("error","failed") else 0
    if args.command == "latest":
        path=store/"latest.json"
        if not path.exists():
            raise ValueError("NO_SCAN: setup 후 scan을 먼저 실행하세요.")
        result=json.loads(path.read_text(encoding="utf-8"))
        if args.include_messages and result.get("files",{}).get("json"):
            data=json.loads(pathlib.Path(result["files"]["json"]).read_text(encoding="utf-8"))
            result["messages"]=data["messages"]
        return result, 0
    if args.command == "setup":
        from .automation import setup_config
        account=state.choose_account(root,args.account)
        config=setup_config(store,account.name,rooms=args.room,profile_path=args.profile_file,kakao_root=args.kakao_root)
        result={"status":"configured","config":config}
        if args.hourly:
            from .scheduler import install_hourly
            result["schedule"]=install_hourly(pathlib.Path(sys.executable),pathlib.Path(__file__).resolve().parents[2]/"entrypoint.py",store)
        return result, 0
    if args.command == "schedule":
        from .scheduler import install_hourly,remove_hourly,status_hourly
        if args.enable:
            if not (store/"config.json").exists():
                raise ValueError("SETUP_REQUIRED: 먼저 setup으로 계정과 방 범위를 설정하세요.")
            result=install_hourly(pathlib.Path(sys.executable),pathlib.Path(__file__).resolve().parents[2]/"entrypoint.py",store)
        elif args.disable:
            result=remove_hourly(store)
        else:
            result=status_hourly(store)
        return result, 0
    if args.command == "accounts":
        return {"status": "ok", "accounts": state.accounts(root)}, 0
    if args.command == "doctor":
        from .winproc import list_processes
        processes = list_processes()
        available = state.accounts(root)
        found = []
        for account in available:
            path = root / account["account_id"]
            found.append({**account, "key_cache_present": state.cache_path(store, path).exists()})
        return {"status": "ok" if processes and available else "needs_setup", "version": __version__,
            "os": platform.platform(), "local_time":dt.datetime.now().astimezone().isoformat(), "python_bits": 64 if sys.maxsize > 2**32 else 32,
            "kakao_running": bool(processes),
            "processes": [{"pid": p["pid"], "bitness": p.get("bitness", 0)} for p in processes],
            "accounts": found, "state_directory": str(store.resolve()),
            "requirements": "Native Windows 10/11 x64, own logged-in KakaoTalk PC account",
            "network_used_by_reader": False, "writes_kakao_database": False}, 0
    # Capture the requested window before a potentially slow key scan.
    since = getattr(args,"since",None)
    if args.command=="search" and not (since or args.start or args.end): since="30d"
    window = time_window(since, args.start, args.end) if args.command in ("read","search") else None
    account = state.choose_account(root, selected_account)
    keys = {} if getattr(args, "reset_cache", False) else state.load_keys(store, account)
    recovery_info = None
    if args.command == "recover" or getattr(args, "refresh_keys", False):
        from .recovery import recover_keys
        keys, recovery_info = recover_keys(account, existing=keys)
        state.save_keys(store, account, keys)
        if args.command == "recover":
            return {"status": "ok" if keys else "no_keys", "account_id": account.name,
                "verified_database_keys": len(keys), "cache_protection": "Windows DPAPI current user",
                "diagnostics": recovery_info,
                "next_step": "필요한 방을 직접 열고 recover를 다시 실행하면 추가 키를 찾을 수 있습니다."}, 0 if keys else 2
    if not keys:
        raise ValueError("KEYS_REQUIRED: 카카오톡에 로그인하고 필요한 방을 연 뒤 recover를 실행하세요.")
    if args.command == "rooms":
        from .collection import list_rooms
        rooms = list_rooms(account, keys)
        if args.active_since:
            begin, _ = time_window(args.active_since, None, None)
            rooms = [r for r in rooms if r.get("last_sent_at") and r["last_sent_at"] >= begin.timestamp()]
        if args.query:
            rooms=[r for r in rooms if args.query.casefold() in str(r.get("title",r.get("room",""))).casefold()]
        if not 1 <= args.limit <= 2000:
            raise ValueError("INVALID_LIMIT: 방 목록 limit은 1~2000 사이입니다.")
        rooms.sort(key=lambda r:r.get("last_sent_at") or 0,reverse=True)
        total=len(rooms)
        return {"status": "ok", "rooms": rooms[:args.limit], "room_count":total,"results_truncated":total>args.limit,
                "note": "마지막 메시지 시간이 없는 방은 최근 활동 여부를 확정할 수 없습니다."}, 0
    from .collection import collect
    start, end = window
    if args.all_rooms and args.room:
        raise ValueError("INVALID_ROOMS: --room과 --all-rooms는 함께 쓸 수 없습니다.")
    selected_rooms=args.room
    if not args.all_rooms and not selected_rooms and not (args.account and args.account!=config.get("account_id")):
        selected_rooms=config.get("rooms")
    result = collect(account, keys, start, end, room_filters=selected_rooms, direction=args.direction)
    if args.command=="search":
        from .searching import filter_messages
        result=filter_messages(result,args.query,args.limit)
    destination = args.out or store / "exports" / (dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    result["summary"]["reader_version"] = __version__
    result["summary"]["key_cache_protection"] = "Windows DPAPI current user"
    paths = write_exports(result, destination)
    response = {"status": result["summary"].get("status", "partial"), "summary": result["summary"],
                "missing_rooms": result.get("missing_rooms", []), "files": paths}
    if recovery_info is not None:
        response["recovery"] = recovery_info
    if args.include_messages:
        response["messages"] = result["messages"]
    # Partial data is a successful query with a visible status, not an exception.
    return response, 0

def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        args = make_parser().parse_args(argv)
        if os.name != "nt":
            raise OSError("WINDOWS_REQUIRED: WSL/원격 Linux가 아닌 Windows에서 실행하세요.")
        if sys.maxsize <= 2**32:
            raise OSError("X64_REQUIRED: 64비트 실행 파일/Python을 사용하세요.")
        result, status = run(args)
        emit(result)
        return status
    except (ValueError, OSError) as exc:
        message = str(exc)
        code = message.split(":", 1)[0] if re.match(r"^[A-Z_]+:", message) else type(exc).__name__
        emit({"status": "error", "error": {"code": code, "message": message}})
        return 2
    except KeyboardInterrupt:
        emit({"status": "cancelled"})
        return 130
    except Exception as exc:
        # Never dump memory, key material, chat content, or a traceback.
        emit({"status": "error", "error": {"code": "INTERNAL_ERROR",
              "message": "예상하지 못한 오류입니다.", "exception_type": type(exc).__name__}})
        return 3
