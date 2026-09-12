"""The planning display observes the frozen controller without changing policy."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from enhanced.replay import project, validate


def event(seq, kind, start, end=None, **data):
    return dict(seq=seq, type=kind, start=start, end=start if end is None else end, data=data)


def test_route_progress_uses_only_traversed_path_and_rewinds():
    snapshot = dict(available=True, route_points=[dict(index=i, position=[i*10., 0.]) for i in range(4)],
                    tasks=[dict(kind='measure', channel=2, position=[30., 0.])],
                    current_target=dict(kind='measure', channel=2, position=[30., 0.]),
                    channel_iterations={'2': dict(followups=0, measurements=1)})
    later = copy.deepcopy(snapshot)
    later['channel_iterations']['2'] = dict(followups=1, measurements=2)
    rows = validate([
        event(0, 'StrategyState', 0, **snapshot),
        event(1, 'Move', 0, 6, origin=[0., 0.], destination=[30., 0.], distance=30.),
        event(2, 'Measure', 6, 11, position=[30., 0.], channel=2, result='direction', svd_deg=42.),
        event(3, 'StrategyState', 11, **later),
    ])
    original = copy.deepcopy(rows)
    assert [p['visited'] for p in project(rows, 0)['route_points']] == [True, False, False, False]
    # Waypoint at x=10 was passed through, even though this Move ends at x=30.
    assert [p['visited'] for p in project(rows, 3)['route_points']] == [True, True, False, False]
    assert [p['visited'] for p in project(rows, 6)['route_points']] == [True]*4
    assert project(rows, 10.99)['strategy']['channel_iterations']['2']['followups'] == 0
    assert project(rows, 11)['strategy']['channel_iterations']['2']['followups'] == 1
    expected = project(rows, 3)
    project(rows, 11)
    assert project(rows, 3) == expected
    expected['strategy']['current_target']['position'][0] = 999.
    assert rows == original


def test_old_logs_do_not_invent_routes_iterations_or_queues():
    state = project([event(0, 'Move', 0, 10, origin=[0., 0.], destination=[50., 0.], distance=50.)], 5)
    assert not state['strategy']['available']
    assert state['route_points'] == []
    assert state['strategy']['tasks'] == []
    assert state['strategy']['pending_targets'] == []
    assert state['strategy']['channel_iterations'] == {}
    assert state['strategy']['current_target'] is None
    assert state['strategy']['unavailable_reason']


PARITY_SCRIPT = r'''
import copy
import hashlib
import json
from pathlib import Path
import sys
repo, model_dir, output = map(Path, sys.argv[1:4])
sys.path.insert(0, str(repo))
sys.path.insert(0, str(model_dir))
import run as original
from enhanced.strategy_observer import observe_controller
from enhanced.world import ScenarioConfig, World
from enhanced.replay import metrics, project

config = json.loads((model_dir/'config.json').read_text())
hashes = {name: hashlib.sha256((model_dir/name).read_bytes()).hexdigest()
          for name in ('run.py', 'solver.py', '第一问.py', 'config.json')}
original.warmup()
controller_type = original.Controller
worlds, summaries = [], []
class Backend:
    def __init__(self, world, observer=None):
        self.world, self.observer = world, observer
    def exchange(self, path, payload):
        if self.observer is not None:
            self.observer.requested(path, payload)
        return self.world.request(path, payload)

for observed in (False, True):
    world = World(ScenarioConfig(seed=42, error_model='baseline_fixed_field'))
    directory = output/('observed' if observed else 'original')
    if observed:
        with observe_controller(original, lambda data: world.emit('StrategyState', **data)) as observer:
            summary, error = original.execute(config, directory, Backend(world, observer), robot_id='PARITY', live=False)
    else:
        summary, error = original.execute(config, directory, Backend(world), robot_id='PARITY', live=False)
    assert error is None, error
    assert summary['completion'] == 'completed', summary['completion']
    summary.pop('wall_runtime_s')
    worlds.append(world)
    summaries.append(summary)
assert original.Controller is controller_type
assert summaries[0] == summaries[1], 'Observing changed original summary'
assert config == json.loads((model_dir/'config.json').read_text())

def normalized_observations(world):
    rows = copy.deepcopy(world.observations)
    for row in rows:
        row['request'].pop('request_id', None)
    return rows
assert normalized_observations(worlds[0]) == normalized_observations(worlds[1]), 'Original request/response sequence changed'
assert metrics(worlds[0].events, len(worlds[0].sources)) == metrics(worlds[1].events, len(worlds[1].sources))
plain_decisions = json.loads((output/'original'/'decisions.json').read_text())
observed_decisions = json.loads((output/'observed'/'decisions.json').read_text())
assert plain_decisions == observed_decisions, 'Original planning decisions changed'

events = worlds[1].events
snapshots = [e['data'] for e in events if e['type'] == 'StrategyState']
assert snapshots and all(s['available'] for s in snapshots)
assert snapshots[0]['route_points'] == [dict(index=i, position=p) for i, p in enumerate(config['points'])]
assert snapshots[-1]['phase'] == 'finished' and snapshots[-1]['tasks'] == []
assert len(project(events, events[-1]['end'])['route_points']) == len(config['points'])
assert all(p['visited'] for p in project(events, events[-1]['end'])['route_points'])
for channel, data in snapshots[-1]['channel_iterations'].items():
    assert data['followups'] == summaries[1]['channel_states'][int(channel)]['followups']
    assert data['measurements'] == sum(d['kind'] == 'measure' and d['channel'] == int(channel) for d in observed_decisions)

previous = None
for e in events:
    if e['type'] == 'StrategyState':
        previous = e['data']
        encoded = json.dumps(previous)
        assert 'svd_deg' not in encoded and 'recv_radius' not in encoded and 'ground_truth' not in encoded
        target = previous.get('current_target')
        if target:
            assert set(target) == {'kind', 'channel', 'position', 'role'}
    if e['type'] in ('Measure', 'Clear'):
        target = previous['current_target']
        assert target['kind'] == e['type'].lower()
        assert target['channel'] == e['data']['channel']
        assert target['position'] == e['data']['position']
    if e['type'] == 'Move':
        assert previous['current_target']['position'] == e['data']['destination']

tail = [d['channel'] for d in observed_decisions if d['kind'] == 'service_start' and d['tail']]
if tail:
    first_tail = next(s for s in snapshots if s['queue_kind'] == 'tail')
    assert [task['channel'] for task in first_tail['tasks']] == tail
    assert all(task['position'] is None for task in first_tail['tasks'])
assert hashes == {name: hashlib.sha256((model_dir/name).read_bytes()).hexdigest() for name in hashes}
print(json.dumps(dict(snapshots=len(snapshots), requests=len(worlds[1].observations),
                      tail_order=tail, cleared=summaries[1]['known_cleared'])))
'''


@pytest.mark.parametrize('model', ('hexagon_v1', 'spiral_v1'))
def test_observer_preserves_complete_original_controller_policy(model, tmp_path):
    from enhanced.baseline import CACHE, ROOT, default_archive, prepare_model
    archive = default_archive()
    if not archive.is_file():
        pytest.skip('Supply the original unchanged Baseline ZIP for policy equivalence')
    directory, _ = prepare_model(archive, model)
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
                       NUMBA_CACHE_DIR=str(CACHE/'runtime-cache'/'numba'))
    result = subprocess.run([sys.executable, '-c', PARITY_SCRIPT, str(ROOT), str(directory), str(tmp_path)],
                            cwd=ROOT, env=environment, text=True, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stdout+'\n'+result.stderr
    report = json.loads(result.stdout)
    assert report['snapshots'] > report['requests']
    assert report['cleared'] == 15


def test_worker_planning_snapshots_are_persisted_and_replay_without_model(tmp_path):
    from enhanced.baseline import default_archive, run_baseline
    from enhanced.replay import load_run
    from enhanced.world import ScenarioConfig
    archive = default_archive()
    if not archive.is_file():
        pytest.skip('Supply the original unchanged Baseline ZIP for worker integration')
    output = tmp_path/'mission'
    run = run_baseline(ScenarioConfig(seed=20260912, error_model='baseline_fixed_field'),
                       archive, 'hexagon_v1', output)
    loaded = load_run(output)
    assert loaded['events'] == run['events']
    assert loaded['metadata']['strategy_observer_schema'] == 1
    assert loaded['metadata']['model_files_unchanged']
    assert loaded['metadata']['metrics_reconciled']
    start = project(loaded['events'], 0)
    assert start['strategy']['available'] and len(start['route_points']) == 7
    assert [p['visited'] for p in start['route_points']] == [True]+[False]*6
    end = project(loaded['events'], loaded['events'][-1]['end'])
    assert end['strategy']['phase'] == 'finished'
    assert end['strategy']['tasks'] == []
    assert all(p['visited'] for p in end['route_points'])
    summary = json.loads((output/'baseline-original'/'summary.json').read_text())
    for channel, info in end['strategy']['channel_iterations'].items():
        assert info['followups'] == summary['channel_states'][channel]['followups']
        assert info['state'] == summary['channel_states'][channel]['state']


NEAR_SCRIPT = r'''
import json
from pathlib import Path
import sys
repo, model_dir, output = map(Path, sys.argv[1:4])
sys.path.insert(0, str(repo))
sys.path.insert(0, str(model_dir))
import run as original
from enhanced.strategy_observer import observe_controller
from enhanced.world import World
from enhanced.replay import project
config = json.loads((model_dir/'config.json').read_text())
world = World()
world.sources = [dict(channel=1, x=0., y=0., recv_radius=1200.,
                      source_type='omnidirectional', orientation=0.)]
with observe_controller(original, lambda data: world.emit('StrategyState', **data)) as observer:
    class Backend:
        def exchange(self, path, payload):
            observer.requested(path, payload)
            return world.request(path, payload)
    client = original.JournalClient(Backend(), output, 'NEAR-OBSERVER', config)
    try:
        controller = original.Controller(client, config)
        client.call('/enter')
        assert controller.measure(1, [0., 0.], 'target_followup') == 'near'
        client.call('/exit')
    finally:
        client.close()
before = project(world.events, 4.999)
during = project(world.events, 7.5)
after = project(world.events, 10.)
assert before['measure_count'] == before['strategy']['channel_iterations']['1']['measurements'] == 0
assert before['strategy']['channel_iterations']['1']['followups'] == 0
assert during['status'] == 'clear' and during['measure_count'] == 1
assert during['strategy']['channel_iterations']['1']['measurements'] == 1
assert during['strategy']['channel_iterations']['1']['followups'] == 1
assert during['strategy']['channel_iterations']['1']['state'] == 'FOUND'
assert during['strategy']['current_target']['kind'] == 'clear'
assert during['cleared'] == set()
assert after['strategy']['channel_iterations']['1']['measurements'] == 1
assert after['strategy']['channel_iterations']['1']['state'] == 'CLEARED'
assert after['cleared'] == {1}
'''


def test_near_measurement_count_updates_before_original_nested_clear_finishes(tmp_path):
    from enhanced.baseline import CACHE, ROOT, default_archive, prepare_model
    archive = default_archive()
    if not archive.is_file():
        pytest.skip('Supply the original unchanged Baseline ZIP for near timing regression')
    directory, _ = prepare_model(archive, 'hexagon_v1')
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
                       NUMBA_CACHE_DIR=str(CACHE/'runtime-cache'/'numba'))
    result = subprocess.run([sys.executable, '-c', NEAR_SCRIPT, str(ROOT), str(directory), str(tmp_path/'near')],
                            cwd=ROOT, env=environment, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout+'\n'+result.stderr
