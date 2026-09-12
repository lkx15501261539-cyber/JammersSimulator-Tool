"""Pure replay projection: results appear only when an event completes."""
import copy
import json
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
    state = dict(time=t, position=[0., 0.], channel=1, status='idle', distance=0., measure_count=0,
                 switch_count=0, failed_clear_count=0, detected=set(), cleared=set(), trajectory=[[0., 0.]],
                 measurements=[], actions=[], localizations={}, clear_actions=[], candidates=[],
                 breakdown=dict(moving=0., measuring=0., switching=0., clear=0.))
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
        if kind == 'Clear' and not complete:
            state['active_clear'] = dict(d, result='pending')
        if not complete:
            break
        if kind == 'ChannelSwitch':
            state['channel'] = d['channel']
            state['switch_count'] += 1
        elif kind == 'Measure':
            state['measure_count'] += 1
            state['actions'].append(d)
            if d['result'] in ('direction', 'near'):
                state['detected'].add(d['channel'])
            if d['result'] == 'direction':
                state['measurements'].append(d)
        elif kind == 'Clear':
            state['actions'].append(d)
            state['clear_actions'].append(d)
            if d['result'] == 'success':
                state['cleared'].add(d['channel'])
            else:
                state['failed_clear_count'] += 1
        elif kind == 'LocalizationUpdate':
            state['localizations'][d['channel']] = d
        elif kind == 'CandidatePoints':
            state['candidates'] = d['points']
        elif kind == 'MissionEnd':
            state['status'] = 'mission ended'
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
