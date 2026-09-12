"""Experiment measurements must come from real accepted simulator receipts."""
from dataclasses import asdict
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from enhanced.runner import ResponseClient
from enhanced.world import ScenarioConfig, World
from tools import q4_compare as report


def small_world():
    world = World(ScenarioConfig(problem=4, count=10, error_model='worst_edge'))
    world.sources = [dict(channel=1, x=1000., y=0., source_type='directional',
                          orientation=250., recv_radius=1000.),
                     dict(channel=2, x=0., y=0., source_type='omnidirectional',
                          orientation=0., recv_radius=1000.)]
    client = ResponseClient(world.request)
    client.enter()
    return world, client


def test_matrix_has_one_current_v2_baseline_and_shared_ablation_controls():
    assert len(report.DEFAULT_VARIANTS) == len(set(report.DEFAULT_VARIANTS)) == 7
    assert len(report.FULL_GRID) == len(set(report.FULL_GRID)) == 11
    assert sum(report.VARIANTS[n].family == 'q4_opportunity_v2' for n in report.DEFAULT_VARIANTS) == 1
    assert {(report.VARIANTS[n].extra_budget, report.VARIANTS[n].tau_route_s)
            for n in report.DEFAULT_VARIANTS if n.startswith('route_')} == {(1, 5.), (2, 5.), (3, 5.), (2, 0.), (2, 20.)}


def test_real_receipts_reconstruct_switch_back_followups_and_delays():
    world, client = small_world()
    client.measure(0., 0., 1)
    client.measure(0., 0., 2)
    client.clear(0., 0., 2)
    client.measure(500., 500., 1)
    client.measure(500., -500., 1)
    client.clear(1000., 0., 1)
    client.exit()
    actions = report.canonical_actions(world.observations)
    assert [a['response'].get('measure_result') for a in actions] == ['direction', 'near', None, 'no_signal', 'direction', None]
    result = report.summarize_actions(actions,
        [dict(event='remeasurement_scheduled', k=1, action_step=1), dict(event='safe_no_signal', k=1, action_step=4),
         dict(event='budget_exhausted', k=1, action_step=5), dict(event='U', reason='budget_zero', k=1, action_step=5)],
        [1, 2], budget=2, roles={4: 'route_opportunity', 5: 'route_opportunity'},
        route_metrics={1: dict(scheduled_remeasure_step=1, certified_direct_after_opportunity=True)})
    m, first, near = result['metrics'], *result['channels']
    assert not result['audit_errors']
    assert math.isclose(m['T'], world.t, abs_tol=1e-7)
    assert m['N_sw'] == 2 and m['N_meas'] == 4 and m['N_clr'] == m['N_succ'] == 2
    assert m['additional_remeasurements'] == 2 and m['mean_followups_per_source'] == 1
    assert m['first_remeasure_success_rate'] == 0 and m['second_remeasure_success_rate'] == 1
    assert m['opportunity_direct_clear_sources'] == m['budget_stop_events'] == m['directional_confirmation_events'] == 1
    assert first['actual_first_remeasure_step'] == 4 and first['scheduled_at_step'] == 1
    assert math.isclose(first['first_remeasure_distance'], math.hypot(500, 500))
    assert math.isclose(first['first_remeasure_delay_s'], actions[3]['T']-actions[0]['T']-5)
    assert first['first_remeasure_feedback_delay_s'] == first['first_remeasure_delay_s']+5
    assert near['first_near_step'] == 2 and near['first_detect_step'] is None
    assert m['first_direction_sources'] == 1  # near-only sources do not enter this cohort


def test_missing_remeasurement_reason_stays_unknown_and_no_attempt_rate_is_null():
    world, client = small_world()
    client.measure(0., 0., 1)
    client.clear(1000., 0., 1)
    result = report.summarize_actions(report.canonical_actions(world.observations), [], [1, 2])
    assert result['metrics']['no_remeasure_until_clear_sources'] == 1
    assert result['metrics']['first_remeasure_success_rate'] is None
    assert result['metrics']['mean_first_remeasure_delay_s'] is None
    assert result['channels'][0]['no_remeasure_reason'] == 'unknown'
    assert result['channels'][0]['scheduled_remeasure_step'] is None


def test_rejected_calls_are_not_counted_and_unresolved_sources_are_censored():
    world, client = small_world()
    client.measure(0., 0., 1)
    response = client.clear(float('nan'), 0., 1)
    assert response['accepted'] is False
    result = report.summarize_actions(report.canonical_actions(world.observations), [], [1, 2])
    assert result['metrics']['N_clr'] == 0
    assert result['metrics']['no_remeasure_until_clear_sources'] == 0
    assert result['metrics']['unresolved_without_remeasure_sources'] == 1


def test_repeated_fixed_location_and_budget_violations_are_reported():
    world, client = small_world()
    for _ in range(3):
        client.measure(0., 0., 1)
    result = report.summarize_actions(report.canonical_actions(world.observations), [], [1, 2], budget=1)
    assert any('repeated coordinate' in error for error in result['audit_errors'])
    assert any('budget exceeded' in error for error in result['audit_errors'])
    assert result['metrics']['additional_remeasurements'] == 2


def _near_world(config):
    world = World(config)
    for source in world.sources:
        source.update(x=0., y=0., source_type='omnidirectional')
    return world


def test_observation_wrapper_preserves_baseline_actions_and_counts():
    config = ScenarioConfig(problem=4, count=10, error_model='worst_edge')
    direct_world, traced_world = _near_world(config), _near_world(config)
    direct = report._controller_module('q4_opportunity_v2').Controller(ResponseClient(direct_world.request))
    traced = report._make_controller(report.VARIANTS['pair_v2'], config, ResponseClient(traced_world.request))
    a, b = direct.run(), traced.run()
    assert a['completed'] and b['completed']
    assert a['counts'] == b['counts'] and a['T'] == b['T']
    assert report.canonical_actions(direct_world.observations) == report.canonical_actions(traced_world.observations)


def test_failed_variant_is_retained_and_next_run_continues(monkeypatch, tmp_path):
    monkeypatch.setattr(report, 'World', _near_world)
    actual = report._make_controller

    def fail_one(variant, config, client):
        if variant.name == 'q4_v1':
            raise RuntimeError('deliberate initialization failure')
        return actual(variant, config, client)

    monkeypatch.setattr(report, '_make_controller', fail_one)
    experiment = report.compare([42], ['uniform'], variants=['q4_v1', 'pair_v2'],
                                trace_root=tmp_path/'traces', output=tmp_path/'out.json', progress=False)
    assert len(experiment['rows']) == 2
    failed, passed = experiment['rows']
    assert failed['metrics']['completed'] is False and 'initialization failure' in failed['failure']
    assert passed['metrics']['completed'] and passed['metrics']['cleared'] == passed['metrics']['source_count']
    assert failed['world_sha256'] == passed['world_sha256']
    assert (tmp_path/'out.checkpoint.json').is_file()
    artifacts = report.write_outputs(experiment, tmp_path/'out.json')
    assert all(Path(path).is_file() for path in artifacts.values())
    saved = json.loads((tmp_path/'out.json').read_text())
    assert saved['baseline_aliases']['A_current_Q4_v2'] == saved['baseline_aliases']['B_left_right'] == 'pair_v2'
    assert saved['summary'][0]['mean_T'] is None  # failed partial T never masquerades as fast completion
    assert (tmp_path/passed['trace_directory']/'actions.json').is_file()
    metadata = json.loads((tmp_path/passed['trace_directory']/'scenario.json').read_text())
    assert metadata['model'] == 'q4_opportunity_v2'
    # Re-rendering from a different working directory preserves relative links.
    report.write_outputs(saved, tmp_path/'out.json')
    assert json.loads((tmp_path/'out.json').read_text())['rows'][1]['trace_directory'] == passed['trace_directory']


def test_route_factory_passes_exact_parameters_without_modifying_controller_policy(monkeypatch):
    calls = []

    class Controller:
        def __init__(self, client, **kwargs):
            calls.append((client, kwargs))
            self.decisions, self.actions = [], []
            self.counts = {'L_move': 0}
            self.T = 0

    fake = SimpleNamespace(Controller=Controller, RouteConfig=lambda **kw: kw)
    monkeypatch.setattr(report, '_route_module', lambda: fake)
    cfg = ScenarioConfig(seed=47, problem=4, error_model='worst_edge')
    report._make_controller(report.VARIANTS['route_b1_t20'], cfg, 'client')
    assert calls == [('client', dict(seed=47, error_mode='worst_edge',
                                   config=dict(extra_budget=1, tau_route_s=20., candidate_policy='route')))]


def test_comparison_rejects_different_worlds_and_initial_tours():
    base = dict(config=dict(scenario='uniform', seed=42), variant=dict(name='pair_v2'),
                world_sha256='world', initial_tour_sha256='tour', metrics=dict(completed=True))
    changed = dict(base, variant=dict(name='route_b2_t5'), world_sha256='changed-world', initial_tour_sha256='changed-tour')
    errors = report.validate_common_scenes([base, changed])
    assert len(errors) == 2


def test_duplicate_design_rows_are_rejected_before_running():
    with pytest.raises(ValueError, match='duplicate'):
        report.compare([42, 42], ['uniform'], variants=['pair_v2'], progress=False)


def test_route_replay_keeps_nondefault_parameters_and_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(report, 'World', _near_world)
    row = report.run_one(asdict(ScenarioConfig(problem=4, count=10)), 'route_b1_t20', tmp_path)
    assert row['metrics']['completed'] and not row['audit_errors'], row['failure']
    scenario = json.loads((Path(row['trace_directory'])/'scenario.json').read_text())
    assert scenario['model'] == 'q4_route_v3'
    assert scenario['route_config'] == dict(extra_budget=1, tau_route_s=20., candidate_policy='route')
    assert row['runtime_metadata'] == scenario['score_runtime']
    assert row['runtime_metadata']['backend'] in ('q3_pure_diameter', 'q1_fraction')
    hashes = report.source_hashes([report.VARIANTS['route_b1_t20']])
    assert 'models/Baseline_v2.0_七点六边形.zip' in hashes
