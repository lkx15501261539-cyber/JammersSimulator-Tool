"""World truth and protocol rules. No strategy or Qt imports."""
from dataclasses import dataclass, asdict
import copy
import hashlib
import json
import math
import random
import time

SCENARIOS = ('uniform', 'center-biased', 'boundary-biased', 'clustered', 'repulsive')

@dataclass(frozen=True)
class ScenarioConfig:
    seed: int = 42
    scenario: str = 'uniform'
    count: int | None = None
    error_model: str = 'deterministic_hash_fixed'
    min_separation: float = 250.0
    problem: int = 3


def generate(config):
    if config.problem not in (3, 4):
        raise ValueError('problem must be 3 or 4')
    if config.scenario not in SCENARIOS or config.error_model not in ('deterministic_hash_fixed', 'worst_edge', 'baseline_fixed_field'):
        raise ValueError('Unknown scenario or error model')
    rng = random.Random(config.seed)
    count = rng.randint(10, 16) if config.count is None else config.count
    if not 10 <= count <= 16 or not 0 <= config.min_separation <= 3600:
        raise ValueError('Invalid source count or separation')
    channels = rng.sample(range(1, 21), count)
    centers = [(rng.uniform(-900, 900), rng.uniform(-900, 900)) for _ in range(3)]
    sources = []
    for channel in channels:
        for _ in range(10000):
            u, a = rng.random(), rng.uniform(0, math.tau)
            r = 1800 * (u if config.scenario == 'center-biased' else
                        u ** .15 if config.scenario == 'boundary-biased' else math.sqrt(u))
            x, y = r * math.cos(a), r * math.sin(a)
            if config.scenario == 'clustered':
                cx, cy = rng.choice(centers)
                x, y = rng.gauss(cx, 200), rng.gauss(cy, 200)
            if math.hypot(x, y) > 1800:
                continue
            if config.scenario == 'repulsive' and any(math.hypot(x-s['x'], y-s['y']) < config.min_separation for s in sources):
                continue
            break
        else:
            raise ValueError('Cannot place sources with requested separation')
        sources.append(dict(channel=channel, x=x, y=y, recv_radius=rng.uniform(1000, 1500),
                            source_type='omnidirectional', orientation=0.0))
    # Separate PRNG preserves the Q3 positions, radii and seeded sequence.
    if config.problem == 4:
        directional_rng = random.Random(config.seed ^ 0x5144)
        for index, source in enumerate(sources):
            source['source_type'] = 'directional' if index % 2 == 0 else 'omnidirectional'
            source['orientation'] = directional_rng.uniform(0, 360)
    return sources


class World:
    def __init__(self, config=ScenarioConfig()):
        self.config = config
        self.sources = generate(config)
        self.events, self.observations = [], []
        self.cache = {}
        self.position = [0.0, 0.0]
        self.channel, self.t = 1, 0.0
        self.active = self.started = False
        self.cleared = set()
        self.robot = None

    def emit(self, kind, duration=0.0, **data):
        event = dict(seq=len(self.events), type=kind, start=self.t, end=self.t+duration, data=data)
        self.events.append(event)
        self.t += duration
        return event

    def request(self, path, payload):
        payload = copy.deepcopy(payload)
        rid = payload.get('request_id') if isinstance(payload, dict) else None
        if not isinstance(rid, str) or not rid:
            return dict(accepted=False, error='request_id_required')
        signature = json.dumps([path, payload], sort_keys=True)
        if rid in self.cache:
            old, response = self.cache[rid]
            return copy.deepcopy(response) if old == signature else dict(accepted=False, error='request_id_conflict')
        try:
            response = self._request(path, payload)
        except (ValueError, TypeError, KeyError) as exc:
            response = dict(accepted=False, error=str(exc))
        self.cache[rid] = signature, copy.deepcopy(response)
        self.observations.append(dict(index=len(self.observations), path=path, request=payload, response=copy.deepcopy(response)))
        return copy.deepcopy(response)

    def _request(self, path, p):
        if path not in ('/enter', '/measure', '/clear', '/exit'):
            raise ValueError('unknown_endpoint')
        robot = p.get('robot_id')
        if not isinstance(robot, str) or not robot or p.get('arena_id', 'default') != 'default':
            raise ValueError('invalid_identity')
        if path == '/enter':
            if self.started:
                raise ValueError('mission_already_started; create a new world')
            self.active = self.started = True
            self.robot, self.wall_start = robot, time.monotonic()
            self.emit('MissionStart', position=self.position[:], channel=1)
            return dict(accepted=True, virtual_time_s=0, max_virtual_duration_s=360000,
                        max_real_duration_s=1200, remaining_real_duration_s=1200)
        if not self.active or robot != self.robot:
            raise ValueError('session_not_active_or_wrong_robot')
        if path == '/exit':
            self.active = False
            self.emit('MissionEnd', reason='user_exit')
            return dict(accepted=True, virtual_time_s=self.t, exit_reason='user_exit')
        c = p['channel']
        if type(c) is not int or not 1 <= c <= 20:
            raise ValueError('invalid_channel')
        pos = p['position']
        xy = [pos['x'], pos['y']]
        if any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 2e6 for v in xy):
            raise ValueError('invalid_position')
        xy = [float(v) for v in xy]
        source = next((s for s in self.sources if s['channel'] == c and c not in self.cleared), None)
        distance = math.dist(self.position, xy)
        move = distance/5
        switch = float(path == '/measure' and c != self.channel)
        success = bool(source and math.dist(xy, [source['x'], source['y']]) <= 20)
        duration = 5 if path == '/measure' or success else 3
        if self.t+move+switch+duration > 360000 or time.monotonic()-self.wall_start > 1200:
            raise ValueError('mission_time_limit')
        start = self.t
        if distance:
            self.emit('Move', move, origin=self.position[:], destination=xy, distance=distance)
        self.position = xy
        if switch:
            self.emit('ChannelSwitch', 1, previous=self.channel, channel=c)
            self.channel = c
        base = dict(accepted=True, position=dict(zip(('x', 'y'), xy)), channel=c)
        if path == '/measure':
            result, angle = 'no_signal', None
            if source:
                d = math.dist(xy, [source['x'], source['y']])
                outward = math.degrees(math.atan2(xy[1]-source['y'], xy[0]-source['x']))
                covered = (d == 0 or source['source_type'] != 'directional' or
                           abs((outward-source['orientation']+180)%360-180) <= 90)
                if d <= source['recv_radius'] and covered:
                    result = 'near' if d <= 5 else 'direction'
                    if result == 'direction':
                        # Normalize signed zero; binary float hex preserves distinct coordinates.
                        key = ':'.join([str(self.config.seed), str(c), *(float(v or 0).hex() for v in xy)])
                        h = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big')
                        error = (1 if h%2 else -1) if self.config.error_model == 'worst_edge' else 2*h/(2**64-1)-1
                        if self.config.error_model == 'baseline_fixed_field':
                            error = .994*math.sin(.00637*xy[0]+.00413*xy[1]+1.713*c+.017*self.config.seed)
                        truth = math.degrees(math.atan2(source['y']-xy[1], source['x']-xy[0]))
                        angle = round((truth+error)%360, 2)%360
            base['measure_result'] = result
            if angle is not None:
                base['svd_deg'] = angle
            self.emit('Measure', 5, position=xy, channel=c, result=result, svd_deg=angle)
            base['cost_breakdown'] = dict(movement_s=move, switch_channel_s=switch, detection_s=5)
        else:
            result = 'success' if success else 'no_target_in_range'
            self.emit('Clear', duration, position=xy, channel=c, result=result, radius=20)
            if success:
                self.cleared.add(c)
            base['clear_result'] = result
            base['cost_breakdown'] = dict(movement_s=move, detection_s=3, clear_s=2 if success else 0)
        base.update(virtual_time_s=self.t, consumed_virtual_duration_s=self.t-start)
        return base
