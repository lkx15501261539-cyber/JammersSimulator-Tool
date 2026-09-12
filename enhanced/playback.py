"""Presentation clock for immutable, ordered replay events.

Action slow motion changes only the rate at which a viewer traverses virtual
time. Event timestamps, mission metrics, and model calculations stay untouched.
"""
import math
from collections.abc import Mapping, Sequence


SLOW_ACTIONS = frozenset({'Measure', 'ChannelSwitch', 'Clear'})
ACTION_SPEED_LIMIT = 5.0


def _finite_number(value, name):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{name} must be a finite number') from exc
    if not math.isfinite(number):
        raise ValueError(f'{name} must be a finite number')
    return number


def advance_playback(
    events: Sequence[Mapping],
    current_time: float,
    real_dt: float,
    speed: float,
    slow_actions: bool = True,
) -> float:
    """Advance a replay by ``real_dt`` wall-clock seconds and return virtual time.

    ``events`` must have ordered, nonoverlapping ``start``/``end`` intervals,
    as checked by ``replay.validate``. Movement and gaps use ``speed``; when
    enabled, measurement, channel switching, and clearing use at most 5x.
    Unspent wall time carries through every crossed boundary, so a fast move
    cannot carry its movement rate into the next short action.

    Zero speed or elapsed time pauses. Negative speed/elapsed time and any
    non-finite numeric input raise ValueError. The current time is clamped to
    the mission interval; an empty replay has duration zero. Input data is
    never modified.
    """
    position = _finite_number(current_time, 'current_time')
    remaining = _finite_number(real_dt, 'real_dt')
    multiplier = _finite_number(speed, 'speed')
    if remaining < 0 or multiplier < 0:
        raise ValueError('real_dt and speed must be nonnegative')
    if not events:
        return 0.0
    duration = _finite_number(events[-1]['end'], 'mission duration')
    if duration < 0:
        raise ValueError('mission duration must be nonnegative')
    position = min(duration, max(0.0, position))
    if remaining == 0 or multiplier == 0 or position == duration:
        return position

    for event in events:
        end = event['end']
        if end <= position:
            continue
        action_speed = (min(multiplier, ACTION_SPEED_LIMIT)
                        if slow_actions and event['type'] in SLOW_ACTIONS
                        else multiplier)
        # The first interval is any gap preceding this event. The second is
        # the event itself; zero-duration intervals are simply ignored.
        for boundary, rate in ((event['start'], multiplier), (end, action_speed)):
            if boundary <= position:
                continue
            real_needed = (boundary-position)/rate
            if remaining < real_needed:
                return min(float(boundary), position+remaining*rate)
            position = float(boundary)
            remaining = max(0.0, remaining-real_needed)
            if remaining == 0:
                return position
    return duration
