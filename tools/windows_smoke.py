"""Test the unmodified BAT launch path on a Windows test machine.

Run with the simulator's prepared .venv. The visible-window probe launches the
normal BAT with no arguments, checks only windows owned by that process tree,
and closes the matching application window normally. It does not click controls,
inject code, or substitute an offscreen platform. CI evidence is not a claim that
every Windows version or a user's particular installation has been tested.
"""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]


def windows_api():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    user = ctypes.WinDLL('user32', use_last_error=True)

    class ProcessEntry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                    ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                    ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]

    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ('Process32FirstW', 'Process32NextW'):
        function = getattr(kernel, name)
        function.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        function.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumWindows.restype = wintypes.BOOL
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsWindowVisible.restype = wintypes.BOOL
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowTextW.restype = ctypes.c_int
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.GetWindowRect.restype = wintypes.BOOL
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.PostMessageW.restype = wintypes.BOOL
    return kernel, user, ProcessEntry, callback_type


def descendants(kernel, entry_type, root_pid):
    snapshot = kernel.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    parents = {}
    try:
        entry = entry_type()
        entry.dwSize = ctypes.sizeof(entry)
        available = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while available:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            available = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    result = {root_pid}
    while True:
        updated = result | {pid for pid, parent in parents.items() if parent in result}
        if updated == result:
            return result
        result = updated


def visible_windows(user, callback_type, pids, expected):
    found = []

    @callback_type
    def visit(handle, _):
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if pid.value not in pids or not user.IsWindowVisible(handle):
            return True
        title = ctypes.create_unicode_buffer(1024)
        user.GetWindowTextW(handle, title, len(title))
        if title.value.startswith('Jammers Lab') and expected in title.value:
            rect = wintypes.RECT()
            if user.GetWindowRect(handle, ctypes.byref(rect)) and rect.right > rect.left and rect.bottom > rect.top:
                found.append(dict(handle=int(handle), pid=pid.value, title=title.value,
                                  visible=True, rect=[rect.left, rect.top, rect.right, rect.bottom]))
        return True

    if not user.EnumWindows(visit, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return found


def command(launcher, check_only=False):
    # Use an explicit CALL and cmd's documented surrounding quote pair. Paths
    # remain quoted even when the checkout directory contains spaces or Chinese.
    arguments = ' --check-only' if check_only else ''
    return f'cmd.exe /d /s /c "call "{ROOT / launcher}"{arguments}"'


def probe(launcher, expected, api, evidence_dir):
    kernel, user, entry_type, callback_type = api
    environment = os.environ.copy()
    environment.pop('QT_QPA_PLATFORM', None)
    environment['PYTHONUTF8'] = '1'
    environment['PYTHONIOENCODING'] = 'utf-8'
    result = subprocess.run(command(launcher, True), cwd=ROOT, env=environment,
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
    (evidence_dir / f'{launcher}.check.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    if result.returncode != 0 or 'PASS:' not in result.stdout:
        raise RuntimeError(f'{launcher} --check-only failed ({result.returncode}); see check log')

    with (evidence_dir / f'{launcher}.visible.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command(launcher), cwd=ROOT, env=environment,
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f'{launcher} exited before a visible model window appeared')
                windows = visible_windows(user, callback_type,
                                          descendants(kernel, entry_type, process.pid), expected)
                if windows:
                    window = windows[0]
                    break
                time.sleep(0.25)
            else:
                raise TimeoutError(f'{launcher}: no visible native window within 90 seconds')
            # Normal application close only, restricted to this test's window.
            if not user.PostMessageW(window['handle'], 0x0010, 0, 0):  # WM_CLOSE
                raise ctypes.WinError(ctypes.get_last_error())
            code = process.wait(timeout=30)
            if code:
                raise RuntimeError(f'{launcher} did not exit successfully after closing its window: {code}')
            return dict(launcher=launcher, check_only_exit=result.returncode,
                        visible_window=window, normal_exit=code, passed=True)
        finally:
            if process.poll() is None:
                # CI failure cleanup is scoped to the process started above.
                subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                               capture_output=True, timeout=15)
                process.wait(timeout=15)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts' / 'windows-launchers.json')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('This probe must run on Windows; it cannot validate Windows from another OS.')
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report = dict(platform=platform.platform(), python=sys.version, executable=sys.executable,
                  commit=os.environ.get('GITHUB_SHA'), runner=os.environ.get('RUNNER_OS'),
                  validation='Unmodified BAT, native visible HWND, normal close', checks=[], passed=False)
    try:
        api = windows_api()
        for launcher, expected in [('start_q3_v2.bat', 'Baseline 2.0'), ('start_q4.bat', 'Q4'), ('启动界面.bat', 'Baseline 2.0')]:
            report['checks'].append(probe(launcher, expected, api, output.parent))
        report['passed'] = True
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
