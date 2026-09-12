"""Animation follows replay time without revealing uncompleted observations."""
import copy
import math

import pytest

from enhanced.replay import metrics, project, validate


@pytest.fixture
def events():
    rows = [
        ('MissionStart', 0, 0, dict(position=[0., 0.], channel=1)),
        ('Move', 0, 10, dict(origin=[0., 0.], destination=[30., 40.], distance=50.)),
        ('ChannelSwitch', 10, 11, dict(previous=1, channel=2)),
        ('Measure', 11, 16, dict(position=[30., 40.], channel=2,
                                result='direction', svd_deg=12.34)),
        ('LocalizationUpdate', 16, 16, dict(channel=2, measurements=2,
                                            vertices=[[20., 30.], [40., 50.]])),
        ('Move', 16, 26, dict(origin=[30., 40.], destination=[-20., 40.], distance=50.)),
        ('Clear', 26, 31, dict(position=[-20., 40.], channel=2, radius=20., result='success')),
        ('Measure', 31, 36, dict(position=[-20., 40.], channel=2,
                                result='no_signal', svd_deg=None)),
        ('Clear', 36, 39, dict(position=[-20., 40.], channel=7,
                              radius=20., result='no_target_in_range')),
        ('MissionEnd', 39, 39, dict(reason='user_exit')),
    ]
    return validate([dict(seq=i, type=kind, start=start, end=end, data=data)
                     for i, (kind, start, end, data) in enumerate(rows)])


def test_midpoint_motion_heading_and_deterministic_gait(events):
    state = project(events, 5)
    assert state['position'] == [15., 20.]
    assert state['distance'] == 25.
    assert state['heading_deg'] == pytest.approx(math.degrees(math.atan2(40, 30)))
    assert state['animation']['heading_deg'] == state['heading_deg']
    assert state['animation']['gait_phase'] == pytest.approx((25./2.8) % 1.)
    assert state['active_event']['data']['destination'] == [30., 40.]
    assert project(events, 5) == state


def test_stopped_heading_and_gait_hold_until_next_move(events):
    moving = project(events, 9)['heading_deg']
    for t in (10, 10.5, 11, 13.5, 15.999):
        state = project(events, t)
        assert state['heading_deg'] == moving
        assert state['animation']['gait_phase'] == pytest.approx((50./2.8) % 1.)
    # At a shared boundary, all completed events are applied, then the next
    # operation begins, matching the existing projection ordering.
    at_turn = project(events, 16)
    assert at_turn['heading_deg'] == 180.
    assert at_turn['animation']['action_type'] == 'Move'
    assert at_turn['animation']['phase'] == 0.
    assert at_turn['animation']['latest_localization_time'] == 16
    assert project(events, 28)['heading_deg'] == 180.


@pytest.mark.parametrize('t,kind,duration,elapsed,phase,channel', [
    (5, 'Move', 10, 5, .5, 1),
    (10.5, 'ChannelSwitch', 1, .5, .5, 2),
    (12, 'Measure', 5, 1, .2, 2),
    (28, 'Clear', 5, 2, .4, 2),
    (37.5, 'Clear', 3, 1.5, .5, 7),
])
def test_action_timing(events, t, kind, duration, elapsed, phase, channel):
    state = project(events, t)
    context = state['animation']
    assert context['action_type'] == kind
    assert context['duration'] == duration
    assert context['elapsed'] == elapsed
    assert context['phase'] == phase == state['action_phase']
    assert context['action_channel'] == channel
    assert state['active_event']['type'] == kind
    assert state['active_event']['end']-state['active_event']['start'] == duration


def test_measurement_and_localization_effects_wait_for_completion(events):
    for t in (11, 12, 15.999):
        state = project(events, t)
        assert state['animation']['last_measure_time'] is None
        assert state['animation']['latest_localization_time'] is None
        assert state['active_event']['data'] == dict(position=[30., 40.], channel=2)
        assert state['measurements'] == []
        assert state['actions'] == []
        assert state['localizations'] == {}
    complete = project(events, 16)
    assert complete['animation']['last_measure_time'] == 16
    assert complete['measurements'][0]['svd_deg'] == 12.34
    assert complete['localizations'][2]['measurements'] == 2


def test_clear_effects_wait_for_completion_and_channel_is_preserved(events):
    pending = project(events, 30.999)
    assert pending['animation']['last_clear_time'] is None
    assert pending['animation']['latest_clear'] is None
    assert pending['active_event']['data'] == dict(position=[-20., 40.], channel=2, radius=20.)
    assert pending['active_clear']['result'] == 'pending'
    assert pending['clear_actions'] == []
    assert pending['cleared'] == set()
    complete = project(events, 31)
    assert complete['animation']['last_clear_time'] == 31
    assert complete['animation']['latest_clear']['result'] == 'success'
    assert complete['animation']['action_type'] == 'Measure'
    assert 'active_clear' not in complete
    later_pending = project(events, 38)
    assert later_pending['animation']['latest_clear']['result'] == 'success'
    assert later_pending['animation']['last_clear_time'] == 31
    assert later_pending['animation']['action_channel'] == 7
    assert later_pending['channel'] == 2
    assert 'result' not in later_pending['active_event']['data']
    assert project(events, 39)['animation']['latest_clear']['result'] == 'no_target_in_range'


def test_seeking_backwards_drops_later_animation_effects(events):
    original = copy.deepcopy(events)
    expected = project(events, 12)
    project(events, 39)
    project(events, 28)
    assert project(events, 12) == expected
    assert project(events, -1)['heading_deg'] == 0.
    assert project(events, -1)['animation']['latest_clear'] is None
    # Renderer-local mutation of the added context must not alter the replay.
    state = project(events, 5)
    state['active_event']['data']['destination'][0] = 999
    state = project(events, 31)
    state['animation']['latest_clear']['position'][0] = 999
    assert events == original


def test_zero_length_move_preserves_heading_and_idle_context(events):
    rows = events[:2] + [dict(seq=2, type='Move', start=10, end=10,
                             data=dict(origin=[30., 40.], destination=[30., 40.], distance=0.))]
    state = project(validate(rows), 10)
    assert state['heading_deg'] == pytest.approx(math.degrees(math.atan2(40, 30)))
    assert state['animation']['action_type'] == 'idle'
    assert state['animation']['duration'] == state['animation']['elapsed'] == state['action_phase'] == 0
    assert 'active_event' not in state
    assert project([], 0)['animation']['gait_phase'] == 0


def test_animation_context_does_not_change_metrics(events):
    assert metrics(events, 2) == dict(
        virtual_time_s=39, distance_m=100., measure_count=2, switch_count=1,
        failed_clear_count=1, detected=1, cleared=1, source_count=2, clear_rate=.5,
        average_localization_clear_time_s=15.,
        time_breakdown=dict(moving=20., measuring=10., switching=1., clear=8.))
