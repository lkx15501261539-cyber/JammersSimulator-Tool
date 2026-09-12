import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
from .world import ScenarioConfig, SCENARIOS
from .strategy import DEFAULT_Q1
from .baseline import MODEL_LABELS


def main():
    parser = argparse.ArgumentParser(description='Enhanced Jammers simulation / desktop replay')
    parser.add_argument('mode', choices=['gui', 'demo', 'baseline', 'q4', 'batch', 'serve'])
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--problem', type=int, choices=[3, 4], default=3,
                        help='Problem number; Q4 enables mixed directional/omnidirectional sources')
    parser.add_argument('--scenario', choices=SCENARIOS, default='uniform')
    parser.add_argument('--error-model', choices=['deterministic_hash_fixed', 'worst_edge', 'baseline_fixed_field'],
                        help='Defaults to baseline_fixed_field for gui/baseline, deterministic_hash_fixed otherwise')
    parser.add_argument('--model', choices=[*MODEL_LABELS, 'q4_cu'],
                        help='Defaults to q4_cu for problem 4, hexagon_v1 otherwise')
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--q1', type=Path, default=DEFAULT_Q1)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--replay', type=Path)
    parser.add_argument('--seeds', default='42,43,44')
    parser.add_argument('--port', type=int, default=2027)
    args = parser.parse_args()
    if args.mode == 'q4' or args.model == 'q4_cu':
        args.problem = 4
    if args.problem == 4 and args.model in MODEL_LABELS and args.mode != 'serve':
        parser.error('Problem 4 requires --model q4_cu; the ZIP baseline models are Q3 only')
    if args.problem == 4 and args.mode in ('demo', 'batch'):
        parser.error('For Q4 use gui, q4, baseline --model q4_cu, or serve')
    args.model = args.model or ('q4_cu' if args.problem == 4 else 'hexagon_v1')
    if args.error_model is None:
        args.error_model = 'baseline_fixed_field' if args.mode in ('gui', 'baseline') else 'deterministic_hash_fixed'
    config = ScenarioConfig(seed=args.seed, scenario=args.scenario, error_model=args.error_model,
                            problem=args.problem)
    output = args.output or Path('runs')/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    if args.mode == 'gui':
        from .ui import launch
        launch(config, args.q1, args.replay, args.model, args.archive)
    elif args.mode == 'q4' or args.mode == 'baseline' and args.model == 'q4_cu':
        from .q4_adapter import run_q4
        run = run_q4(config, output,
                     progress=lambda p: print(f"{p['phase']} | {p['action_count']} actions | {p['virtual_time_s']:.1f} s", flush=True))
        print(run['metadata'])
    elif args.mode == 'baseline':
        from .baseline import run_baseline, default_archive
        run = run_baseline(config, args.archive or default_archive(args.model), args.model, output,
                           progress=lambda p: print(f"{p['phase']} | {p['action_count']} actions | {p['virtual_time_s']:.1f} s", flush=True))
        print(run['metadata'])
    elif args.mode == 'serve':
        from .server import make_server
        print(f'Enhanced Mock http://127.0.0.1:{args.port}; logs: {output}', flush=True)
        make_server(config, args.port, output).serve_forever()
    elif args.mode == 'demo':
        from .runner import simulate
        simulate(config, output, args.q1)
        print((output/'metrics.json').read_text())
    else:
        from .runner import simulate
        output.mkdir(parents=True, exist_ok=False)
        rows, grouped = [], {}
        for scenario in SCENARIOS:
            group = []
            for seed in map(int, args.seeds.split(',')):
                directory = output/f'{scenario}-{seed}'
                simulate(ScenarioConfig(seed, scenario, error_model=args.error_model), directory, args.q1)
                row = json.loads((directory/'metrics.json').read_text())
                row['replay_path'] = str(directory.resolve())
                rows.append(row)
                group.append(row)
            worst = min(group, key=lambda r: (r['clear_rate'], -r['virtual_time_s']))
            keys = ['clear_rate','average_localization_clear_time_s','virtual_time_s','distance_m','measure_count','switch_count','failed_clear_count']
            grouped[scenario] = {k: sum(r[k] for r in group if r[k] is not None)/sum(r[k] is not None for r in group)
                                 if any(r[k] is not None for r in group) else None for k in keys}
            grouped[scenario].update(runs=len(group), worst_seed=worst['seed'], worst_replay=worst['replay_path'])
        (output/'summary.json').write_text(json.dumps(grouped, indent=2), encoding='utf-8')
        with (output/'runs.csv').open('w', newline='', encoding='utf-8') as f:
            fields = ['scenario','seed','strategy','strategy_version',*keys,'replay_path']
            writer = csv.DictWriter(f, fields, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)
        print(output.resolve())

if __name__ == '__main__': main()
