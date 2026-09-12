"""Build and verify a portable source delivery; never bundle a developer environment."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'JammersLab_Q3v2_Q4'
MANIFEST = 'DELIVERY_MANIFEST.json'
V1_ARCHIVE = 'Baseline_v1.0_两模型完整交付.zip'
V2_ARCHIVE = 'Baseline_v2.0_七点六边形.zip'
# These files must also be included before their first Git commit.
DELIVERY_FILES = {'tools/build_delivery.py', 'tests/test_delivery.py',
                  'docs/WINDOWS_DELIVERY.md', 'Windows启动说明.txt',
                  'start_q4.command', 'Mac启动说明.txt',
                  'start_q4_v2.command', 'start_q4_v2.bat', 'docs/Q4_V2.md',
                  'model_sources/q4/q4_v2.py', 'model_sources/q4/opportunities.py',
                  'tests/test_q4_v2.py', 'tests/test_q4_opportunities.py',
                  'model_sources/q4/route_opportunistic_remeasure.py',
                  'tests/test_route_geometry.py', 'tests/test_route_controller.py',
                  'docs/Q4_ROUTE_V3.md'}
ROOT_FILES = {'README.md', 'ENHANCED.md', 'COMPATIBILITY.md', 'LICENSE',
              'cli.py', 'mcp_server.py', 'mock_simulator.py', 'simulator_client.py',
              'requirements.txt', 'requirements-enhanced.txt', 'requirements-q3-v2.txt',
              'start_q3_v2.bat', 'start_q4.bat', '启动界面.bat', '启动界面.command',
              'start_q4.command', 'start_q4_v2.command', 'start_q4_v2.bat', 'Mac启动说明.txt',
              'Windows启动说明.txt', '.gitattributes', '.gitignore'}
SOURCE_DIRS = {'enhanced', 'assets', 'docs', 'examples', 'model_sources',
               'references', 'skills', 'tests', 'tools'}
SOURCE_SUFFIXES = {'.py', '.md', '.txt', '.json', '.jsonl', '.csv', '.png', '.jpg', '.svg'}
EXCLUDED_PARTS = {'.git', '.venv', '.cache', '.pytest_cache', '__pycache__',
                  'runs', 'artifacts', 'node_modules', '.env'}
REPLAY_FILES = ('events.jsonl', 'ground_truth.json', 'metrics.json',
                'observations.jsonl', 'scenario.json')
MODEL_RUNTIME = {'run.py', 'solver.py', '第一问.py', 'config.json', 'VERSION.json',
                 'requirements.txt', 'LICENSE'}
REQUIRED = {'enhanced/__main__.py', 'enhanced/ui.py', 'enhanced/baseline.py',
            'enhanced/q4_adapter.py', 'model_sources/q4/q4.py',
            'model_sources/q4/cu.py', 'model_sources/q4/第一问.py',
            f'models/{V2_ARCHIVE}', 'requirements-q3-v2.txt',
            'start_q3_v2.bat', 'start_q4.bat', '启动界面.bat', '启动界面.command'} | DELIVERY_FILES


def _allowed(name):
    path = PurePosixPath(name)
    if (path.is_absolute() or '..' in path.parts or '\\' in name
            or any(p in EXCLUDED_PARTS for p in path.parts)):
        return False
    if name in ROOT_FILES:
        return True
    if name in {f'models/{V1_ARCHIVE}', f'models/{V2_ARCHIVE}'}:
        return True
    return (len(path.parts) > 1 and path.parts[0] in SOURCE_DIRS
            and (path.suffix in SOURCE_SUFFIXES or path.name == 'LICENSE')
            and not any(p.startswith('.') for p in path.parts))


def _source_names(root):
    """Use tracked files, or the verified inventory supplied in a source delivery."""
    if (root / MANIFEST).is_file() and not (root / '.git').exists():
        previous = json.loads((root / MANIFEST).read_text(encoding='utf-8'))
        names = set(previous['files'])
        for name, info in previous['files'].items():
            if not _allowed(name):
                raise ValueError(f'Unexpected delivery path: {name}')
            data = _read_source(root, name)
            if len(data) != info['size'] or hashlib.sha256(data).hexdigest() != info['sha256']:
                raise ValueError(f'Delivery source checksum mismatch: {name}')
    else:
        result = subprocess.run(['git', '-C', str(root), 'ls-files', '-z'],
                                capture_output=True, check=False)
        if result.returncode:
            raise ValueError('Source must be a Git checkout or an intact delivery with its manifest')
        names = set(result.stdout.decode('utf-8').split('\0')) - {''}
    return {name for name in names | DELIVERY_FILES if _allowed(name)}


def _read_source(root, name):
    path = root.joinpath(*PurePosixPath(name).parts)
    # Refuse links even when they happen to point inside the checkout.
    if any(p.is_symlink() for p in (path, *path.parents) if p != root.parent):
        raise ValueError(f'Symbolic link cannot enter delivery: {name}')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Path escapes delivery source: {name}')
    return path.read_bytes()


def _verify_model_archive(path, prefixes):
    """Keep original ZIP bytes, but require a complete, intact model manifest."""
    raw = path.read_bytes()
    with zipfile.ZipFile(path) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError(f'Duplicate entries in model archive: {path.name}')
        for prefix in prefixes:
            hashes = json.loads(archive.read(f'{prefix}/SHA256SUMS.json'))
            required = MODEL_RUNTIME | ({'baseline_runtime.py', 'cu.py', 'routing.py',
                                         'requirements-lock.txt'} if prefix.startswith('Baseline_v2.') else set())
            if not required <= set(hashes):
                raise ValueError(f'Model runtime missing from manifest: {prefix}')
            for name, expected in hashes.items():
                rel = PurePosixPath(name)
                if rel.is_absolute() or '..' in rel.parts or '\\' in name:
                    raise ValueError(f'Invalid model archive entry: {name}')
                if hashlib.sha256(archive.read(f'{prefix}/{name}')).hexdigest() != expected:
                    raise ValueError(f'Model checksum mismatch: {prefix}/{name}')
    return raw


def verify_delivery(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate entries in delivery')
        manifest = json.loads(archive.read(f'{PACKAGE}/{MANIFEST}'))
        expected = {f'{PACKAGE}/{name}' for name in manifest['files']}
        if set(names) != expected | {f'{PACKAGE}/{MANIFEST}'}:
            raise ValueError('Delivery file inventory does not match manifest')
        for name, info in manifest['files'].items():
            if not _allowed(name):
                raise ValueError(f'Unexpected delivery path: {name}')
            data = archive.read(f'{PACKAGE}/{name}')
            if len(data) != info['size'] or hashlib.sha256(data).hexdigest() != info['sha256']:
                raise ValueError(f'Delivery checksum mismatch: {name}')
    return manifest


def build_delivery(output, *, source=ROOT, v1_archive=None, demo_run=None):
    root = Path(source).resolve()
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f'Output already exists: {output}')
    names = _source_names(root)
    missing = REQUIRED - names
    if missing:
        raise ValueError(f'Delivery runtime files missing: {sorted(missing)}')
    contents = {name: _read_source(root, name) for name in sorted(names)}
    contents[f'models/{V2_ARCHIVE}'] = _verify_model_archive(
        root / 'models' / V2_ARCHIVE, ('Baseline_v2.0_七点六边形',))
    if v1_archive is not None:
        contents[f'models/{V1_ARCHIVE}'] = _verify_model_archive(
            Path(v1_archive), ('hexagon_v1', 'spiral_v1'))
    if demo_run is not None:
        demo = Path(demo_run).resolve()
        for name in REPLAY_FILES:
            contents[f'examples/q3-v2-demo/{name}'] = _read_source(demo, name)
        if (demo / 'reconciliation.json').is_file():
            contents['examples/q3-v2-demo/reconciliation.json'] = _read_source(demo, 'reconciliation.json')
        scenario = json.loads(contents['examples/q3-v2-demo/scenario.json'])
        if scenario.get('baseline_model') != 'hexagon_v2' or scenario.get('completion') != 'completed':
            raise ValueError('The optional demo must be a completed Q3 v2.0 replay')
    manifest = {'schema_version': 1, 'package': PACKAGE,
                'contains_v1': f'models/{V1_ARCHIVE}' in contents,
                'q4_models': ['q4_cu', 'q4_opportunity_v2', 'q4_route_v3'],
                'contains_q3_v2_demo': 'examples/q3-v2-demo/events.jsonl' in contents,
                'files': {name: {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                          for name, data in sorted(contents.items())}}
    contents[MANIFEST] = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(prefix='delivery-', suffix='.zip', dir=output.parent,
                                         delete=False) as temp:
            temp_name = Path(temp.name)
        with zipfile.ZipFile(temp_name, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in sorted(contents.items()):
                info = zipfile.ZipInfo(f'{PACKAGE}/{name}', date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (0o100755 if name.endswith('.command') else 0o100644) << 16
                archive.writestr(info, data, compresslevel=9)
        verify_delivery(temp_name)
        temp_name.replace(output)
    finally:
        if temp_name is not None:
            temp_name.unlink(missing_ok=True)
    return {'output': str(output), 'file_count': len(manifest['files']),
            'size': output.stat().st_size, 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
            'contains_v1': manifest['contains_v1'], 'contains_q3_v2_demo': manifest['contains_q3_v2_demo']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--v1-archive', type=Path)
    parser.add_argument('--demo-run', type=Path)
    args = parser.parse_args()
    result = build_delivery(args.output, v1_archive=args.v1_archive, demo_run=args.demo_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
