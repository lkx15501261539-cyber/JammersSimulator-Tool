"""Frozen Q3 v2 importer, process bridge and read-only policy equivalence."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest
from enhanced.baseline import CACHE, ROOT, default_archive, prepare_model, run_baseline
from enhanced.replay import load_run, project
from enhanced.world import ScenarioConfig


@pytest.fixture
def v2_archive():
    archive = default_archive('hexagon_v2')
    if not archive.is_file():
        pytest.skip('Supply the original Baseline v2 archive')
    return archive


def test_v2_imports_manifest_verified_original_modules(v2_archive):
    directory, provenance = prepare_model(v2_archive, 'hexagon_v2')
    assert {'run.py', 'baseline_runtime.py', 'solver.py', 'cu.py', 'routing.py', '第一问.py'} <= set(provenance['model_files_sha256'])
    with zipfile.ZipFile(v2_archive) as archive:
        prefix = 'Baseline_v2.0_七点六边形/'
        manifest = json.loads(archive.read(prefix+'SHA256SUMS.json'))
        assert provenance['manifest_files_verified'] == len(manifest)
        for name, digest in provenance['model_files_sha256'].items():
            assert (directory/name).read_bytes() == archive.read(prefix+name)
            assert hashlib.sha256((directory/name).read_bytes()).hexdigest() == manifest[name] == digest
    cfg = json.loads((directory/'config.json').read_text())
    assert cfg['version'] == 'baseline-v2.0'
    assert cfg['max_followups'] == 3 and len(cfg['points']) == 7


PARITY_SCRIPT = r'''
import copy
import json
import sys
from pathlib import Path
repo, model_dir, out = map(Path, sys.argv[1:4])
sys.path[:0] = [str(model_dir), str(repo)]
import run as model
from enhanced.baseline_worker import execute_model
from enhanced.strategy_observer import observe_controller
from enhanced.world import World, ScenarioConfig
from enhanced.replay import metrics
cfg = json.loads((model_dir/'config.json').read_text())
original_controller = model.Controller
worlds, summaries = [], []
model.warmup()
for observed in (False, True):
    world = World(ScenarioConfig(seed=42, error_model='baseline_fixed_field'))
    world.sources = [dict(channel=1, x=700., y=300., recv_radius=1200., source_type='omnidirectional', orientation=0.),
                     dict(channel=2, x=0., y=0., recv_radius=1000., source_type='omnidirectional', orientation=0.)]
    class Backend:
        def exchange(self, path, payload):
            if observed:
                observer.requested(path, payload)
            return world.request(path, payload)
    directory = out/('observed' if observed else 'plain')
    if observed:
        with observe_controller(model, lambda data: world.emit('StrategyState', **data)) as observer:
            summary, error = execute_model(model, cfg, directory, Backend())
    else:
        # Direct package public classes are the reference, not another bridge.
        client = model.JournalClient(Backend(), directory, 'BASELINE-SIM', cfg, live=False)
        controller = model.Controller(client, cfg)
        try:
            summary = controller.run()
            model.save_json(directory/'decisions.json', controller.events)
            error = None
        finally:
            client.close()
    assert error is None, error
    assert summary['completion'] == 'completed', summary
    summary.pop('wall_runtime_s', None)
    summaries.append(summary)
    worlds.append(world)
assert model.Controller is original_controller
assert summaries[0] == summaries[1]

def normalized(world):
    rows = copy.deepcopy(world.observations)
    for row in rows:
        row['request'].pop('request_id', None)
    return rows
assert normalized(worlds[0]) == normalized(worlds[1])
assert json.loads((out/'plain'/'decisions.json').read_text()) == json.loads((out/'observed'/'decisions.json').read_text())
assert metrics(worlds[0].events, 2) == metrics(worlds[1].events, 2)
snapshots = [e['data'] for e in worlds[1].events if e['type'] == 'StrategyState']
assert snapshots and all(s['available'] and s['model'] == 'hexagon_v2' for s in snapshots)
assert snapshots[-1]['tasks'] == [] and snapshots[-1]['phase'] == 'finished'
assert any(s['cu_decision'] is not None for s in snapshots)
assert any(s['optical_points'] for s in snapshots)
assert all(info['limit'] == 3 and 0 <= info['followups'] <= 3 for s in snapshots for info in s['channel_iterations'].values())
previous = None
for event in worlds[1].events:
    if event['type'] == 'StrategyState':
        previous = event['data']
    elif event['type'] in ('Measure', 'Clear'):
        assert previous['current_target']['kind'] == event['type'].lower()
        assert previous['current_target']['channel'] == event['data']['channel']
        assert previous['current_target']['position'] == event['data']['position']
    elif event['type'] == 'Move':
        assert previous['current_target']['position'] == event['data']['destination']
summary = summaries[1]
assert abs(summary['time_equation_s']-summary['virtual_time_s']) < 1e-6
assert summary['successes'] == 2
print(json.dumps(dict(requests=len(worlds[1].observations), cleared=2, snapshots=len(snapshots))))
'''


def test_v2_observer_matches_unmodified_controller_actions_and_decisions(v2_archive, tmp_path):
    directory, _ = prepare_model(v2_archive, 'hexagon_v2')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', NUMBA_CACHE_DIR=str(CACHE/'runtime-cache'/'numba'))
    result = subprocess.run([sys.executable, '-c', PARITY_SCRIPT, str(ROOT), str(directory), str(tmp_path)],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout+'\n'+result.stderr
    assert json.loads(result.stdout)['cleared'] == 2


def test_v2_complete_worker_mission_preserves_config_and_time(v2_archive, tmp_path):
    output = tmp_path/'v2-mission'
    run = run_baseline(ScenarioConfig(seed=20260912, error_model='baseline_fixed_field'),
                       v2_archive, 'hexagon_v2', output)
    assert run['metadata']['strategy_version'] == 'baseline-v2.0'
    assert run['metadata']['completion'] == 'completed'
    assert run['metadata']['model_files_unchanged'] and run['metadata']['metrics_reconciled']
    assert load_run(output)['events'] == run['events']
    end = project(run['events'], run['events'][-1]['end'])
    assert len(end['cleared']) == len(run['sources']) == 10
    assert end['strategy']['model'] == 'hexagon_v2' and end['strategy']['tasks'] == []
    assert all(0 <= item['followups'] <= 3 for item in end['strategy']['channel_iterations'].values())
    with zipfile.ZipFile(v2_archive) as archive:
        cfg = json.loads(archive.read('Baseline_v2.0_七点六边形/config.json'))
    assert json.loads((output/'baseline-original'/'session.json').read_text())['settings'] == cfg
    summary = json.loads((output/'baseline-original'/'summary.json').read_text())
    assert abs(summary['virtual_time_s']-summary['time_equation_s']) < 1e-6
    assert summary['optimizations'] > 0
