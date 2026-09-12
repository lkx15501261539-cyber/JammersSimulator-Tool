"""Paired Q4 experiments backed by physical receipts; optional offline plots.

Current Q4 v2 and the left/right policy are one baseline: pair_v2. Strategies
receive only ResponseClient; hidden truth is used here for offline validation.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
from dataclasses import asdict, dataclass, is_dataclass
import hashlib
import importlib.util
import json
import math
import multiprocessing
from pathlib import Path
import platform
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from enhanced.q4_adapter import _controller_module, CODE
from enhanced.runner import ResponseClient, save_run
from enhanced.world import ScenarioConfig, World, SCENARIOS


@dataclass(frozen=True)
class Variant:
    name: str
    family: str
    extra_budget: int = 3
    tau_route_s: float | None = None
    candidate_policy: str | None = None


VARIANTS = {
    'pair_v2': Variant('pair_v2', 'q4_opportunity_v2'),
    'q4_v1': Variant('q4_v1', 'q4_cu'),
    **{f'route_b{b}_t{t}': Variant(f'route_b{b}_t{t}', 'route', b, float(t), 'route')
       for b in (1, 2, 3) for t in (0, 5, 20)},
    'global_q2_b2': Variant('global_q2_b2', 'route', 2, 5., 'global_q2'),
}
DEFAULT_VARIANTS = ('pair_v2', 'route_b1_t5', 'route_b2_t5', 'route_b3_t5',
                    'route_b2_t0', 'route_b2_t20', 'global_q2_b2')
FULL_GRID = ('pair_v2', *(f'route_b{b}_t{t}' for b in (1, 2, 3) for t in (0, 5, 20)), 'global_q2_b2')
TIMING = 'L_move/5+N_sw+5*N_meas+3*N_clr+2*N_succ'
ERROR_RULES = {
    'worst_edge': 'deterministic coordinate/channel hash selects -1 or +1 degree; bearing rounded to 0.01 degree',
    'deterministic_hash_fixed': 'deterministic coordinate/channel hash field in [-1,1] degree; bearing rounded to 0.01 degree',
    'baseline_fixed_field': 'fixed 0.994*sin(0.00637*x+0.00413*y+1.713*k+0.017*seed) degree field; bearing rounded to 0.01 degree',
}


def _clean(value):
    if is_dataclass(value):
        return _clean(asdict(value))
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Path):
        return str(value)
    return value


def _dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(_clean(value), ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    temporary.replace(path)


def _digest(value):
    return hashlib.sha256(json.dumps(_clean(value), sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def source_hashes(variants):
    paths = [p for p in CODE.glob('*.py') if not p.name.startswith('test_')]
    if any(v.family == 'route' for v in variants) and not (CODE/'route_opportunistic_remeasure.py').is_file():
        raise FileNotFoundError('Route controller is not installed')
    paths += [ROOT/'enhanced/world.py', ROOT/'enhanced/runner.py', ROOT/'enhanced/q4_adapter.py', Path(__file__).resolve()]
    if any(v.family == 'route' for v in variants):
        # The route scorer loads unchanged Q2 geometry from this frozen archive.
        paths.append(ROOT/'models'/'Baseline_v2.0_七点六边形.zip')
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(paths))}


def _route_module():
    name = 'jammers_route_comparison_controller'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, CODE/'route_opportunistic_remeasure.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        sys.path.insert(0, str(CODE))
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(name, None)
            raise
        finally:
            sys.path.remove(str(CODE))
    return sys.modules[name]


class _DecisionTrace(list):
    """Add receipt coordinates to diagnostics without changing policy state."""
    def __init__(self, owner, existing=()):
        self.owner = owner
        super().__init__(existing)

    def append(self, item):
        item = dict(item)
        item.setdefault('action_step', len(self.owner.actions))
        item.setdefault('T', self.owner.T)
        item.setdefault('L_move', self.owner.counts['L_move'])
        super().append(item)


def _make_controller(variant, config, client):
    module = _route_module() if variant.family == 'route' else _controller_module(variant.family)

    class TracedController(module.Controller):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.decisions = _DecisionTrace(self, self.decisions)
            self._comparison_measure_role = None

        def followup(self, *args, **kwargs):
            previous = self._comparison_measure_role
            self._comparison_measure_role = 'paired_opportunity' if kwargs.get('anchor') is not None else 'active_remeasure'
            try:
                return super().followup(*args, **kwargs)
            finally:
                self._comparison_measure_role = previous

        def action(self, kind, k, p):
            result = super().action(kind, k, p)
            if kind == 'measure' and self._comparison_measure_role is not None:
                self.actions[-1]['comparison_role'] = self._comparison_measure_role
            return result

    if variant.family == 'route':
        cfg = module.RouteConfig(extra_budget=variant.extra_budget, tau_route_s=variant.tau_route_s,
                                 candidate_policy=variant.candidate_policy)
        return TracedController(client, seed=config.seed, error_mode=config.error_model, config=cfg)
    return TracedController(client)


def canonical_actions(observations):
    actions = []
    for observation in observations:
        request, response = observation['request'], observation['response']
        if observation['path'] not in ('/measure', '/clear') or response.get('accepted') is not True:
            continue
        actions.append(dict(kind=observation['path'][1:], k=request['channel'],
                            p=[request['position']['x'], request['position']['y']],
                            response=response, T=response['virtual_time_s']))
    return actions


def _channel(k, sources):
    return dict(channel=k, is_true_source=k in sources, first_detect_step=None, first_detect_position=None,
        first_detect_T=None, first_detect_L_move=None, first_near_step=None, scheduled_remeasure_step=None,
        scheduled_at_step=None, actual_first_remeasure_step=None, first_remeasure_position=None,
        first_remeasure_distance=None, first_remeasure_delay_s=None, first_remeasure_feedback_delay_s=None,
        followup_count=0, followup_results=[], clear_attempts=0, clear_success=False, clear_step=None,
        no_remeasure_until_clear=False, no_remeasure_reason=None, no_remeasure_reason_detail=None,
        first_remeasure_role=None, last_direction_role=None, last_remeasure_role=None)


def summarize_actions(actions, decisions, source_channels, *, budget=3, roles=None, route_metrics=None):
    """Physical counts/delays come from accepted receipts, reasons from telemetry.

    Delay runs from first direction completion to first subsequent measurement
    start. feedback_delay includes its additional five-second measurement.
    Missing booking estimates/reasons stay null/unknown; they are not inferred
    from the absence of measurement alone.
    """
    sources, channels = set(source_channels), {}
    counts, errors = dict(L_move=0., N_sw=0, N_meas=0, N_clr=0, N_succ=0), []
    p, c, seen = (0., 0.), 1, set()
    roles, route_metrics = roles or {}, route_metrics or {}
    for step, action in enumerate(actions, 1):
        k, position, response = int(action['k']), tuple(action['p']), action['response']
        row = channels.setdefault(k, _channel(k, sources))
        counts['L_move'] += math.dist(p, position)
        p = position
        if action['kind'] == 'measure':
            counts['N_meas'] += 1
            counts['N_sw'] += k != c
            c = k
            if (k, position) in seen:
                errors.append(f'repeated coordinate/channel measurement at action {step}')
            seen.add((k, position))
            if row['clear_success']:
                errors.append(f'measurement after success on channel {k}')
            outcome = response.get('measure_result')
            if row['first_detect_step'] is not None:
                row['followup_count'] += 1
                row['followup_results'].append(outcome)
                row['last_remeasure_role'] = roles.get(step)
                if outcome == 'direction':
                    row['last_direction_role'] = roles.get(step)
                if row['actual_first_remeasure_step'] is None:
                    row.update(actual_first_remeasure_step=step, first_remeasure_position=list(position),
                        first_remeasure_distance=counts['L_move']-row['first_detect_L_move'],
                        first_remeasure_delay_s=response['virtual_time_s']-5-row['first_detect_T'],
                        first_remeasure_feedback_delay_s=response['virtual_time_s']-row['first_detect_T'],
                        first_remeasure_role=roles.get(step))
            elif outcome == 'direction':
                row.update(first_detect_step=step, first_detect_position=list(position),
                           first_detect_T=response['virtual_time_s'], first_detect_L_move=counts['L_move'])
            elif outcome == 'near' and row['first_near_step'] is None:
                row['first_near_step'] = step
        elif action['kind'] == 'clear':
            counts['N_clr'] += 1
            row['clear_attempts'] += 1
            if response.get('clear_result') == 'success':
                counts['N_succ'] += 1
                if row['clear_success']:
                    errors.append(f'duplicate clear success on channel {k}')
                row.update(clear_success=True, clear_step=step)
        else:
            raise ValueError(f'Unknown physical action kind: {action["kind"]}')
        expected = counts['L_move']/5+counts['N_sw']+5*counts['N_meas']+3*counts['N_clr']+2*counts['N_succ']
        if not math.isclose(expected, response['virtual_time_s'], abs_tol=1e-7, rel_tol=1e-10):
            errors.append(f'physical timing mismatch at action {step}')
    for k in sources:
        channels.setdefault(k, _channel(k, sources))
    entered_U, certified_after_opportunity, directional = set(), set(), set()
    budget_stops = []
    for k, row in channels.items():
        events = [d for d in decisions if d.get('k', d.get('channel')) == k]
        native = route_metrics.get(k, route_metrics.get(str(k), {}))
        if row['followup_count'] > budget:
            errors.append(f'additional measurement budget exceeded on channel {k}')
        bookings = [d for d in events if d.get('event') in ('opportunity_pair', 'remeasurement_scheduled')]
        if bookings:
            row['scheduled_at_step'] = bookings[0].get('action_step')
        row['scheduled_remeasure_step'] = native.get('scheduled_remeasure_step')
        if any(d.get('event') == 'U' for d in events):
            entered_U.add(k)
        budget_stops.extend(d for d in events if d.get('event') == 'budget_exhausted'
                            or d.get('event') == 'U' and d.get('reason') in ('r_k=0', 'budget_zero', 'budget_exhausted'))
        for d in events:
            if d.get('event') == 'safe_no_signal':
                directional.add((k, d.get('action_step')))
            if d.get('event') == 'direct_clear' and row['clear_success'] and row.get('last_direction_role') == 'paired_opportunity':
                certified_after_opportunity.add(k)
        if (native.get('certified_direct_after_opportunity') and row['clear_success']
                and row['last_direction_role'] in ('route_opportunity', 'paired_opportunity')):
            certified_after_opportunity.add(k)
        # Explicit source metadata can add interpretation, never physical counts.
        for key in ('first_detect_step', 'actual_first_remeasure_step'):
            if native.get(key) is not None and native[key] != row[key]:
                errors.append(f'native {key} disagrees with receipts on channel {k}')
        row['no_remeasure_until_clear'] = (row['first_detect_step'] is not None and row['followup_count'] == 0 and row['clear_success'])
        if row['no_remeasure_until_clear']:
            if native.get('no_remeasure_reason'):
                reason = native['no_remeasure_reason']
            elif any(d.get('event') == 'direct_clear' for d in events):
                reason = 'already_clearable'
            elif k in entered_U:
                reason = 'optical_fallback'
            else:
                reason = 'unknown'
            row['no_remeasure_reason'] = reason
            row['no_remeasure_reason_detail'] = next((d['event'] for d in events if d.get('event') in
                ('no_certified_opportunity_pair', 'no_route_candidate')), None)
    rows = [channels[k] for k in sorted(sources)]
    found = [r for r in rows if r['first_detect_step'] is not None]
    first = [r for r in found if r['followup_count'] >= 1]
    second = [r for r in found if r['followup_count'] >= 2]
    followups = sum(r['followup_count'] for r in rows)
    mean = lambda values: statistics.mean(values) if values else None
    metrics = dict(**counts, T=counts['L_move']/5+counts['N_sw']+5*counts['N_meas']+3*counts['N_clr']+2*counts['N_succ'],
        source_count=len(sources), cleared=sum(r['clear_success'] for r in rows), first_direction_sources=len(found),
        additional_remeasurements=followups, mean_followups_per_source=followups/len(sources) if sources else None,
        first_remeasure_attempts=len(first), first_remeasure_successes=sum(r['followup_results'][0] in ('direction', 'near') for r in first),
        second_remeasure_attempts=len(second), second_remeasure_successes=sum(r['followup_results'][1] in ('direction', 'near') for r in second),
        opportunity_direct_clear_sources=len(certified_after_opportunity),
        opportunity_near_clear_sources=sum(r['clear_success'] and r['followup_results'][-1:] == ['near']
            and r['last_remeasure_role'] in ('paired_opportunity', 'route_opportunity') for r in rows),
        optical_fallback_sources=len(entered_U),
        mean_clear_attempts_per_source=sum(r['clear_attempts'] for r in rows)/len(sources) if sources else None,
        mean_first_remeasure_distance=mean([r['first_remeasure_distance'] for r in first]),
        mean_first_remeasure_delay_s=mean([r['first_remeasure_delay_s'] for r in first]),
        mean_first_remeasure_feedback_delay_s=mean([r['first_remeasure_feedback_delay_s'] for r in first]),
        no_remeasure_until_clear_sources=sum(r['no_remeasure_until_clear'] for r in found),
        unresolved_without_remeasure_sources=sum(not r['clear_success'] and r['followup_count'] == 0 for r in found),
        budget_exhausted_sources=sum(r['followup_count'] == budget for r in found),
        budget_stop_events=len({(d.get('k', d.get('channel')), d.get('action_step')) for d in budget_stops}),
        directional_confirmation_events=len(directional), directional_confirmed_sources=len({k for k, _ in directional}))
    for prefix in ('first', 'second'):
        n = metrics[f'{prefix}_remeasure_attempts']
        metrics[f'{prefix}_remeasure_success_rate'] = metrics[f'{prefix}_remeasure_successes']/n if n else None
    return dict(metrics=metrics, channels=rows, audit_errors=errors)


def run_one(config_data, variant_name, trace_root=None):
    config, variant = ScenarioConfig(**config_data), VARIANTS[variant_name]
    world, controller, result = World(config), None, {}
    truth_hash = _digest(world.sources)
    client = ResponseClient(world.request)
    failure = None
    wall_start, cpu_start = time.perf_counter(), time.process_time()
    warm_wall = warm_cpu = 0.
    runtime_metadata = None
    try:
        if variant.family == 'route':
            warm_start, warm_cpu_start = time.perf_counter(), time.process_time()
            runtime_metadata = _route_module().prepare_runtime()
            warm_wall, warm_cpu = time.perf_counter()-warm_start, time.process_time()-warm_cpu_start
        controller = _make_controller(variant, config, client)
        result = controller.run()
        failure = result.get('failure')
    except Exception as exc:
        failure = f'{type(exc).__name__}: {exc}'
    finally:
        if world.active:
            try:
                client.exit()
            except Exception as exc:
                failure = f'{failure or ""}; exit failed: {exc}'
    wall, cpu = time.perf_counter()-wall_start, time.process_time()-cpu_start
    actions = canonical_actions(world.observations)
    supplied = list(getattr(controller, 'actions', result.get('actions', [])))
    decisions = list(getattr(controller, 'decisions', result.get('decisions', [])))
    roles = {i: a['comparison_role'] for i, a in enumerate(supplied, 1) if a.get('comparison_role')}
    for d in decisions:
        if d.get('event') == 'remeasurement' and d.get('action_step') is not None:
            roles[d['action_step']] = 'route_opportunity' if d.get('level') in (1, 2) else 'active_remeasure'
    reconstructed = summarize_actions(actions, decisions, [s['channel'] for s in world.sources],
        budget=variant.extra_budget, roles=roles, route_metrics=result.get('route_metrics'))
    errors, metrics = reconstructed['audit_errors'], reconstructed['metrics']
    if len(actions) != len(supplied):
        errors.append('controller action count differs from accepted simulator receipts')
    for recorded, actual in zip(supplied, actions):
        if any(recorded.get(key) != actual[key] for key in ('kind', 'k')) or tuple(recorded.get('p', ())) != tuple(actual['p']):
            errors.append('controller action identity differs from simulator receipts')
            break
    if not math.isclose(metrics['T'], world.t, abs_tol=1e-7, rel_tol=1e-10):
        errors.append('independent total time differs from simulator')
    if result.get('T') is not None and not math.isclose(metrics['T'], result['T'], abs_tol=1e-7, rel_tol=1e-10):
        errors.append('controller total time differs from accepted receipts')
    for key, value in result.get('counts', {}).items():
        if key in metrics and not math.isclose(value, metrics[key], abs_tol=1e-7, rel_tol=1e-10):
            errors.append(f'controller count {key} differs from accepted receipts')
    if controller is not None:
        for s in controller.channels.values():
            if s.sigma == 'ABSENT' and not (len(s.scanned) == 25 and set(s.scanned.values()) == {'no_signal'}):
                errors.append(f'ABSENT without 25 negative scans on channel {s.k}')
            if s.followups > variant.extra_budget or not 0 <= s.r_k <= variant.extra_budget:
                errors.append(f'controller budget state invalid on channel {s.k}')
    completed = bool(result.get('completed') and len(world.cleared) == len(world.sources) and not errors and failure is None)
    if result.get('completed') and len(world.cleared) != len(world.sources):
        errors.append('controller claimed completion before all true sources were cleared')
    order = getattr(controller, 'initial_order', result.get('initial_order'))
    tour = dict(points=getattr(controller, 'Z', None), order=order)
    measurements = [d for d in decisions if d.get('event') == 'remeasurement']
    losses = [d['J_hat'] for d in measurements if isinstance(d.get('J_hat'), (int, float)) and math.isfinite(d['J_hat'])]
    detours = []
    for d in decisions:
        if (d.get('event') != 'opportunity_detour_verified'
                or not isinstance(d.get('actual_extra_move_s'), (int, float))
                or not math.isfinite(d['actual_extra_move_s'])):
            continue
        # A verification just before a rejected request is not an executed
        # detour. Match it to its accepted physical measurement receipt.
        step = d.get('action_step')
        if isinstance(step, int):
            for index in (step, step-1):
                if 0 <= index < len(actions):
                    a = actions[index]
                    if a['kind'] == 'measure' and a['k'] == d.get('k') and tuple(a['p']) == tuple(d.get('point', ())):
                        detours.append(d['actual_extra_move_s'])
                        break
    metrics.update(completed=completed, completion_reason=result.get('completion_reason'),
        cpu_seconds=cpu, wall_seconds=wall, warmup_cpu_seconds=warm_cpu, warmup_wall_seconds=warm_wall,
        mean_selected_J_hat=statistics.mean(losses) if losses else None,
        executed_remeasure_detour_s=sum(detours) if detours else None,
        verified_executed_detours=len(detours),
        scored_remeasurements=len(losses))
    row = dict(config=asdict(config), variant=asdict(variant), world_sha256=truth_hash,
        initial_tour=tour, initial_tour_sha256=_digest(tour) if order is not None else None,
        metrics=metrics, channels=reconstructed['channels'], audit_errors=errors, failure=failure,
        runtime_metadata=result.get('score_runtime', runtime_metadata), native_route_metrics=result.get('route_metrics'),
        native_opportunity_stats=result.get('opportunities'))
    if trace_root is not None:
        directory = Path(trace_root)/f'{config.scenario}-seed-{config.seed}'/variant.name
        metadata = dict(**asdict(config), model='q4_route_v3' if variant.family == 'route' else variant.family,
            experiment_variant=asdict(variant), completion='completed' if completed else 'incomplete_unresolved',
            failure=failure, world_sha256=truth_hash)
        if variant.family == 'route':
            metadata['route_config'] = dict(extra_budget=variant.extra_budget,
                tau_route_s=variant.tau_route_s, candidate_policy=variant.candidate_policy)
            metadata['score_runtime'] = result.get('score_runtime', runtime_metadata)
        save_run(world, directory, metadata)
        _dump(directory/'controller.json', result)
        _dump(directory/'actions.json', actions)
        _dump(directory/'decisions.json', decisions)
        _dump(directory/'measurements.json', measurements)
        row['trace_directory'] = str(directory.resolve())
        _dump(directory/'experiment.json', row)
    return _clean(row)


def validate_common_scenes(rows):
    groups, errors = {}, []
    for row in rows:
        groups.setdefault((row['config']['scenario'], row['config']['seed']), []).append(row)
    for key, group in groups.items():
        if len({r['world_sha256'] for r in group}) != 1:
            errors.append(dict(scene=key, error='paired worlds differ'))
        comparable = [r for r in group if r['variant']['name'] != 'q4_v1' and r['initial_tour_sha256'] is not None]
        if len({r['initial_tour_sha256'] for r in comparable}) > 1:
            errors.append(dict(scene=key, error='initial 25-point search tours differ'))
        for r in group:
            if r['metrics']['completed'] and r['variant']['name'] != 'q4_v1' and r['initial_tour_sha256'] is None:
                errors.append(dict(scene=key, variant=r['variant']['name'], error='initial search tour was not recorded'))
    return errors


def aggregate(rows):
    variants = list(dict.fromkeys(r['variant']['name'] for r in rows))
    baseline = {(r['config']['scenario'], r['config']['seed']): r for r in rows if r['variant']['name'] == 'pair_v2'}
    summary = []
    for name in variants:
        group = [r for r in rows if r['variant']['name'] == name]
        good = [r for r in group if r['metrics']['completed']]
        out = dict(variant=name, runs=len(group), completed=len(good), completion_rate=len(good)/len(group),
                   cleared=sum(r['metrics']['cleared'] for r in group), source_count=sum(r['metrics']['source_count'] for r in group))
        numeric = sorted({k for r in group for k, v in r['metrics'].items() if isinstance(v, (int, float)) and not isinstance(v, bool)})
        for key in numeric:
            selected = good if key in ('T', 'L_move', 'N_clr', 'N_meas', 'N_sw') else group
            values = [r['metrics'][key] for r in selected if r['metrics'].get(key) is not None]
            out[f'mean_{key}'] = statistics.mean(values) if values else None
        paired = []
        for r in good:
            b = baseline.get((r['config']['scenario'], r['config']['seed']))
            if b and b['metrics']['completed'] and b['world_sha256'] == r['world_sha256']:
                if name != 'q4_v1' and b['initial_tour_sha256'] != r['initial_tour_sha256']:
                    continue
                paired.append((r['metrics']['T'], b['metrics']['T']))
        out.update(paired_complete_runs=len(paired),
            mean_paired_T_change_s=statistics.mean(a-b for a, b in paired) if paired else None,
            mean_paired_T_change_percent=statistics.mean(100*(a/b-1) for a, b in paired) if paired else None,
            median_completed_T=statistics.median(r['metrics']['T'] for r in good) if good else None)
        for prefix in ('first', 'second'):
            attempts = sum(r['metrics'][f'{prefix}_remeasure_attempts'] for r in group)
            successes = sum(r['metrics'][f'{prefix}_remeasure_successes'] for r in group)
            out[f'{prefix}_remeasure_attempts'] = attempts
            out[f'{prefix}_remeasure_success_rate'] = successes/attempts if attempts else None
        attempted = [c for r in group for c in r['channels'] if c['actual_first_remeasure_step'] is not None]
        for metric in ('first_remeasure_distance', 'first_remeasure_delay_s', 'first_remeasure_feedback_delay_s'):
            values = [c[metric] for c in attempted if c[metric] is not None]
            out[f'pooled_{metric}'] = statistics.mean(values) if values else None
        out['pooled_clear_attempts_per_source'] = (sum(r['metrics']['N_clr'] for r in group)/out['source_count']
                                                  if out['source_count'] else None)
        summary.append(out)
    return summary


def compare(seeds, scenarios, *, variants=DEFAULT_VARIANTS, jobs=1, trace_root=None,
            output=None, error_model='worst_edge', progress=True):
    if jobs < 1 or not seeds or not scenarios or not variants:
        raise ValueError('jobs, seeds, distributions, and variants must be nonempty/positive')
    if len(set(seeds)) != len(seeds) or len(set(scenarios)) != len(scenarios) or len(set(variants)) != len(variants):
        raise ValueError('duplicate seeds, distributions, or variants would duplicate observations')
    selected = [VARIANTS[name] for name in variants]
    hashes = source_hashes(selected)
    configs = [asdict(ScenarioConfig(problem=4, seed=seed, scenario=scenario, error_model=error_model))
               for scenario in scenarios for seed in seeds]
    tasks = [(cfg, variant.name, trace_root) for cfg in configs for variant in selected]
    report = dict(schema_version=2, baseline_aliases={'A_current_Q4_v2': 'pair_v2', 'B_left_right': 'pair_v2'},
        design=dict(seeds=list(seeds), distributions=list(scenarios), variants=[asdict(v) for v in selected],
                    common_geometries=len(configs), planned_runs=len(tasks), jobs=jobs),
        error_rule=ERROR_RULES[error_model], timing_formula=TIMING, source_files_sha256=hashes,
        environment=dict(python=sys.version, platform=platform.platform()),
        metric_conventions=dict(step='accepted measure/clear action index, starting at 1',
            scheduled_at_step='actual booking decision step; never a promised future action index',
            first_remeasure_delay_s='first direction completion to first followup measurement start',
            pooled='source-weighted first-remeasurement delays/distances; unattempted sources excluded',
            no_attempt_success_rate='null, not zero', failed_runs='retained; partial T excluded from completed-time averages',
            CPU='process CPU including preparation and controller initialization; warmup also reported separately',
            J='finite-search numerical score; not a certified continuous global optimum'), rows=[], summary=[])

    def receive(row):
        report['rows'].append(row)
        if progress:
            m, cfg = row['metrics'], row['config']
            print(f'{len(report["rows"])}/{len(tasks)} {cfg["scenario"]} seed={cfg["seed"]} '
                  f'{row["variant"]["name"]}: completed={m["completed"]} T={m["T"]:.3f}s '
                  f'cleared={m["cleared"]}/{m["source_count"]} CPU={m["cpu_seconds"]:.3f}s', flush=True)
        if output is not None:
            _dump(Path(output).with_suffix('.checkpoint.json'), report)

    if jobs == 1:
        for task in tasks:
            receive(run_one(*task))
    else:
        with ProcessPoolExecutor(max_workers=jobs, mp_context=multiprocessing.get_context('spawn')) as pool:
            futures = {pool.submit(run_one, *task): task for task in tasks}
            for future in as_completed(futures):
                # Controller/protocol failures are rows. Infrastructure failures
                # preserve the checkpoint and raise; do not fabricate empty runs.
                receive(future.result())
    report['rows'].sort(key=lambda r: (list(scenarios).index(r['config']['scenario']),
        list(seeds).index(r['config']['seed']), list(variants).index(r['variant']['name'])))
    report['comparison_errors'] = validate_common_scenes(report['rows'])
    report['source_files_unchanged'] = source_hashes(selected) == hashes
    if not report['source_files_unchanged']:
        report['comparison_errors'].append(dict(error='source changed while experiments were running; rerun stable source'))
    report['comparison_valid'] = not report['comparison_errors']
    report['summary'] = aggregate(report['rows'])
    return report


def _csv(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with Path(path).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                             for k, v in row.items()})


def write_outputs(report, output):
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    for row in report['rows']:
        if row.get('trace_directory'):
            try:
                path = Path(row['trace_directory'])
                if path.is_absolute():
                    row['trace_directory'] = str(path.resolve().relative_to(output.parent))
            except ValueError:
                pass
    _dump(output, report)
    runs = [dict(distribution=r['config']['scenario'], seed=r['config']['seed'],
                 **r['variant'], **r['metrics'], world_sha256=r['world_sha256'],
                 initial_tour_sha256=r['initial_tour_sha256'], failure=r['failure'],
                 audit_errors=r['audit_errors']) for r in report['rows']]
    channels = [dict(distribution=r['config']['scenario'], seed=r['config']['seed'],
                     variant=r['variant']['name'], **channel)
                for r in report['rows'] for channel in r['channels']]
    _csv(output.with_suffix('.runs.csv'), runs)
    _csv(output.with_suffix('.channels.csv'), channels)
    _csv(output.with_suffix('.summary.csv'), report['summary'])
    fmt = lambda value: '—' if value is None else f'{value:.3f}'
    lines = ['# Q4 同场景实验', '',
        '当前 Q4 v2.0 与左右夹击机会复测版是同一条 `pair_v2` 基线，未重复计为两组。', '',
        f'共同几何场景：{report["design"]["common_geometries"]}；实际记录：{len(report["rows"])} / '
        f'{report["design"]["planned_runs"]}。共同场景与源码核验：'
        f'{"通过" if report.get("comparison_valid") else "未通过，不应据此宣称策略胜负"}。', '',
        '| 策略 | 完成/运行 | 已完成场均 T (s) | 共同完成场配对 T 变化 (%) | 追加复测/场 | 光学尝试/源 | 无复测清除源/场 | CPU/场 (s) |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for row in report['summary']:
        lines.append(f'| {row["variant"]} | {row["completed"]}/{row["runs"]} | '
            f'{fmt(row.get("mean_T"))} | {fmt(row.get("mean_paired_T_change_percent"))} | '
            f'{fmt(row.get("mean_additional_remeasurements"))} | {fmt(row.get("pooled_clear_attempts_per_source"))} | '
            f'{fmt(row.get("mean_no_remeasure_until_clear_sources"))} | {fmt(row.get("mean_cpu_seconds"))} |')
    lines += ['', '时间和移动指标来自已接受动作，并逐动作与模拟器对账。失败局保留，部分任务时间不混入已完成场的平均 T。',
        '复测成功率以实际执行对应次复测的源为分母；没有尝试时记为缺失值。首次 near 后直接清除单列，不算首次 direction 后不复测。',
        '延迟从首次 direction 返回至首次复测开始；另保留包含复测本身 5 秒的反馈延迟。预约发生步数与实际执行步数分开。源级延迟按实际有复测的源汇总平均。',
        'J 是有限搜索评分，不宣称连续全局最优。累计绕路仅统计有接受测量回执对应的执行前边际距离认证。',
        'CPU 包含预热与控制器初始化，预热 CPU/墙钟另列；不是只有主循环的耗时。固定场景结果不构成任意场景更快的证明。', '',
        f'真实误差规则：`{report["error_rule"]}`。', '']
    output.with_suffix('.md').write_text('\n'.join(lines), encoding='utf-8')
    return dict(json=str(output), runs_csv=str(output.with_suffix('.runs.csv')),
                channels_csv=str(output.with_suffix('.channels.csv')),
                summary_csv=str(output.with_suffix('.summary.csv')), markdown=str(output.with_suffix('.md')))


def render_plots(report, directory):
    """Standard scientific plotting, deliberately not a GUI runtime dependency."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError('Plotting needs matplotlib in this offline reporting environment; JSON/CSV/table remain available.') from exc
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    rows, summary = report['rows'], report['summary']
    names = [r['variant'] for r in summary]
    colors = {name: plt.get_cmap('tab20')(i) for i, name in enumerate(names)}
    saved = []

    def finish(fig, name):
        fig.tight_layout()
        for suffix in ('png', 'svg'):
            path = directory/f'{name}.{suffix}'
            fig.savefig(path, dpi=180)
            saved.append(str(path))
        plt.close(fig)

    fig, axes = plt.subplots(1, 3, figsize=(17, 6))
    for ax, metric, title in zip(axes, ('mean_T', 'mean_L_move', 'mean_N_clr'),
                              ('Task time (s)', 'Move distance (m)', 'Clear attempts')):
        values = [r.get(metric) for r in summary]
        ax.bar(names, [v if v is not None else 0 for v in values], color=[colors[n] for n in names])
        for i, value in enumerate(values):
            if value is None:
                ax.text(i, 0, 'N/A', ha='center', va='bottom', fontsize=8)
        ax.set_title(title+' | completed runs')
        ax.tick_params(axis='x', rotation=55)
        ax.grid(axis='y', alpha=.2)
    finish(fig, 'completed_task_comparison')

    fig, ax = plt.subplots(figsize=(10, 6))
    for name in names:
        subset = [r['metrics'] for r in rows if r['variant']['name'] == name and r['metrics']['completed']]
        ax.scatter([r['additional_remeasurements'] for r in subset], [r['L_move'] for r in subset],
                   label=name, color=colors[name], alpha=.65, s=24)
    ax.set(xlabel='Additional measurements', ylabel='Move distance (m)', title='Same geometries; completed runs')
    ax.grid(alpha=.2)
    ax.legend(fontsize=8, bbox_to_anchor=(1.02, 1), loc='upper left')
    finish(fig, 'movement_vs_remeasurement')

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    for ax, parameter, fixed in ((axes[0], 'extra_budget', lambda v: v['tau_route_s'] == 5),
                                 (axes[1], 'tau_route_s', lambda v: v['extra_budget'] == 2)):
        points = []
        for s in summary:
            variant = VARIANTS[s['variant']]
            if variant.family == 'route' and variant.candidate_policy == 'route' and fixed(asdict(variant)) and s.get('mean_T') is not None:
                points.append((getattr(variant, parameter), s['mean_T']))
        points.sort()
        ax.plot([p[0] for p in points], [p[1] for p in points], marker='o')
        ax.set(xlabel='Additional budget' if parameter == 'extra_budget' else 'Route detour limit (s)',
               ylabel='Mean completed task time (s)', title='Budget ablation' if parameter == 'extra_budget' else 'Detour threshold ablation')
        ax.grid(alpha=.2)
    finish(fig, 'budget_and_detour_ablations')

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    for ax, metric, title in ((axes[0], 'pooled_first_remeasure_delay_s', 'First remeasurement wait (s) | attempted sources'),
                              (axes[1], 'mean_no_remeasure_until_clear_sources', 'Sources cleared without remeasurement / run')):
        values = [r.get(metric) for r in summary]
        ax.bar(names, [v if v is not None else 0 for v in values], color=[colors[n] for n in names])
        for i, value in enumerate(values):
            if value is None:
                ax.text(i, 0, 'N/A', ha='center', va='bottom', fontsize=8)
        ax.set_title(title)
        ax.tick_params(axis='x', rotation=55)
        ax.grid(axis='y', alpha=.2)
    finish(fig, 'first_remeasurement_diagnostics')
    _dump(directory/'figures.json', dict(files=saved, completed_time_excludes_failed=True,
                                        missing_delay_is_not_zero=True))
    return saved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seeds', type=int, nargs='+', default=list(range(42, 48)))
    parser.add_argument('--distributions', '--scenarios', dest='distributions', choices=SCENARIOS,
                        nargs='+', default=list(SCENARIOS))
    parser.add_argument('--variants', choices=tuple(VARIANTS), nargs='+')
    parser.add_argument('--full-grid', action='store_true', help='Use all 3 budgets x 3 detour thresholds, baseline, and global Q2 control')
    parser.add_argument('--legacy', action='store_true', help='Reproduce the old q4_cu versus pair_v2 comparison only')
    parser.add_argument('--jobs', type=int, default=1)
    parser.add_argument('--error-model', choices=tuple(ERROR_RULES), default='worst_edge')
    parser.add_argument('--output', type=Path, default=Path('q4-route-comparison.json'))
    parser.add_argument('--trace-root', type=Path)
    parser.add_argument('--plots', action='store_true', help='Render PNG/SVG using optional matplotlib')
    parser.add_argument('--render', type=Path, help='Regenerate CSV/table and optionally plots from an existing experiment JSON; no simulations')
    args = parser.parse_args()
    if args.render:
        report = json.loads(args.render.read_text(encoding='utf-8'))
        output = args.render
    else:
        if args.output.exists():
            parser.error('output exists; choose another output path')
        if sum(bool(x) for x in (args.variants, args.full_grid, args.legacy)) > 1:
            parser.error('choose one of --variants, --full-grid, or --legacy')
        if args.jobs < 1:
            parser.error('--jobs must be positive')
        variants = args.variants or (('q4_v1', 'pair_v2') if args.legacy else FULL_GRID if args.full_grid else DEFAULT_VARIANTS)
        trace_root = args.trace_root or args.output.with_suffix('').with_name(args.output.stem+'-runs')
        report = compare(args.seeds, args.distributions, variants=variants, jobs=args.jobs,
            trace_root=trace_root, output=args.output, error_model=args.error_model)
        output = args.output
    artifacts = write_outputs(report, output)
    if args.plots:
        artifacts['figures'] = render_plots(report, output.with_suffix('').with_name(output.stem+'-figures'))
    print(json.dumps(artifacts, ensure_ascii=False, indent=2))
    return 0 if report.get('comparison_valid') else 1


if __name__ == '__main__':
    raise SystemExit(main())
