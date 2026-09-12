"""Standalone model release checks; Windows launchers are checked statically."""
import hashlib
import json
from pathlib import Path
import re
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
    assert launch in script
    assert '..\\CUMCM-2026' not in script
    labels = set(re.findall(r'^:(\w+)', script, re.MULTILINE))
    assert set(re.findall(r'\bgoto (\w+)', script, re.IGNORECASE)) <= labels
    assert 'pause\r\npopd\r\nexit /b %_' in script
    assert 'set "_' in script and '=%errorlevel%"' in script
