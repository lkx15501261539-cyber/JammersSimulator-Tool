"""Q4 v2 state-machine, budgets, complete timing and finite completion."""
import copy
import math
from types import SimpleNamespace

import pytest

from enhanced.q4_adapter import _controller_module
from enhanced.runner import ResponseClient
from enhanced.world import ScenarioConfig, World

model = _controller_module('q4_opportunity_v2')


def world_source(k, x=1000., y=0., orientation=250., kind='directional'):
    return dict(channel=k, x=x, y=y, source_type=kind, orientation=orientation, recv_radius=1000.)


def setup(sources=None, **kwargs):
    world = World(ScenarioConfig(problem=4, count=10, error_model='worst_edge'))
    if sources is not None:
        world.sources = sources
    client = ResponseClient(world.request)
    ctl = model.Controller(client, opt_rounds=0, **kwargs)
    return world, client, ctl


def pair_state():
    # Isolate the opportunity state machine in a real physical response world.
    # Synthetic anchor coordinates are not claimed to be a discovery skeleton.
    sources = [world_source(1)] + [world_source(k, 500., k*10., kind='omnidirectional') for k in range(2, 11)]
    world, client, ctl = setup(sources)
    client.enter()
    z = list(ctl.Z)
    z[1], z[2] = (500., 500.), (500., -500.)
    ctl.Z, ctl.remaining = tuple(z), [1, 2]
    for k, s in ctl.channels.items():
        if k != 1:
            s.sigma = 'ABSENT'  # other channels are out of this transition test
    s = ctl.channels[1]
    r = ctl.action('measure', 1, (0., 0.))
    ctl.direction(s, r['svd_deg'])
    assert s.r_k == 3 and not ctl.opportunities
    ctl.followup(s, (1000., -1000.), model.Uhat(s.F, ctl.p, E=s.E, saved=s.H_k0))
    assert s.r_k == 2 and ctl.opportunities[1].pending() == (1, 2)
    return world, client, ctl, s


def test_safe_failure_reserves_opposite_without_shrinking_F_and_then_clears():
    world, _, ctl, s = pair_state()
    before = copy.deepcopy(s.F.directions), s.E, s.H_k0, dict(s.scanned)
    ctl.scan(1)
    assert s.r_k == 1 and s.followups == 2 and s.sigma == 'FOUND'
    assert s.F.directions == before[0] and s.E is before[1] and s.H_k0 is before[2]
    assert s.scanned == before[3]  # opportunity is not an UNKNOWN absence scan
    assert ctl.opportunities[1].guaranteed_anchor == 2
    assert ctl.M_j == {2: {1}}
    assert not any(d['event'] == 'U' for d in ctl.decisions)
    ctl.scan(2)
    assert s.r_k == 0 and s.followups == 3
    assert not ctl.opportunities and not ctl.M_j
    ctl.service(1)
    assert s.sigma == 'CLEARED'
    assert ctl.opportunity_stats['safe_no_signal'] == 1
    assert ctl.opportunity_stats['guaranteed_measurements'] == 1
    assert math.isclose(ctl.T, world.t, abs_tol=1e-7)
    n = ctl.counts
    assert ctl.T == n['L_move']/5+n['N_sw']+5*n['N_meas']+3*n['N_clr']+2*n['N_succ']


def test_success_on_first_visited_side_cancels_other_side_and_does_not_reset_budget():
    _, _, ctl, s = pair_state()
    ctl.scan(2)  # This side is visible even when visited before "left".
    assert s.r_k == 1 and s.followups == 2
    assert not ctl.opportunities and not ctl.M_j
    before = len(ctl.actions)
    ctl.scan(1)
    assert len(ctl.actions) == before  # cancelled soft attachment never executes


def test_new_pair_needs_two_measurements_and_cannot_consume_reserved_budget():
    _, _, ctl, s = pair_state()
    fallback = model.Uhat(s.F, ctl.p, E=s.E)
    with pytest.raises(RuntimeError, match='reserved'):
        ctl.followup(s, (600., 600.), fallback)
    ctl._cancel_pair(1)
    s.r_k = 1
    ctl._assign_pair(s)
    assert not ctl.opportunities
    s.r_k = 0
    with pytest.raises(RuntimeError, match='budget'):
        ctl.followup(s, (600., 600.), fallback)


def test_ordinary_no_signal_keeps_original_optical_fallback():
    world, client, ctl = setup([world_source(1)])
    client.enter()
    s = ctl.channels[1]
    r = ctl.action('measure', 1, (0., 0.))
    ctl.direction(s, r['svd_deg'])
    ctl._cancel_pair(1)
    before = copy.deepcopy(s.F.directions)
    result = ctl.followup(s, (2000., 0.), model.Uhat(s.F, (2000., 0.), E=s.E, saved=s.H_k0))
    assert result == 'no_signal' and s.sigma == 'CLEARED'
    assert s.r_k == 2 and s.F.directions == before
    assert any(d['event'] == 'U' and d['reason'] == 'no_signal' for d in ctl.decisions)
    assert math.isclose(ctl.T, world.t, abs_tol=1e-7)


def test_impossible_second_failure_is_reported_not_false_absence():
    world, _, ctl, s = pair_state()
    ctl.scan(1)
    # Deliberately violate the fixed-orientation protocol after certification.
    world.sources[0]['orientation'] = 0.
    with pytest.raises(RuntimeError, match='certificate/protocol mismatch'):
        ctl.scan(2)
    assert s.r_k == 0 and s.sigma == 'FOUND'
    assert not s.scanned


def test_no_pair_uses_CU_and_search_tail_does_not_leave_FOUND_waiting():
    _, client, ctl = setup([world_source(1)])
    client.enter()
    s = ctl.channels[1]
    r = ctl.action('measure', 1, (0., 0.))
    ctl.remaining = []  # discovery at the end: no future opportunities
    ctl.direction(s, r['svd_deg'])
    for k, other in ctl.channels.items():
        if k != 1:
            other.sigma = 'ABSENT'
    assert ctl.next_tasks() == [('source', 1)]
    ctl.service(1)
    assert s.sigma == 'CLEARED' and ctl._finished()


def test_active_direction_hands_new_pair_back_to_global_route(monkeypatch):
    _, client, ctl = setup([world_source(1)])
    client.enter()
    z = list(ctl.Z)
    z[1], z[2] = (500., 500.), (500., -500.)
    ctl.Z, ctl.remaining = tuple(z), [1, 2]
    s = ctl.channels[1]
    r = ctl.action('measure', 1, (0., 0.))
    ctl.direction(s, r['svd_deg'])
    assert not ctl.opportunities
    calls = []

    def choose_followup(*args, **kwargs):
        # Exercise this legal interface branch without claiming the unchanged
        # conservative production C4 chooses it in the benchmark worlds.
        calls.append(1)
        assert len(calls) == 1
        u = model.Uhat(s.F, ctl.p, E=s.E, saved=s.H_k0)
        return SimpleNamespace(cost=u.cost-1, U=u, S=(1000., -1000.), fallback=u)

    monkeypatch.setattr(model, 'C4', choose_followup)
    ctl.service(1)
    assert s.sigma == 'FOUND' and s.r_k == 2
    assert ctl.opportunities[1].pending() == (1, 2)
    assert not any(d['event'] == 'U' for d in ctl.decisions)


def test_measurement_insert_counts_switch_back_in_frozen_plan():
    _, _, ctl = setup([])
    for s in ctl.channels.values():
        s.sigma = 'ABSENT'
    ctl.channels[1].sigma = 'UNKNOWN'
    tasks = [('search', 1), ('search', 2)]
    original = ctl.schedule_cost(tasks)
    ctl.M_j = {1: {2}}
    # CH1 -> extra CH2 at first point -> CH1 at the second point.
    assert math.isclose(ctl.schedule_cost(tasks)-original, 7., abs_tol=1e-8)


def test_frozen_completion_bound_includes_other_known_sources_and_unknown_reserve():
    _, _, ctl, s = pair_state()
    ctl._cancel_pair(1)
    tasks = [('search', 1), ('search', 2)]
    with_source = ctl.schedule_cost(tasks)
    s.sigma = 'CLEARED'
    no_source = ctl.schedule_cost(tasks)
    assert with_source >= no_source + 5
    ctl.channels[20].sigma = 'UNKNOWN'
    assert ctl._unknown_reserve() > 0
    # Source count upper limit is 16, not all 20 channels.
    for k in range(1, 17):
        ctl.channels[k].sigma = 'CLEARED'
    assert ctl._unknown_reserve() == 0


def test_forward_move_is_finite_strictly_improving_and_preserves_attachments():
    _, _, ctl, s = pair_state()
    ctl.scan(1)
    ctl.remaining = [3, 4, 5, 6, 2]
    ctl.forward_window = 2
    # A deterministic complete cost supplied to isolate the bounded Or-opt
    # rule; production cost and switching are exercised in the tests above.
    ctl.schedule_cost = lambda tasks: 10.*[key for _, key in tasks].index(2)
    ctl._promote(1)
    assert ctl.remaining == [3, 4, 2, 5, 6]
    decision = ctl.decisions[-1]
    assert decision['moved'] == 2 and decision['bound'] < decision['previous_bound']
    assert ctl.M_j == {2: {1}} and s.r_k == 1
    before = list(ctl.remaining)
    # next_tasks must not promote a second time without another observation.
    for k, other in ctl.channels.items():
        if k != 1:
            other.sigma = 'UNKNOWN'
    ctl.next_tasks()
    assert ctl.remaining == before


def test_forward_move_keeps_order_when_bound_does_not_improve():
    _, _, ctl, _ = pair_state()
    ctl.scan(1)
    ctl.remaining = [3, 4, 2]
    ctl.schedule_cost = lambda tasks: 10.
    ctl._promote(1)
    assert ctl.remaining == [3, 4, 2]
    assert ctl.decisions[-1]['moved'] == 0


def test_shared_guaranteed_anchor_moves_at_most_one_window_in_one_scan():
    world, _, ctl, _ = pair_state()
    world.sources[1] = world_source(2)
    s = ctl.channels[2]
    s.sigma = 'UNKNOWN'
    r = ctl.action('measure', 2, (0., 0.))
    ctl.direction(s, r['svd_deg'])
    ctl.followup(s, (1000., -1000.), model.Uhat(s.F, ctl.p, E=s.E))
    assert ctl.opportunities[1].pending() == ctl.opportunities[2].pending()
    ctl.remaining = [1, 3, 4, 5, 6, 2]
    ctl.schedule_cost = lambda tasks: 10.*[key for _, key in tasks].index(2)
    ctl.scan(1)
    assert ctl.remaining == [3, 2, 4, 5, 6]  # index 4 -> 1, not 4 -> 0
    assert ctl.opportunity_stats['safe_no_signal'] == 2
    assert ctl.opportunity_stats['promotions'] == 1
    assert ctl.M_j == {2: {1, 2}}


def test_clear_16_distinct_channels_stops_without_faking_UNKNOWN_absence():
    sources = [world_source(k, 0., 0., kind='omnidirectional') for k in range(1, 17)]
    world, _, ctl = setup(sources)
    result = ctl.run()
    assert result['completed'] and result['completion_reason'] == 'maximum_16_cleared'
    assert result['counts']['N_succ'] == 16 and result['counts']['N_meas'] == 16
    assert ctl.T == world.t == 175.
    assert all(ctl.channels[k].sigma == 'UNKNOWN' and not ctl.channels[k].scanned for k in range(17, 21))


def test_fewer_than_16_near_sources_still_scan_every_UNKNOWN_at_all_25_points():
    sources = [world_source(k, 0., 0., kind='omnidirectional') for k in range(1, 11)]
    world, _, ctl = setup(sources)
    result = ctl.run()
    assert result['completed'] and result['completion_reason'] == 'all_channels_resolved'
    assert len(ctl.visited) == 25 and len(world.cleared) == 10
    assert all(ctl.channels[k].sigma == 'ABSENT' and len(ctl.channels[k].scanned) == 25
               for k in range(11, 21))
    assert all(ctl.channels[k].followups <= 3 and ctl.channels[k].r_k >= 0 for k in ctl.channels)
    assert math.isclose(ctl.T, world.t, abs_tol=1e-7)


def test_invalid_planning_limits_are_rejected():
    with pytest.raises(ValueError, match='forward_window'):
        setup(forward_window=-1)
    with pytest.raises(ValueError, match='opt_rounds'):
        model.Controller(None, opt_rounds=4)
