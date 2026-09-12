"""Strategy consumes only public responses. Q1 is loaded, never copied or modified."""
import hashlib
import importlib.util
import math
from pathlib import Path
import sys
from typing import Protocol

class Client(Protocol):
    def enter(self): ...
    def measure(self, x, y, channel): ...
    def clear(self, x, y, channel): ...
    def exit(self): ...

DEFAULT_Q1 = Path(__file__).resolve().parents[2] / 'CUMCM-2026' / 'code' / '第一问.py'

def load_q1(path=DEFAULT_Q1):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f'Q1 localize module missing: {path}. Use --q1 PATH.')
    name = 'jammers_q1_' + hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name].localize


def localization_data(result, channel, measurements):
    point = lambda p: None if p is None else [float(v) for v in p]
    return dict(channel=channel, measurements=len(measurements), status=result.status,
                vertices=[point(p) for p in result.vertices], center=point(result.center),
                farthest_pair=None if result.farthest_pair is None else [point(p) for p in result.farthest_pair],
                diameter=result.diameter if result.diameter is not None and math.isfinite(result.diameter) else None,
                radius=result.radius, circle_covers=result.circle_covers, notes=result.notes)


def run_mission(client: Client, config):
    """Bounded Q1 demonstration; scan fixed public waypoints, localize one channel, exit.

    Yields derived observer notifications. No source positions or simulator references.
    This is deliberately not a Q2/Q3 exploration policy.
    """
    localize = load_q1(config.get('q1', DEFAULT_Q1))
    def checked(response):
        if not response.get('accepted'):
            raise RuntimeError(str(response))
        return response
    checked(client.enter())
    records, target = [], None
    try:
        waypoints = [(0, 0)] + [(1200*math.cos(i*math.pi/3), 1200*math.sin(i*math.pi/3)) for i in range(6)]
        for x, y in waypoints:
            for channel in range(1, 21):
                r = checked(client.measure(x, y, channel))
                if r['measure_result'] == 'near':
                    checked(client.clear(x, y, channel))
                elif r['measure_result'] == 'direction':
                    target = channel
                    records.append((x, y, r['svd_deg']))
                    break
            if target is not None:
                break
        if target is None:
            return
        x, y, bearing = records[0]
        angle = math.radians(bearing)
        # Offset sideways relative to observed bearing; never consult source truth.
        for offset in (200, -200, 400, -400):
            px, py = x-offset*math.sin(angle), y+offset*math.cos(angle)
            r = checked(client.measure(px, py, target))
            if r['measure_result'] == 'near':
                checked(client.clear(px, py, target))
                return
            if r['measure_result'] != 'direction':
                continue
            records.append((px, py, r['svd_deg']))
            result = localize(records)
            yield ('LocalizationUpdate', localization_data(result, target, records))
            if result.center is not None:
                cx, cy = map(float, result.center)
                if math.hypot(cx, cy) <= 2500:
                    # A demonstration probe, not a claimed guaranteed clear.
                    probe = checked(client.measure(cx, cy, target))
                    if probe['measure_result'] == 'direction':
                        records.append((cx, cy, probe['svd_deg']))
                        result = localize(records)
                        yield ('LocalizationUpdate', localization_data(result, target, records))
                    if probe['measure_result'] == 'near' or (result.circle_covers and result.radius is not None and result.radius <= 20):
                        p = (cx, cy) if probe['measure_result'] == 'near' else tuple(map(float, result.center))
                        checked(client.clear(*p, target))
                        return
    finally:
        checked(client.exit())
