import copy
import math

import pytest

from enhanced.playback import advance_playback


@pytest.fixture
def events():
    intervals = [
        ('MissionStart', 0., 0.),
        ('Move', 0., 10.),
        ('ChannelSwitch', 10., 11.),
        ('Measure', 11., 16.),
        ('LocalizationUpdate', 16., 16.),
        ('Clear', 16., 21.),
        ('Move', 21., 31.),
        ('MissionEnd', 31., 31.),
    ]
    return [dict(seq=i, type=kind, start=start, end=end, data={})
            for i, (kind, start, end) in enumerate(intervals)]


def test_fast_move_enters_switch_at_capped_rate(events):
    # 0.02 real seconds finish the move; 0.08 seconds advance the switch 0.4 s.
    assert advance_playback(events, 9., .1, 50.) == pytest.approx(10.4)


def test_remaining_real_time_carries_from_move_through_switch_into_measure(events):
    # Move: 0.02 s, switch: 0.2 s, measure: the remaining 0.18 s at 5x.
    assert advance_playback(events, 9., .4, 50.) == pytest.approx(11.9)
    assert advance_playback(events, 10.99, .01, 50.) == pytest.approx(11.04)


def test_clear_uses_cap_then_following_move_restores_selected_speed(events):
    assert advance_playback(events, 16., .1, 50.) == pytest.approx(16.5)
    # The last second of clearing takes 0.2 real seconds; movement gets 0.05 s.
    assert advance_playback(events, 20., .25, 50.) == pytest.approx(23.5)


def test_slow_motion_never_accelerates_slower_selected_speed(events):
    assert advance_playback(events, 10., 2., .5) == pytest.approx(11.)
    assert advance_playback(events, 11., 1., 2.) == pytest.approx(13.)


def test_disabled_slow_motion_uses_uniform_selected_speed(events):
    assert advance_playback(events, 9., .2, 50., slow_actions=False) == pytest.approx(19.)


def test_zero_time_and_zero_speed_pause_at_clamped_time(events):
    assert advance_playback(events, 12., 0., 50.) == 12.
    assert advance_playback(events, 12., 50., 0.) == 12.
    assert advance_playback(events, -10., 0., 50.) == 0.
    assert advance_playback(events, 100., 0., 50.) == 31.


def test_end_clamping_empty_replay_and_zero_duration_events(events):
    assert advance_playback(events, 0., 1000., 50.) == 31.
    assert advance_playback(events, 31., 1., 50.) == 31.
    assert advance_playback(events, 99., 1., 50.) == 31.
    assert advance_playback([], 5., 1., 50.) == 0.
    assert advance_playback([dict(type='MissionEnd', start=0., end=0.)], 0., 1., 50.) == 0.
    assert advance_playback(events, 16., .1, 50.) == pytest.approx(16.5)


def test_gaps_use_selected_speed_and_preserve_real_time_remainder():
    events = [dict(type='Measure', start=10., end=15.)]
    assert advance_playback(events, 0., .3, 50.) == pytest.approx(10.5)


def test_single_tick_and_many_frames_advance_equally_without_mutating_logs(events):
    original = copy.deepcopy(events)
    current = 9.
    for _ in range(100):
        current = advance_playback(events, current, .01, 50.)
    assert current == pytest.approx(advance_playback(events, 9., 1., 50.))
    assert events == original


@pytest.mark.parametrize('field,value', [
    ('speed', -1.), ('speed', math.inf), ('speed', math.nan),
    ('real_dt', -1.), ('real_dt', -math.inf), ('real_dt', math.nan),
    ('current_time', math.inf), ('current_time', math.nan),
    ('speed', None), ('real_dt', 'invalid'),
])
def test_invalid_numeric_inputs_raise_value_error(events, field, value):
    arguments = dict(current_time=0., real_dt=.1, speed=50.)
    arguments[field] = value
    with pytest.raises(ValueError):
        advance_playback(events, **arguments)
