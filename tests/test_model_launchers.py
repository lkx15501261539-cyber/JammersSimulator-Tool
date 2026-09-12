"""Release probes, launcher contracts, and Windows-only failure-path checks."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_q4_release_files_match_recorded_content():
    model = ROOT / 'model_sources' / 'q4'
    hashes = json.loads((model / 'SHA256SUMS.json').read_text(encoding='utf-8'))
    assert set(hashes) == {'q4.py', 'cu.py', '第一问.py', 'LICENSE'}
    for name, expected in hashes.items():
        assert hashlib.sha256((model / name).read_bytes()).hexdigest() == expected


def test_q4_loads_from_standalone_simulator_without_paper_repository(tmp_path):
    # Copy the published runtime into an otherwise empty workspace. Its old
    # sibling CUMCM-2026 path does not exist, so a hidden dependency cannot pass.
    checkout = tmp_path / 'standalone-simulator'
    shutil.copytree(ROOT / 'enhanced', checkout / 'enhanced',
                    ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT / 'model_sources', checkout / 'model_sources',
                    ignore=shutil.ignore_patterns('__pycache__'))
    probe = """
from enhanced.q4_adapter import CODE, BUNDLED_CODE, _controller_module
assert CODE == BUNDLED_CODE
model = _controller_module()
points = model.search_points()
assert len(points) == 25
assert model.discovery_certificate(points)['passed']
print('standalone Q4 model and discovery certificate loaded')
"""
    result = subprocess.run([sys.executable, '-c', probe], cwd=checkout,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_q3_v2_launcher_installs_original_archive_dependency_ranges():
    archive = ROOT / 'models' / 'Baseline_v2.0_七点六边形.zip'
    with zipfile.ZipFile(archive) as z:
        name = next(n for n in z.namelist() if n.endswith('/requirements.txt'))
        original = set(z.read(name).decode('utf-8').splitlines())
    installed = set((ROOT / 'requirements-q3-v2.txt').read_text().splitlines())
    assert original <= installed
    assert 'PySide6>=6.6,<7' in installed


@pytest.mark.parametrize('name,launch', [
    ('start_q3_v2.bat', 'gui --model hexagon_v2'),
    ('start_q4.bat', 'gui --problem 4'),
])
def test_windows_launcher_contract(name, launch):
    raw = (ROOT / name).read_bytes()
    script = raw.decode('ascii')
    assert '\r\n' in script and b'\n' not in raw.replace(b'\r\n', b'')
    assert 'pushd "%~dp0"' in script
    assert 'DisableDelayedExpansion' in script
    assert 'set "PYTHONUTF8=1"' in script
    assert 'set "PYTHONIOENCODING=utf-8"' in script
    assert launch in script
    assert '..\\CUMCM-2026' not in script
    labels = set(re.findall(r'^:(\w+)', script, re.MULTILINE))
    assert set(re.findall(r'\bgoto (\w+)', script, re.IGNORECASE)) <= labels
    assert 'pause\r\npopd\r\nexit /b %_' in script
    assert 'set "_' in script and '=%errorlevel%"' in script
    # Double-clicking reaches the GUI. Only an explicit check flag bypasses it.
    ready = script.split('\r\n:ready\r\n', 1)[1]
    assert ready.startswith('if /i "%~1"=="--check-only" goto check_only\r\n\r\n:launch\r\n')
    assert 'if /i not "%~1"=="--check-only" pause' in script


def test_existing_desktop_shortcut_selects_v2_and_forwards_check_flag():
    script = (ROOT / '启动界面.bat').read_text(encoding='ascii')
    assert 'call "%~dp0start_q3_v2.bat" %*\nexit /b %errorlevel%' in script
    assert 'DisableDelayedExpansion' in script
    assert 'if /i not "%~1"=="--check-only" pause\nexit /b 1' in script


@pytest.mark.parametrize('name,section', [
    ('start_q3_v2.bat', 'check_only'),
    ('start_q4.bat', 'check_only'),
    ('start_q4.bat', 'check_only_v2'),
])
def test_launcher_probe_builds_selected_desktop_and_validates_model(name, section):
    # Run the exact probe embedded in the BAT on any platform. Execution of
    # cmd.exe and the normal visible Windows window are separate validation.
    script = (ROOT / name).read_text(encoding='ascii')
    block = script.split(f'\n:{section}\n', 1)[1].split('\n:success\n', 1)[0]
    command = next(line for line in block.splitlines()
                   if line.startswith('".venv\\Scripts\\python.exe" -c "'))
    code = command.split(' -c "', 1)[1][:-1]
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS:' in result.stdout


@pytest.mark.skipif(os.name != 'nt', reason='Requires Windows cmd.exe')
@pytest.mark.parametrize('name', ['start_q3_v2.bat', 'start_q4.bat',
                                  'start_q4_v2.bat', '启动界面.bat'])
def test_windows_missing_files_exits_without_waiting_for_input(tmp_path, name):
    folder = tmp_path / '中文 path with spaces'
    folder.mkdir()
    launcher = folder / name
    shutil.copyfile(ROOT / name, launcher)
    result = subprocess.run(['cmd.exe', '/d', '/c', 'call', str(launcher), '--check-only'],
                            cwd=tmp_path, capture_output=True, timeout=15)
    assert result.returncode == 1
    assert b'not found' in result.stdout


def test_q4_v2_windows_wrapper_reuses_environment_and_preserves_check_flag():
    raw = (ROOT / 'start_q4_v2.bat').read_bytes()
    assert b'\r\n' in raw and b'\n' not in raw.replace(b'\r\n', b'')
    script = raw.decode('ascii')
    assert 'call "%~dp0start_q4.bat" --v2 %*\r\nexit /b %errorlevel%' in script
    assert 'if /i not "%~1"=="--check-only" pause' in script
    shared = (ROOT / 'start_q4.bat').read_text(encoding='ascii')
    assert 'set "_q4_version=2"\n  shift' in shared
    assert 'gui --problem 4 --model q4_opportunity_v2' in shared
    assert "_controller_module(model='q4_opportunity_v2')" in shared


@pytest.mark.skipif(shutil.which('zsh') is None, reason='Requires zsh')
@pytest.mark.parametrize('name', ['启动界面.command', 'start_q4.command', 'start_q4_v2.command'])
def test_mac_launcher_syntax(name):
    result = subprocess.run(['zsh', '-n', str(ROOT / name)],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which('zsh') is None, reason='Requires zsh')
@pytest.mark.parametrize('name', ['start_q4.command', 'start_q4_v2.command'])
def test_mac_wrapper_missing_shared_launcher_returns_error_without_prompt(tmp_path, name):
    folder = tmp_path / '中文 path with spaces'
    folder.mkdir()
    target = folder / name
    shutil.copyfile(ROOT / name, target)
    result = subprocess.run(['zsh', str(target), '--check-only'], cwd=tmp_path,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 1
    assert '缺少共用启动文件' in result.stderr


@pytest.mark.skipif(shutil.which('zsh') is None, reason='Requires zsh')
def test_mac_q4_v2_probe_selects_exact_model():
    environment = os.environ.copy()
    environment['JAMMERS_PYTHON'] = sys.executable
    result = subprocess.run(['zsh', str(ROOT / 'start_q4_v2.command'), '--check-only'],
                            cwd=ROOT.parent, env=environment,
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS: q4_opportunity_v2 model and desktop window' in result.stdout


def test_windows_native_window_probe_distinguishes_q4_versions():
    from tools.windows_smoke import LAUNCHERS, matches_model_title
    launchers = dict(LAUNCHERS)
    assert set(launchers) == {'start_q3_v2.bat', 'start_q4.bat',
                              'start_q4_v2.bat', '启动界面.bat'}
    v1 = 'Jammers Lab · 第四问 Q4 · 25 点 C/U · v1.0'
    v2 = 'Jammers Lab · 第四问 Q4 · 左右机会复测 · v2.0'
    assert matches_model_title(v1, launchers['start_q4.bat'])
    assert matches_model_title(v2, launchers['start_q4_v2.bat'])
    assert not matches_model_title(v1, launchers['start_q4_v2.bat'])
    assert not matches_model_title(v2, launchers['start_q4.bat'])
    assert not matches_model_title('Terminal · '+v2, launchers['start_q4_v2.bat'])


def test_q4_windows_prepares_every_gui_models_dependencies():
    script = (ROOT / 'start_q4.bat').read_text(encoding='ascii')
    assert 'pip install -r "requirements-q3-v2.txt"' in script
    assert 'import numpy, numba, scipy, mpmath' in script
    assert 'r.specifier.contains(md.version(r.name))' in script
    # Keep the Windows environment in its existing checkout location.
    assert 'py -3 -m venv ".venv"' in script


def _mac_environment_fixture(tmp_path):
    folder = tmp_path / 'Desktop 中文 path' / 'simulator'
    folder.mkdir(parents=True)
    for name in ('启动界面.command', 'start_q4_v2.command', 'requirements-q3-v2.txt'):
        shutil.copyfile(ROOT / name, folder / name)
    for name in ('enhanced', 'model_sources'):
        shutil.copytree(ROOT / name, folder / name, ignore=shutil.ignore_patterns('__pycache__'))
    desktop_python = folder / '.venv' / 'bin' / 'python'
    desktop_python.parent.mkdir(parents=True)
    desktop_python.write_text('#!/bin/zsh\nexit 81\n', encoding='utf-8')
    desktop_python.chmod(0o755)
    shared_root = tmp_path / 'Library' / 'Application Support' / 'JammersLab' / 'venvs'
    shared_python = shared_root / 'python-3.12' / 'bin' / 'python'
    shared_python.parent.mkdir(parents=True)
    environment = os.environ.copy()
    environment.pop('JAMMERS_PYTHON', None)
    environment['JAMMERS_VENV_ROOT'] = str(shared_root)
    return folder, shared_python, desktop_python, environment


@pytest.mark.skipif(shutil.which('zsh') is None or sys.version_info[:2] != (3, 12),
                    reason='Uses zsh and the preferred Python 3.12 environment')
@pytest.mark.parametrize('certificate', [None, '/explicit/user-ca.pem'])
def test_mac_prefers_shared_environment_and_preserves_checkout_venv(tmp_path, certificate):
    folder, shared_python, desktop_python, environment = _mac_environment_fixture(tmp_path)
    certificate_record = tmp_path / 'pip-certificate.txt'
    environment.pop('PIP_CERT', None)
    if certificate is not None:
        environment['PIP_CERT'] = certificate
    # Forward to this test's prepared environment, keeping all imports real.
    shared_python.write_text(
        f'#!/bin/zsh\nprint -r -- "${{PIP_CERT-unset}}" > {shlex.quote(str(certificate_record))}\n'
        f'exec {shlex.quote(sys.executable)} "$@"\n', encoding='utf-8')
    shared_python.chmod(0o755)
    original = desktop_python.read_bytes()
    result = subprocess.run(['zsh', str(folder / 'start_q4_v2.command'), '--check-only'],
                            cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert str(shared_python) in result.stdout
    assert 'PASS: q4_opportunity_v2' in result.stdout
    assert desktop_python.read_bytes() == original
    expected = certificate or ('/etc/ssl/cert.pem' if Path('/etc/ssl/cert.pem').is_file() else 'unset')
    assert certificate_record.read_text(encoding='utf-8').strip() == expected


@pytest.mark.skipif(shutil.which('zsh') is None, reason='Requires zsh')
def test_mac_invalid_shared_environment_is_preserved_and_not_replaced(tmp_path):
    folder, shared_python, desktop_python, environment = _mac_environment_fixture(tmp_path)
    shared_python.write_text('#!/bin/zsh\nexit 82\n', encoding='utf-8')
    shared_python.chmod(0o755)
    original = shared_python.read_bytes()
    result = subprocess.run(['zsh', str(folder / 'start_q4_v2.command'), '--check-only'],
                            cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    assert '共用 Python 3.12 环境不兼容或不完整' in result.stderr
    assert shared_python.read_bytes() == original
    assert desktop_python.exists()


@pytest.mark.skipif(shutil.which('zsh') is None, reason='Requires zsh')
def test_mac_explicit_python_override_still_wins_over_broken_shared_environment(tmp_path):
    folder, shared_python, _, environment = _mac_environment_fixture(tmp_path)
    shared_python.write_text('#!/bin/zsh\nexit 82\n', encoding='utf-8')
    shared_python.chmod(0o755)
    environment['JAMMERS_PYTHON'] = sys.executable
    result = subprocess.run(['zsh', str(folder / 'start_q4_v2.command'), '--check-only'],
                            cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert sys.executable in result.stdout
    assert 'PASS: q4_opportunity_v2' in result.stdout
