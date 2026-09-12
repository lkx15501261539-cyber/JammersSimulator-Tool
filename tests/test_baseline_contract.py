"""Compare the simulator directly with the user's unchanged Baseline backend.

The delivery archive is intentionally an external test fixture: it is neither
rewritten nor substituted by a test double. Each model is imported in a fresh
interpreter so its unqualified ``solver`` and ``第一问`` imports stay isolated.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest


REPO = Path(__file__).resolve().parents[1]
PROTECTED_FILES = (
    "run.py", "solver.py", "第一问.py", "config.json",
    "requirements.txt", "VERSION.json", "LICENSE",
)


CONTRACT_SCRIPT = r'''
import json
import math
from pathlib import Path
import sys

repo, model_dir = map(Path, sys.argv[1:3])
sys.path.insert(0, str(repo))
sys.path.insert(0, str(model_dir))
from run import OfflineBackend
from enhanced.world import ScenarioConfig, World
from enhanced.replay import metrics

case = json.load(sys.stdin)
truth = case['scene']
original = OfflineBackend(truth)
world = World(ScenarioConfig(seed=truth['seed'], error_model='baseline_fixed_field'))
world.sources = [dict(channel=s['channel'], x=s['position'][0],
                      y=s['position'][1], recv_radius=s['radius'],
                      source_type='omnidirectional', orientation=0.)
                 for s in truth['sources']]
responses = []
for index, action in enumerate(case['actions']):
    path = action['path']
    payload = dict(arena_id='default', robot_id='BASELINE-CONTRACT',
                   request_id=action.get('request_id', f'contract-{index}'))
    if 'position' in action:
        payload.update(position=action['position'], channel=action['channel'])
    expected = original.exchange(path, payload)
    actual = world.request(path, payload)
    assert expected['accepted'] is True, (index, expected)
    assert actual.get('accepted') is True, (index, actual)
    assert math.isclose(actual['virtual_time_s'], expected['virtual_time_s'],
                        abs_tol=1e-8, rel_tol=1e-12), (index, expected, actual)
    for field in ('measure_result', 'svd_deg', 'clear_result', 'exit_reason',
                  'max_virtual_duration_s', 'max_real_duration_s',
                  'remaining_real_duration_s'):
        assert actual.get(field) == expected.get(field), (index, field, expected, actual)
    assert world.channel == original._channel, (index, world.channel, original._channel)
    assert all(math.isclose(float(x), float(y), abs_tol=1e-9)
               for x, y in zip(world.position, original._p)), index
    assert not any(key in actual for key in ('sources', 'ground_truth', 'truth'))
    if action.get('expected_result'):
        result = actual.get('measure_result', actual.get('clear_result'))
        assert result == action['expected_result'], (index, actual)
    if action.get('repeat_of') is not None:
        previous = responses[action['repeat_of']]
        assert actual['svd_deg'] == previous['svd_deg'], (index, previous, actual)
    responses.append(actual)

evaluation = original.evaluation()
observed = metrics(world.events, len(world.sources))
assert observed['measure_count'] == evaluation['measures']
assert observed['switch_count'] == evaluation['switches']
assert math.isclose(observed['distance_m'], evaluation['movement_m'], abs_tol=1e-8)
assert math.isclose(observed['virtual_time_s'], evaluation['virtual_time_s'], abs_tol=1e-8)
assert world.cleared == original._cleared
print(json.dumps(dict(actions=len(responses), measures=evaluation['measures'],
                      switches=evaluation['switches'], cleared=len(world.cleared),
                      virtual_time_s=evaluation['virtual_time_s'])))
'''


def contract_case(seed):
    # Controlled sources expose exact protocol boundaries; the remaining sources
    # retain a legal ten-source scene without influencing the scripted channels.
    sources = [
        dict(channel=1, position=[600., 300.], radius=1000.),
        dict(channel=2, position=[100., 0.], radius=1000.),
        dict(channel=3, position=[-1200., 0.], radius=1500.),
        dict(channel=4, position=[0., 1500.], radius=1000.),
    ] + [dict(channel=c, position=[-600. + 80*c, -1200.], radius=1200.)
         for c in range(5, 11)]

    def action(path, x, y, channel, result=None, **extra):
        return dict(path=path, position=dict(x=x, y=y), channel=channel,
                    expected_result=result, **extra)

    actions = [
        dict(path='/enter'),
        action('/measure', 0., 0., 1, 'direction', request_id='fixed-reading'),
        action('/measure', 0., 0., 1, 'direction', request_id='fixed-reading', repeat_of=1),
        action('/measure', 200., 100., 1, 'direction'),
        action('/measure', 0., 0., 1, 'direction', repeat_of=1),
        action('/measure', 95., 0., 2, 'near'),
        action('/measure', 94.999, 0., 2, 'direction'),
        action('/clear', 79.999, 0., 2, 'no_target_in_range'),
        # Clearing another channel never changes the current measuring channel.
        action('/clear', 95., 0., 4, 'no_target_in_range'),
        action('/measure', 95., 0., 2, 'near'),
        action('/clear', 80., 0., 2, 'success'),
        action('/measure', 80., 0., 2, 'no_signal'),
        action('/measure', -2700., 0., 3, 'direction'),
        action('/measure', -2700.001, 0., 3, 'no_signal'),
        action('/clear', 0., 1480., 4, 'success'),
        action('/measure', 0., 1480., 3, 'no_signal'),
        action('/measure', 0., 1480., 20, 'no_signal'),
        dict(path='/exit'),
    ]
    return dict(scene=dict(seed=seed, sources=sources), actions=actions)


@pytest.fixture(params=('hexagon_v1', 'spiral_v1'))
def unchanged_model(request):
    from enhanced.baseline import default_archive, prepare_model

    archive = default_archive()
    if not archive or not Path(archive).is_file():
        pytest.skip('Supply the original Baseline v1.0 delivery archive for contract integration')
    model_dir, _ = prepare_model(archive, request.param)
    model_dir = Path(model_dir)
    with zipfile.ZipFile(archive) as delivery:
        expected = {name: hashlib.sha256(delivery.read(f'{request.param}/{name}')).hexdigest()
                    for name in PROTECTED_FILES}
    assert {name: hashlib.sha256((model_dir/name).read_bytes()).hexdigest()
            for name in PROTECTED_FILES} == expected
    yield model_dir
    assert {name: hashlib.sha256((model_dir/name).read_bytes()).hexdigest()
            for name in PROTECTED_FILES} == expected


@pytest.mark.parametrize('seed', (0, 20260912))
def test_unchanged_baseline_protocol_parity(unchanged_model, seed, tmp_path):
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
                       NUMBA_CACHE_DIR=str(tmp_path/'numba-cache'))
    result = subprocess.run(
        [sys.executable, '-c', CONTRACT_SCRIPT, str(REPO), str(unchanged_model)],
        input=json.dumps(contract_case(seed)), text=True, capture_output=True,
        cwd=tmp_path, env=environment, timeout=90,
    )
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
    report = json.loads(result.stdout)
    assert report['actions'] == 18
    assert report['cleared'] == 2
    # The identical request_id at the second reading must execute only once.
    assert report['measures'] == 11
