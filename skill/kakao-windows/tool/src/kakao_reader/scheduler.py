"""Opt-in hourly Windows Task Scheduler integration for a logged-in user.

This module performs no work at import time and never prints scheduler output.
Tasks use the current user's interactive token, least privilege and no password.
Native scheduling calls occur only inside install/remove/status functions.
Schema: https://learn.microsoft.com/en-us/windows/win32/taskschd/task-scheduler-schema
"""
from __future__ import annotations

import datetime as dt
import hashlib
import locale
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

_NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"
_PREFIX = "KakaoWindowsReader-"
_DESCRIPTION = "KakaoWindowsReader hourly local scan; state-sha256="


class SchedulerError(ValueError):
    """The requested task could not be safely installed, removed, or inspected."""


def _identity(state_dir: Path) -> tuple[str, str]:
    state = str(Path(state_dir).resolve()).casefold()
    digest = hashlib.sha256(state.encode("utf-8")).hexdigest()
    return _PREFIX + digest[:12], _DESCRIPTION + digest


def _current_user_sid() -> str:
    if sys.platform != "win32":
        raise OSError("Windows is required for Task Scheduler")
    import ctypes
    from ctypes import wintypes as wt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wt.HANDLE
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    kernel.CloseHandle.restype = wt.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    security.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    security.OpenProcessToken.restype = wt.BOOL
    security.GetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]
    security.GetTokenInformation.restype = wt.BOOL
    security.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.LPWSTR)]
    security.ConvertSidToStringSidW.restype = wt.BOOL
    token = wt.HANDLE()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise SchedulerError("unable_to_read_current_user_token")
    try:
        required = wt.DWORD()
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(required))
        if not required.value or required.value > 65536:
            raise SchedulerError("unable_to_read_current_user_sid")
        buffer = ctypes.create_string_buffer(required.value)
        if not security.GetTokenInformation(token, 1, buffer, required, ctypes.byref(required)):
            raise SchedulerError("unable_to_read_current_user_sid")
        sid_pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        rendered = wt.LPWSTR()
        if not security.ConvertSidToStringSidW(sid_pointer, ctypes.byref(rendered)):
            raise SchedulerError("unable_to_format_current_user_sid")
        try:
            return rendered.value
        finally:
            kernel.LocalFree(ctypes.cast(rendered, ctypes.c_void_p))
    finally:
        kernel.CloseHandle(token)


def _local_start_boundary() -> str:
    # TimeTrigger without a Duration or EndBoundary repeats indefinitely.
    start = dt.datetime.now().replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    return start.isoformat(timespec="seconds")


def task_spec(python_exe: Path, entrypoint: Path, state_dir: Path) -> dict:
    """Build a reviewable task specification without invoking Task Scheduler."""
    python = Path(python_exe).resolve(strict=True)
    script = Path(entrypoint).resolve(strict=True)
    if not python.is_file() or not script.is_file():
        raise SchedulerError("python_and_entrypoint_must_be_files")
    windowless = python.with_name("pythonw.exe")
    if windowless.is_file():
        python = windowless.resolve()
    state = Path(state_dir).resolve()
    name, description = _identity(state)
    sid = _current_user_sid()
    arguments = subprocess.list2cmdline([str(script), "scan", "--state-dir", str(state)])
    root = ET.Element("Task", {"version": "1.4", "xmlns": _NAMESPACE})

    def node(parent, tag, value=None, **attributes):
        element = ET.SubElement(parent, tag, attributes)
        if value is not None:
            element.text = str(value)
        return element

    registration = node(root, "RegistrationInfo")
    node(registration, "Description", description)
    node(registration, "URI", "\\" + name)
    triggers = node(root, "Triggers")
    trigger = node(triggers, "TimeTrigger")
    node(trigger, "Enabled", "true")
    node(trigger, "StartBoundary", _local_start_boundary())
    repetition = node(trigger, "Repetition")
    node(repetition, "Interval", "PT1H")
    node(repetition, "StopAtDurationEnd", "false")
    principals = node(root, "Principals")
    principal = node(principals, "Principal", id="CurrentUser")
    node(principal, "UserId", sid)
    node(principal, "LogonType", "InteractiveToken")
    node(principal, "RunLevel", "LeastPrivilege")
    settings = node(root, "Settings")
    for key, value in (
        ("MultipleInstancesPolicy", "IgnoreNew"),
        ("DisallowStartIfOnBatteries", "false"),
        ("StopIfGoingOnBatteries", "false"),
        ("AllowHardTerminate", "true"),
        ("StartWhenAvailable", "true"),
        ("RunOnlyIfNetworkAvailable", "false"),
        ("AllowStartOnDemand", "true"),
        ("Enabled", "true"),
        ("Hidden", "true"),
        ("RunOnlyIfIdle", "false"),
        ("WakeToRun", "false"),
        ("ExecutionTimeLimit", "PT50M"),
        ("Priority", "7"),
    ):
        node(settings, key, value)
    actions = node(root, "Actions", Context="CurrentUser")
    execution = node(actions, "Exec")
    node(execution, "Command", python)
    node(execution, "Arguments", arguments)
    node(execution, "WorkingDirectory", script.parent)
    xml = '<?xml version="1.0" encoding="UTF-16"?>\n' + ET.tostring(root, encoding="unicode")
    return {
        "task_name": name, "xml": xml, "command": str(python),
        "arguments": arguments, "working_directory": str(script.parent),
        "windowless": python.name.lower() == "pythonw.exe",
        "schedule": "hourly_while_user_logged_in", "privilege": "least",
    }


def _system_scheduler_path() -> Path:
    if sys.platform != "win32":
        raise OSError("Windows is required for Task Scheduler")
    import ctypes
    from ctypes import wintypes as wt
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetSystemDirectoryW.argtypes = [wt.LPWSTR, wt.UINT]
    kernel.GetSystemDirectoryW.restype = wt.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    count = kernel.GetSystemDirectoryW(buffer, len(buffer))
    if not count or count >= len(buffer):
        raise SchedulerError("unable_to_locate_system_scheduler")
    return Path(buffer.value) / "schtasks.exe"


def _decode_output(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    if data[:4] in (b"<\x00?\x00", b"<\x00T\x00"):
        return data.decode("utf-16-le", errors="replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode(locale.getpreferredencoding(False), errors="replace")


def _run_schtasks(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Invoke the system binary directly and capture all output without a window."""
    command = [str(_system_scheduler_path()), *args]
    try:
        result = subprocess.run(
            command, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SchedulerError("task_scheduler_invocation_failed") from None
    return subprocess.CompletedProcess(command, result.returncode, _decode_output(result.stdout), _decode_output(result.stderr))


def _owned_task(xml: str, state_dir: Path, sid: str) -> bool:
    _, description = _identity(state_dir)
    try:
        root = ET.fromstring(xml)
    except (ET.ParseError, ValueError):
        return False
    namespace = {"t": _NAMESPACE}
    return (
        root.tag == "{" + _NAMESPACE + "}Task"
        and root.findtext("t:RegistrationInfo/t:Description", namespaces=namespace) == description
        and root.findtext("t:Principals/t:Principal/t:UserId", namespaces=namespace) == sid
    )


def status_hourly(state_dir: Path) -> dict:
    """Inspect only this state directory's task; never return scheduler output."""
    name, _ = _identity(state_dir)
    result = _run_schtasks(["/Query", "/TN", name, "/XML"])
    if result.returncode:
        return {"task_name": name, "installed": False, "owned": False,
                "status": "missing_or_unavailable", "query_returncode": result.returncode}
    owned = _owned_task(result.stdout, state_dir, _current_user_sid())
    return {"task_name": name, "installed": True, "owned": owned,
            "status": "installed" if owned else "unrecognized_task", "query_returncode": 0}


def install_hourly(python_exe: Path, entrypoint: Path, state_dir: Path) -> dict:
    """Register an hourly task, replacing an existing task only after ownership checks."""
    spec = task_spec(python_exe, entrypoint, state_dir)
    current = status_hourly(state_dir)
    if current["installed"] and not current["owned"]:
        raise SchedulerError("task_name_exists_without_matching_owner")
    with tempfile.TemporaryDirectory(prefix="kakao-reader-task-") as directory:
        xml_path = Path(directory) / "hourly-task.xml"
        xml_path.write_text(spec["xml"], encoding="utf-16")
        args = ["/Create", "/TN", spec["task_name"], "/XML", str(xml_path)]
        if current["owned"]:
            args.append("/F")
        result = _run_schtasks(args)
    if result.returncode:
        raise SchedulerError("task_registration_failed:" + str(result.returncode))
    return {"task_name": spec["task_name"], "installed": True, "owned": True,
            "schedule": spec["schedule"], "windowless": spec["windowless"],
            "status": "installed", "requires_user_login": True}


def remove_hourly(state_dir: Path) -> dict:
    """Delete only a task whose name, state marker and current-user SID match."""
    current = status_hourly(state_dir)
    if not current["installed"]:
        return {**current, "removed": False}
    if not current["owned"]:
        raise SchedulerError("task_name_exists_without_matching_owner")
    result = _run_schtasks(["/Delete", "/TN", current["task_name"], "/F"])
    if result.returncode:
        raise SchedulerError("task_removal_failed:" + str(result.returncode))
    return {"task_name": current["task_name"], "installed": False, "owned": False,
            "removed": True, "status": "removed"}
