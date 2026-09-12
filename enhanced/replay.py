"""Pure replay projection: results appear only when an event completes."""
import copy
import json
import math
from pathlib import Path


def validate(events):
    last = 0.0
    for i, e in enumerate(events):
        if e['seq'] != i or e['start'] < last or e['end'] < e['start']:
            raise ValueError('Invalid event order/timestamps')
        last = e['end']
    return events


def load_run(path):
    path = Path(path)
    events = validate([json.loads(line) for line in (path/'events.jsonl').read_text().splitlines() if line.strip()])
    return dict(events=events, sources=json.loads((path/'ground_truth.json').read_text()),
                metadata=json.loads((path/'scenario.json').read_text()))


def project(events, t):
    """Project observer state using virtual time alone.

    ``animation`` describes the current operation, with input-only data in
    ``active_event``. Results and effect timestamps become available only after
    completion. Rebuilding these values on every call makes pause and seeking
    independent of rendering frame rate and previously projected times.
    """
    state = dict(time=t, position=[0., 0.], channel=1, status='idle', distance=0., measure_count=0,
                 switch_count=0, failed_clear_count=0, detected=set(), cleared=set(), trajectory=[[0., 0.]],
                 measurements=[], actions=[], localizations={}, clear_actions=[], candidates=[],
                 breakdown=dict(moving=0., measuring=0., switching=0., clear=0.))
    animation = dict(phase=0., duration=0., elapsed=0., action_type='idle',
                     action_channel=None, heading_deg=0., gait_phase=0.,
                     last_measure_time=None, last_clear_time=None, latest_clear=None,
                     latest_localization_time=None)
    for e in events:
        if e['start'] > t:
            break
        d, kind = e['data'], e['type']
        duration = e['end']-e['start']
        elapsed = min(duration, max(0., t-e['start']))
        complete = t >= e['end']
        key = {'Move':'moving', 'Measure':'measuring', 'ChannelSwitch':'switching', 'Clear':'clear'}.get(kind)
        if key:
            state['breakdown'][key] += elapsed
            if not complete:
                state['status'] = key
        if kind == 'Move':
            ratio = elapsed/duration if duration else 1
            state['position'] = [a+(b-a)*ratio for a,b in zip(d['origin'], d['destination'])]
            state['distance'] += d['distance']*ratio
            state['trajectory'].append(state['position'][:])
            dx, dy = (b-a for a, b in zip(d['origin'], d['destination']))
            if dx or dy:
                animation['heading_deg'] = math.degrees(math.atan2(dy, dx)) % 360
        if kind == 'Clear' and not complete:
            state['active_clear'] = dict(d, result='pending')
        if not complete:
            if key:
                animation.update(phase=elapsed/duration if duration else 1., duration=duration,
                                 elapsed=elapsed, action_type=kind,
                                 action_channel=d.get('channel', state['channel']))
                # Whitelist operation inputs: copying the complete event here
                # would disclose a future bearing or clear result to the UI.
                input_keys = {
                    'Move': ('origin', 'destination', 'distance'),
                    'ChannelSwitch': ('previous', 'channel'),
                    'Measure': ('position', 'channel'),
                    'Clear': ('position', 'channel', 'radius'),
                }[kind]
                state['active_event'] = dict(type=kind, start=e['start'], end=e['end'],
                                            data={k: copy.deepcopy(d[k]) for k in input_keys if k in d})
            break
        if kind == 'ChannelSwitch':
            state['channel'] = d['channel']
            state['switch_count'] += 1
        elif kind == 'Measure':
            animation['last_measure_time'] = e['end']
            state['measure_count'] += 1
            state['actions'].append(d)
            if d['result'] in ('direction', 'near'):
                state['detected'].add(d['channel'])
            if d['result'] == 'direction':
                state['measurements'].append(d)
        elif kind == 'Clear':
            animation['last_clear_time'] = e['end']
            animation['latest_clear'] = copy.deepcopy(d)
            state['actions'].append(d)
            state['clear_actions'].append(d)
            if d['result'] == 'success':
                state['cleared'].add(d['channel'])
            else:
                state['failed_clear_count'] += 1
        elif kind == 'LocalizationUpdate':
            animation['latest_localization_time'] = e['end']
            state['localizations'][d['channel']] = d
        elif kind == 'CandidatePoints':
            state['candidates'] = d['points']
        elif kind == 'MissionEnd':
            state['status'] = 'mission ended'
    # A fixed stride length keeps foot placement stable when paused or scrubbed.
    animation['gait_phase'] = (state['distance']/2.8) % 1.
    state['animation'] = animation
    state['heading_deg'] = animation['heading_deg']
    state['action_phase'] = animation['phase']
    return state


def metrics(events, source_count):
    end = events[-1]['end'] if events else 0
    s = project(events, end)
    first, durations = {}, []
    for e in events:
        d = e['data']
        if e['type'] == 'Measure' and d['result'] in ('near', 'direction'):
            first.setdefault(d['channel'], e['end'])
        if e['type'] == 'Clear' and d['result'] == 'success' and d['channel'] in first:
            durations.append(e['end']-first[d['channel']])
    return dict(virtual_time_s=end, distance_m=s['distance'], measure_count=s['measure_count'],
                switch_count=s['switch_count'], failed_clear_count=s['failed_clear_count'],
                detected=len(s['detected']), cleared=len(s['cleared']), source_count=source_count,
                clear_rate=len(s['cleared'])/source_count,
                average_localization_clear_time_s=sum(durations)/len(durations) if durations else None,
                time_breakdown=s['breakdown'])
