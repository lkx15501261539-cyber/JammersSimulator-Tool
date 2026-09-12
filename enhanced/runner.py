"""Composition root and persistence. Strategies receive only a response client."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from .world import World, ScenarioConfig
from .strategy import run_mission, DEFAULT_Q1
from .replay import metrics


class ResponseClient:
    __slots__ = ('__request', '__counter')
    def __init__(self, request):
        self.__request, self.__counter = request, 0
    def _call(self, path, **fields):
        self.__counter += 1
        return self.__request(path, dict(arena_id='default', robot_id='q1-demo',
                                        request_id=f'demo-{self.__counter}', **fields))
    def enter(self): return self._call('/enter')
    def measure(self, x, y, channel): return self._call('/measure', position=dict(x=x, y=y), channel=channel)
    def clear(self, x, y, channel): return self._call('/clear', position=dict(x=x, y=y), channel=channel)
    def exit(self): return self._call('/exit')


def save_run(world, directory, metadata):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    def write(name, value):
        (directory/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    write('scenario.json', metadata)
    write('ground_truth.json', world.sources)
    for name, records in [('events.jsonl', world.events), ('observations.jsonl', world.observations)]:
        (directory/name).write_text(''.join(json.dumps(r, ensure_ascii=False, allow_nan=False)+'\n' for r in records), encoding='utf-8')
    result = metrics(world.events, len(world.sources))
    result.update(metadata)
    write('metrics.json', result)
    return result


def simulate(config=ScenarioConfig(), output=None, q1=DEFAULT_Q1, strategy=run_mission,
             strategy_name='Q1 Integration Demo', strategy_version='1.0'):
    world = World(config)
    metadata = dict(schema_version=1, **asdict(config), strategy=strategy_name, strategy_version=strategy_version,
                    q1_sha256=hashlib.sha256(Path(q1).read_bytes()).hexdigest())
    for kind, data in strategy(ResponseClient(world.request), dict(q1=q1)):
        if kind not in ('LocalizationUpdate', 'CandidatePoints'):
            raise ValueError('Strategy may only emit derived observer notifications')
        world.emit(kind, **data)
    if output is not None:
        save_run(world, output, metadata)
    return dict(events=world.events, sources=world.sources, metadata=metadata)
