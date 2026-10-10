"""Per-user account discovery and DPAPI-protected key cache; never shipped in releases."""
from __future__ import annotations
import ctypes
import hashlib
import json
import os
import pathlib
import secrets

def local_appdata() -> pathlib.Path:
    value = os.environ.get("LOCALAPPDATA")
    if not value:
        raise OSError("Windows LOCALAPPDATA is unavailable. Run natively on Windows.")
    return pathlib.Path(value)

def default_kakao_root() -> pathlib.Path:
    return local_appdata() / "Kakao" / "KakaoTalk" / "users"

def default_state_dir() -> pathlib.Path:
    return local_appdata() / "KakaoWindowsReader"

def accounts(root: pathlib.Path) -> list[dict]:
    if not root.is_dir():
        return []
    found = []
    for p in sorted(root.iterdir()):
        if not p.is_dir() or p.is_symlink():
            continue
        # Login creates an account folder containing metadata and/or chat_data.
        if not (p / "chat_data").is_dir() and not any(p.glob("*.edb")):
            continue
        found.append({"account_id": p.name, "chat_database_count": len(list(p.glob("chat_data/chatLogs_*.edb")))})
    return found

def choose_account(root: pathlib.Path, account_id: str | None) -> pathlib.Path:
    available = accounts(root)
    if not available:
        raise ValueError("NO_ACCOUNT: 카카오톡 PC에 로그인한 뒤 다시 실행하세요.")
    if account_id is None:
        if len(available) != 1:
            raise ValueError("ACCOUNT_REQUIRED: 계정 폴더가 여러 개입니다. accounts 결과에서 --account를 지정하세요.")
        account_id = available[0]["account_id"]
    if account_id not in {a["account_id"] for a in available}:
        raise ValueError("ACCOUNT_NOT_FOUND: accounts에 나온 정확한 account_id를 사용하세요.")
    result = (root / account_id).resolve()
    if result.parent != root.resolve():
        raise ValueError("ACCOUNT_OUTSIDE_ROOT")
    return result

def _dpapi(data: bytes, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise OSError("DPAPI is only available on native Windows.")
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    fn = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    # CRYPTPROTECT_UI_FORBIDDEN; no LOCAL_MACHINE scope.
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise OSError("KEY_CACHE_PROTECTION_FAILED: Windows 사용자 보호 키를 사용할 수 없습니다.")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)

def cache_path(state_dir: pathlib.Path, account: pathlib.Path) -> pathlib.Path:
    fingerprint = hashlib.sha256(str(account.resolve()).casefold().encode("utf-8")).hexdigest()
    return state_dir / "keys" / (fingerprint + ".dpapi")

def load_keys(state_dir: pathlib.Path, account: pathlib.Path) -> dict[str, str]:
    path = cache_path(state_dir, account)
    if not path.exists():
        return {}
    try:
        obj = json.loads(_dpapi(path.read_bytes(), decrypt=True))
        if obj.get("account_id") != account.name or obj.get("format") != 1:
            raise ValueError("account mismatch")
        keys = obj["keys"]
        if not isinstance(keys, dict):
            raise ValueError("key map")
        for rel, key in keys.items():
            target = (account / rel).resolve()
            if not target.is_relative_to(account.resolve()) or len(bytes.fromhex(key)) != 32:
                raise ValueError("invalid cached key")
        return keys
    except Exception as exc:
        raise ValueError("KEY_CACHE_UNREADABLE: 다른 Windows 사용자/손상된 캐시입니다. recover --reset-cache로 다시 찾으세요.") from exc

def save_keys(state_dir: pathlib.Path, account: pathlib.Path, keys: dict[str, str]) -> None:
    payload = json.dumps({"format": 1, "account_id": account.name, "keys": keys}).encode("utf-8")
    encrypted = _dpapi(payload)
    path = cache_path(state_dir, account)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + secrets.token_hex(6) + ".tmp")
    try:
        temp.write_bytes(encrypted)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
