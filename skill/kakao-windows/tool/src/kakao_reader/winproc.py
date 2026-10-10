"""Minimal read-only Windows process access, loaded only when called.

Adapted from https://github.com/is-theo/kakao-cli-win (MIT),
Copyright (c) 2026 theo; see THIRD_PARTY_NOTICES.md. Pointer-sized Win32 prototypes and exact
executable checks are explicit here. A 64-bit Windows Python host is required.
No process write, injection, suspend, clipboard or messaging APIs are used.
"""
from __future__ import annotations

from pathlib import PureWindowsPath
import struct
import sys


def _windows_api():
    if sys.platform != "win32":
        raise OSError("Windows is required for KakaoTalk process access")
    if struct.calcsize("P") != 8:
        raise OSError("A 64-bit Python runtime is required")
    import ctypes
    from ctypes import wintypes as wt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
            ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
            ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", wt.LONG),
            ("dwFlags", wt.DWORD), ("szExeFile", wt.WCHAR * 260),
        ]

    class MemoryBasicInformation(ctypes.Structure):
        _fields_ = [
            ("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
            ("AllocationProtect", wt.DWORD), ("_alignment1", wt.DWORD),
            ("RegionSize", ctypes.c_size_t), ("State", wt.DWORD),
            ("Protect", wt.DWORD), ("Type", wt.DWORD), ("_alignment2", wt.DWORD),
        ]

    def prototype(name, args, result):
        function = getattr(kernel, name)
        function.argtypes = args
        function.restype = result
        return function

    prototype("CreateToolhelp32Snapshot", [wt.DWORD, wt.DWORD], wt.HANDLE)
    prototype("Process32FirstW", [wt.HANDLE, ctypes.POINTER(ProcessEntry)], wt.BOOL)
    prototype("Process32NextW", [wt.HANDLE, ctypes.POINTER(ProcessEntry)], wt.BOOL)
    prototype("OpenProcess", [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE)
    prototype("CloseHandle", [wt.HANDLE], wt.BOOL)
    prototype("QueryFullProcessImageNameW", [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)], wt.BOOL)
    prototype("VirtualQueryEx", [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MemoryBasicInformation), ctypes.c_size_t], ctypes.c_size_t)
    prototype("ReadProcessMemory", [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)], wt.BOOL)
    if hasattr(kernel, "IsWow64Process2"):
        prototype("IsWow64Process2", [wt.HANDLE, ctypes.POINTER(wt.WORD), ctypes.POINTER(wt.WORD)], wt.BOOL)
    else:
        prototype("IsWow64Process", [wt.HANDLE, ctypes.POINTER(wt.BOOL)], wt.BOOL)
    return ctypes, wt, kernel, ProcessEntry, MemoryBasicInformation


def _process_bitness(handle, api) -> int:
    ctypes, wt, kernel, _, _ = api
    if hasattr(kernel, "IsWow64Process2"):
        process_machine, native_machine = wt.WORD(), wt.WORD()
        if not kernel.IsWow64Process2(handle, ctypes.byref(process_machine), ctypes.byref(native_machine)):
            return 0
        machine = process_machine.value or native_machine.value
        return {0x014C: 32, 0x8664: 64}.get(machine, 0)
    wow64 = wt.BOOL()
    if not kernel.IsWow64Process(handle, ctypes.byref(wow64)):
        return 0
    return 32 if wow64.value else 64


def _executable(handle, api) -> str:
    ctypes, wt, kernel, _, _ = api
    buffer = ctypes.create_unicode_buffer(32768)
    size = wt.DWORD(len(buffer))
    if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
        raise OSError("Unable to verify target executable")
    return PureWindowsPath(buffer.value).name


def list_processes() -> list[dict]:
    """Return running KakaoTalk.exe entries only; bitness 0 means undetermined.

    Executable paths and other processes' details are never returned. Listing
    uses process metadata only, without reading a process's memory.
    """
    api = _windows_api()
    ctypes, _, kernel, ProcessEntry, _ = api
    snapshot = kernel.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value or not snapshot:
        raise OSError("Unable to enumerate processes")
    results = []
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        present = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while present:
            if entry.szExeFile.lower() == "kakaotalk.exe":
                handle = kernel.OpenProcess(0x1000, False, entry.th32ProcessID)
                bitness = 0
                if handle:
                    try:
                        if _executable(handle, api).lower() == "kakaotalk.exe":
                            bitness = _process_bitness(handle, api)
                    except OSError:
                        pass
                    finally:
                        kernel.CloseHandle(handle)
                results.append({"pid": int(entry.th32ProcessID), "exe": "KakaoTalk.exe", "bitness": bitness})
            present = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return results


def read_process_regions(pid: int, expected_bitness: int) -> list[tuple[int, bytes]]:
    """Read committed readable regions of the exact KakaoTalk executable.

    Guard pages and regions larger than 128 MiB are skipped. The total retained
    memory is capped at 1 GiB; exceeding it fails rather than silently scanning
    an arbitrary prefix. Unreadable bytes remain zero-filled to preserve offsets.
    Keys can be unavailable when a database is closed or the app layout changed.
    """
    if not isinstance(pid, int) or pid <= 0 or expected_bitness not in (32, 64):
        raise ValueError("invalid_process_request")
    api = _windows_api()
    ctypes, _, kernel, _, MemoryBasicInformation = api
    handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
    if not handle:
        raise OSError("Unable to open KakaoTalk for read-only memory access")
    try:
        if _executable(handle, api).lower() != "kakaotalk.exe":
            raise OSError("Refusing memory access to a different executable")
        if _process_bitness(handle, api) != expected_bitness:
            raise OSError("Target architecture changed or could not be verified")
        address = 0
        maximum = 0x100000000 if expected_bitness == 32 else 0x800000000000
        regions = []
        retained = 0
        chunk_size = 1024 * 1024
        buffer = ctypes.create_string_buffer(chunk_size)
        readable = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}
        while address < maximum:
            information = MemoryBasicInformation()
            if not kernel.VirtualQueryEx(handle, ctypes.c_void_p(address), ctypes.byref(information), ctypes.sizeof(information)):
                break
            base = int(information.BaseAddress or 0)
            size = int(information.RegionSize)
            following = base + size
            if size <= 0 or following <= address:
                break
            protection = int(information.Protect)
            if (information.State == 0x1000 and protection & 0xFF in readable
                    and not protection & 0x100 and size <= 128 * 1024 * 1024):
                if retained + size > 1024 * 1024 * 1024:
                    raise OSError("Process memory scan exceeds the 1 GiB limit")
                data = bytearray(size)
                for offset in range(0, size, chunk_size):
                    count = min(chunk_size, size - offset)
                    got = ctypes.c_size_t()
                    kernel.ReadProcessMemory(handle, ctypes.c_void_p(base + offset), buffer, count, ctypes.byref(got))
                    actual = min(count, got.value)
                    if actual:
                        data[offset:offset + actual] = buffer.raw[:actual]
                regions.append((base, bytes(data)))
                retained += size
            address = following
        return regions
    finally:
        kernel.CloseHandle(handle)
