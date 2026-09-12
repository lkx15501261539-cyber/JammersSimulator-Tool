"""Reproduce paired Q4 v1/v2 physical simulations, without GUI overhead."""
import argparse
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from enhanced.q4_adapter import _controller_module, CODE, _MODEL_FILES
from enhanced.runner import ResponseClient
from enhanced.world import ScenarioConfig, World, SCENARIOS


def compare(seeds, scenarios):
    hashes = {name: hashlib.sha256((CODE/name).read_bytes()).hexdigest()
              for name in _MODEL_FILES['q4_opportunity_v2']}
    rows = []
    for scenario in scenarios:
        for seed in seeds:
            config = ScenarioConfig(problem=4, seed=seed, scenario=scenario, error_model='worst_edge')
            row = dict(config=asdict(config), models={})
            truth = None
            for model in ('q4_cu', 'q4_opportunity_v2'):
                world = World(config)
                digest = hashlib.sha256(json.dumps(world.sources, sort_keys=True).encode()).hexdigest()
                if truth is not None and digest != truth:
                    raise AssertionError('paired worlds differ')
                truth = digest
                controller = _controller_module(model).Controller(ResponseClient(world.request))
                start = time.perf_counter()
                result = controller.run()
                elapsed = time.perf_counter() - start
                n = result['counts']
                exact_T = n['L_move']/5 + n['N_sw'] + 5*n['N_meas'] + 3*n['N_clr'] + 2*n['N_succ']
                if not (result['completed'] and len(world.cleared) == len(world.sources)
                        and math.isclose(exact_T, result['T'], abs_tol=1e-7)
                        and math.isclose(world.t, result['T'], abs_tol=1e-7)):
                    raise AssertionError(f'{scenario}/{seed}/{model}: {result}')
                for s in controller.channels.values():
                    assert 0 <= s.r_k <= 3 and s.followups <= 3
                    if s.sigma == 'ABSENT':
                        assert len(s.scanned) == 25 and set(s.scanned.values()) == {'no_signal'}
                row['models'][model] = dict(completed=True, T=result['T'], counts=n,
                    cleared=len(world.cleared), source_count=len(world.sources),
                    completion_reason=result.get('completion_reason', 'all_channels_resolved'),
                    opportunities=result.get('opportunities'), wall_seconds=elapsed)
                print(f'{scenario} seed={seed} {model}: T={result["T"]:.3f}s '
                      f'cleared={len(world.cleared)}/{len(world.sources)} wall={elapsed:.2f}s', flush=True)
            row['world_sha256'] = truth
            a, b = row['models']['q4_cu']['T'], row['models']['q4_opportunity_v2']['T']
            row['v2_time_change_percent'] = (b/a - 1)*100
            rows.append(row)
    if any(hashlib.sha256((CODE/name).read_bytes()).hexdigest() != digest
           for name, digest in hashes.items()):
        raise RuntimeError('model source changed during comparison; rerun on stable source')
    return dict(schema_version=1, error_rule='worst_edge: deterministic bounded ±2 degrees',
                timing_formula='L_move/5+N_sw+5*N_meas+3*N_clr+2*N_succ',
                source_files_sha256=hashes,
                rows=rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seeds', type=int, nargs='+', default=[42, 47])
    parser.add_argument('--scenarios', choices=SCENARIOS, nargs='+', default=list(SCENARIOS))
    parser.add_argument('--output', type=Path, default=Path('q4-v2-comparison.json'))
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output exists; choose another path')
    result = compare(args.seeds, args.scenarios)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
