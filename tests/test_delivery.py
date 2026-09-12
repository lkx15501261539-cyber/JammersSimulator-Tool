"""Verify the distributed bytes, optional models and source-only isolation."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

import pytest

from tools.build_delivery import (MODEL_RUNTIME, PACKAGE, ROOT, V1_ARCHIVE, V2_ARCHIVE,
                                 _allowed, build_delivery, verify_delivery)


def test_source_delivery_is_reproducible_and_self_contained(tmp_path):
    first, second = tmp_path / 'first.zip', tmp_path / 'second.zip'
    result = build_delivery(first)
    repeated = build_delivery(second)
    assert result['sha256'] == repeated['sha256']
    manifest = verify_delivery(first)
    names = set(manifest['files'])
    assert {'启动界面.bat', 'start_q3_v2.bat', 'start_q4.bat',
            '启动界面.command', 'start_q4.command', 'Mac启动说明.txt',
            'start_q4_v2.command', 'start_q4_v2.bat', 'docs/Q4_V2.md',
            'model_sources/q4/q4.py', 'model_sources/q4/q4_v2.py',
            'model_sources/q4/opportunities.py', f'models/{V2_ARCHIVE}'} <= names
    assert manifest['q4_models'] == ['q4_cu', 'q4_opportunity_v2', 'q4_route_v3']
    assert not any(set(Path(name).parts) & {'.git', '.venv', '.cache', '__pycache__', 'runs'}
                   for name in names)
    with zipfile.ZipFile(first) as archive:
        for name in ('启动界面.command', 'start_q4.command', 'start_q4_v2.command'):
            assert archive.getinfo(f'{PACKAGE}/{name}').external_attr >> 16 & 0o111 == 0o111
        for name in ('start_q3_v2.bat', 'start_q4.bat', 'start_q4_v2.bat',
                     '启动界面.bat', f'models/{V2_ARCHIVE}'):
            assert archive.read(f'{PACKAGE}/{name}') == (ROOT / name).read_bytes()
        archive.extractall(tmp_path / 'unpacked')
    standalone = tmp_path / 'unpacked' / PACKAGE
    assert not (standalone / '.git').exists()
    third = build_delivery(tmp_path / 'from-unpacked.zip', source=standalone)
    assert third['sha256'] == result['sha256']
    (standalone / 'enhanced' / 'ui.py').write_text('modified', encoding='utf-8')
    with pytest.raises(ValueError, match='checksum mismatch'):
        build_delivery(tmp_path / 'changed.zip', source=standalone)


def test_optional_demo_uses_only_completed_q3_v2_replay_files(tmp_path):
    demo = tmp_path / 'demo'
    demo.mkdir()
    for name in ('events.jsonl', 'observations.jsonl'):
        (demo / name).write_text('{}\n', encoding='utf-8')
    for name in ('ground_truth.json', 'metrics.json', 'reconciliation.json'):
        (demo / name).write_text('{}', encoding='utf-8')
    (demo / 'scenario.json').write_text(json.dumps(
        {'baseline_model': 'hexagon_v2', 'completion': 'completed'}), encoding='utf-8')
    (demo / 'worker.log').write_text('private diagnostics', encoding='utf-8')
    (demo / 'baseline-original').mkdir()
    (demo / 'baseline-original' / 'session.json').write_text('private diagnostics', encoding='utf-8')
    output = tmp_path / 'demo.zip'
    result = build_delivery(output, demo_run=demo)
    assert result['contains_q3_v2_demo']
    names = set(verify_delivery(output)['files'])
    assert len([name for name in names if name.startswith('examples/q3-v2-demo/')]) == 6
    assert not any('worker.log' in name or 'baseline-original' in name for name in names)
    (demo / 'scenario.json').write_text(json.dumps(
        {'baseline_model': 'hexagon_v2', 'completion': 'error'}), encoding='utf-8')
    with pytest.raises(ValueError, match='completed Q3 v2.0'):
        build_delivery(tmp_path / 'invalid-demo.zip', demo_run=demo)


def test_optional_v1_keeps_original_zip_bytes_and_rejects_corruption(tmp_path):
    original = tmp_path / V1_ARCHIVE
    with zipfile.ZipFile(original, 'w') as archive:
        for prefix in ('hexagon_v1', 'spiral_v1'):
            raw = b'original runtime\n'
            for name in MODEL_RUNTIME:
                archive.writestr(f'{prefix}/{name}', raw)
            archive.writestr(f'{prefix}/SHA256SUMS.json', json.dumps(
                {name: hashlib.sha256(raw).hexdigest() for name in MODEL_RUNTIME}))
    output = tmp_path / 'v1.zip'
    assert build_delivery(output, v1_archive=original)['contains_v1']
    with zipfile.ZipFile(output) as archive:
        assert archive.read(f'{PACKAGE}/models/{V1_ARCHIVE}') == original.read_bytes()
    with zipfile.ZipFile(original, 'w') as archive:
        for name in MODEL_RUNTIME:
            archive.writestr(f'hexagon_v1/{name}', 'changed')
        archive.writestr('hexagon_v1/SHA256SUMS.json', json.dumps(
            {name: 'bad' for name in MODEL_RUNTIME}))
    with pytest.raises(ValueError, match='Model checksum mismatch'):
        build_delivery(tmp_path / 'corrupt-model.zip', v1_archive=original)


def test_delivery_verification_detects_changed_bytes_and_extra_entries(tmp_path):
    original = tmp_path / 'original.zip'
    build_delivery(original)
    for change in ('changed', 'extra'):
        corrupt = tmp_path / f'{change}.zip'
        with zipfile.ZipFile(original) as source, zipfile.ZipFile(corrupt, 'w') as target:
            for info in source.infolist():
                raw = source.read(info.filename)
                if change == 'changed' and info.filename == f'{PACKAGE}/start_q4.bat':
                    raw += b'changed'
                target.writestr(info, raw)
            if change == 'extra':
                target.writestr(f'{PACKAGE}/.env', 'PRIVATE=value')
        with pytest.raises(ValueError, match='checksum mismatch|inventory'):
            verify_delivery(corrupt)
    with pytest.raises(FileExistsError):
        build_delivery(original)


@pytest.mark.parametrize('name', ['../outside.py', '/absolute.py', 'enhanced/../secret.py',
                                  'enhanced\\secret.py', 'enhanced/.env', 'runs/scene.json',
                                  '.venv/lib/runtime.py', 'enhanced/__pycache__/cached.py',
                                  'models/unrelated.zip', 'personal.txt'])
def test_source_allowlist_excludes_private_and_escaping_paths(name):
    assert not _allowed(name)


def test_delivery_refuses_source_symlinks(tmp_path):
    output = tmp_path / 'source.zip'
    build_delivery(output)
    with zipfile.ZipFile(output) as archive:
        archive.extractall(tmp_path / 'unpacked')
    root = tmp_path / 'unpacked' / PACKAGE
    target = root / 'enhanced' / 'ui.py'
    copied = tmp_path / 'outside.py'
    shutil.copyfile(target, copied)
    target.unlink()
    target.symlink_to(copied)
    with pytest.raises(ValueError, match='Symbolic link'):
        build_delivery(tmp_path / 'symlink.zip', source=root)
