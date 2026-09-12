"""Accepted-feedback and independent physical invariants for the route policy."""
import math
from pathlib import Path
import sys

import pytest

CODE = Path(__file__).resolve().parents[1] / 'model_sources' / 'q4'
sys.path.insert(0, str(CODE))
from route_opportunistic_remeasure import Controller, RouteConfig, prepare_runtime
from q4_v2 import Controller as PairedController
from opportunities import RouteCandidate, distance_safe
from enhanced.runner import ResponseClient
from enhanced.world import ScenarioConfig, World


def source_world(*, problem=4, directional=True):
    w = World(ScenarioConfig(problem=problem, count=10, seed=42, error_model='worst_edge'))
    for k, source in enumerate(w.sources, 1):
        source['channel'] = k
    w.sources[0] = dict(channel=1, x=0., y=0., recv_radius=1000.,
                        source_type='directional' if directional else 'omnidirectional', orientation=0.)
    c = Controller(ResponseClient(w.request), config=RouteConfig(problem_id=problem))
    assert c.client.enter()['accepted']
    reply = c.action('measure', 1, (100., 0.))
    assert reply['measure_result'] == 'direction'
    c.direction(c.channels[1], reply['svd_deg'])
    return w, c, c.channels[1]


def test_initial_25_tour_is_identical_to_existing_v2():
    a = Controller(None)
    b = PairedController(None)
    assert a.initial_order == b.initial_order and a.initial_order[0] == 0
    assert a.certificate['passed']


def test_direction_actually_reserves_route_measurement_and_replans():
    _, c, s = source_world(directional=False)
    tasks = c.next_tasks()
    assert 1 in c.reservations and ('opportunity', 1) in tasks
    assert c.reservations[1].level == 1
    assert c.reservations[1].extra_move_s < .0003
    assert c.source_metrics[1]['scheduled_remeasure_step'] is not None
    before = c._reservation_signature[1]
    c.remaining.reverse()
    c._core_signature = None
    c.next_tasks()
    assert c._reservation_signature[1] != before
    assert c.reservations[1].certificate
    assert s.r_k == 2


def test_safe_backside_failure_preserves_F_P_E_H_and_shared_budget():
    w, c, s = source_world()
    before = tuple(s.F.directions), tuple(s.F.negative_history), s.P, s.E, s.H_k0
    assert distance_safe(s.F, s.E, (-1, 0))
    assert c.followup(s, (-1, 0)) == 'no_signal'  # even within 5m, backside wins
    assert before == (tuple(s.F.directions), tuple(s.F.negative_history), s.P, s.E, s.H_k0)
    assert 1 in c.directional_confirmed and s.r_k == 1 and s.followups == 1
    c.next_tasks()
    assert s.r_k == 1  # scheduling never restores budget
    # Force another individually certified, distinct backside opportunity.
    assert c.followup(s, (-2, 0)) == 'no_signal'
    assert s.r_k == 0 and s.followups == 2 and s.sigma == 'FOUND'
    assert 1 in c.optical_pending and c.optical_plans[1].verifies()
    while s.sigma == 'FOUND':
        c.execute_task('optical', 1)
    assert 1 in w.cleared and s.sigma == 'CLEARED'
    assert math.isclose(c.T, w.t, abs_tol=1e-7)


def test_certified_Q3_no_signal_is_anomaly_not_normal_inference():
    _, c, s = source_world(problem=3, directional=True)
    with pytest.raises(RuntimeError, match='Q3 certified reception'):
        c.followup(s, (-1, 0))
    assert s.r_k == 1 and s.followups == 1
    assert not c.directional_confirmed and s.sigma == 'FOUND'


def test_near_feedback_then_exactly_one_in_place_clear():
    w, c, s = source_world(directional=False)
    assert c.followup(s, (1, 0)) == 'near'
    assert c.actions[-1]['kind'] == 'measure' and s.sigma == 'FOUND'
    assert c.next_tasks() == [('near', 1)]
    c.execute_task('near', 1)
    assert c.actions[-1]['p'] == (1., 0.) and c.actions[-1]['kind'] == 'clear'
    assert s.sigma == 'CLEARED' and 1 in w.cleared
    with pytest.raises(RuntimeError, match='cleared channel'):
        c.action('measure', 1, (2, 0))


def test_direction_refines_existing_P_F_E_and_certifies_direct_clear():
    _, c, s = source_world(directional=False)
    old_P, old_E = s.P, s.E
    assert c.followup(s, (0, 100)) == 'direction'
    assert len(s.F.directions) == 2 and len(s.P) > len(old_P) and s.E != old_E
    assert s.F.contains((0, 0)) and 1 in c.direct_points
    c.execute_task('direct', 1)
    assert s.sigma == 'CLEARED'
    assert c.source_metrics[1]['certified_direct_after_opportunity']


def test_duplicate_millimeter_point_is_rejected_before_interface_call():
    _, c, s = source_world(directional=False)
    count = len(c.actions)
    with pytest.raises(RuntimeError, match='duplicate snapped'):
        c.action('measure', 1, (100.0004, .0004))
    assert len(c.actions) == count and s.r_k == 2


def test_no_candidate_uses_certified_U_and_zero_budget_is_explicit(monkeypatch):
    _, c, s = source_world(directional=False)
    class Empty:
        def path_candidates(self, segments): return []
        def nearby_candidates(self, segments, tau): return []
    monkeypatch.setattr(c, '_prepare', lambda s: Empty())
    c.next_tasks()
    assert 1 in c.optical_pending and c.optical_plans[1].verifies()
    assert c.source_metrics[1]['no_remeasure_reason'] == 'no_route_candidate'
    _, d, s2 = source_world(directional=False)
    s2.r_k = 0
    d.next_tasks()
    assert s2.followups == 0 and 1 in d.optical_pending
    assert d.source_metrics[1]['no_remeasure_reason'] == 'budget_zero'


def test_unknown_scan_is_one_action_and_absent_requires_real_all_25():
    w = World(ScenarioConfig(problem=4, count=10))
    c = Controller(ResponseClient(w.request))
    c.client.enter()
    for k, s in c.channels.items():
        if k != 20:
            s.sigma = 'CLEARED'
    # Ensure tested frequency does not physically exist.
    for k, source in enumerate(w.sources, 1): source['channel'] = k
    s = c.channels[20]
    for j in range(25):
        before = len(c.actions)
        c.scan(j)
        assert len(c.actions) == before + 1
        assert s.sigma == ('ABSENT' if j == 24 else 'UNKNOWN')
    assert len(s.scanned) == 25 and set(s.scanned.values()) == {'no_signal'}


def test_optical_executes_one_attempt_and_never_restarts_the_cover():
    _, c, s = source_world()
    c._enter_U(s, 'test')
    route = tuple(c.optical_pending[1])
    before = len(c.actions)
    c.execute_task('optical', 1)
    assert len(c.actions) == before + 1
    if s.sigma == 'FOUND':
        assert tuple(c.optical_pending[1]) == route[1:]
        c.next_tasks()
        assert tuple(c.optical_pending[1]) == route[1:]


def test_inserted_measure_and_switch_back_costs_seven_seconds():
    w = World(ScenarioConfig(problem=4))
    c = Controller(ResponseClient(w.request))
    c.client.enter()
    c.action('measure', 2, (0, 0))
    c.action('measure', 1, (0, 0))
    assert c.T == w.t == 12  # original one channel-1 measurement costs 5


@pytest.mark.parametrize('budget', [1, 2, 3])
def test_real_mixed_world_finite_completion_budget_timing_and_evidence(budget):
    prepare_runtime()
    w = World(ScenarioConfig(problem=4, seed=47, scenario='boundary-biased'))
    c = Controller(ResponseClient(w.request), config=RouteConfig(extra_budget=budget))
    r = c.run()
    assert r['completed'], r['failure']
    assert len(w.cleared) == len(w.sources) and math.isclose(c.T, w.t, abs_tol=1e-7)
    assert sum(s.followups for s in c.channels.values()) > 0
    for s in c.channels.values():
        assert s.followups <= budget and 0 <= s.r_k <= budget
        if s.sigma == 'ABSENT':
            assert len(s.scanned) == 25 and set(s.scanned.values()) == {'no_signal'}
    observed = [(a['k'], tuple(a['p'])) for a in c.actions if a['kind'] == 'measure']
    assert len(observed) == len(set(observed))
    executed_detours = [d for d in c.decisions if d['event'] == 'opportunity_detour_verified']
    assert executed_detours and all(d['actual_extra_move_s'] <= 5 + 1e-8 for d in executed_detours)


def test_global_candidate_ablation_executes_with_truth_blind_public_client():
    prepare_runtime()
    w = World(ScenarioConfig(problem=4, seed=42, scenario='center-biased'))
    c = Controller(ResponseClient(w.request), config=RouteConfig(candidate_policy='global_q2'))
    r = c.run()
    assert r['completed'], r['failure']
    assert len(w.cleared) == len(w.sources)
    assert any(d.get('level') == 3 for d in c.decisions if d['event'] == 'remeasurement')
