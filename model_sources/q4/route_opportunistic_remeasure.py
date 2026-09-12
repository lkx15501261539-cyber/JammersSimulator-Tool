"""Route-constrained remeasurement on the existing Q4 F/P/E and C/U model.

Only accepted public responses change the state. J_hat ranks a finite candidate
set; reception, direct clear and U coverage retain continuous certificates.
The optimistic opportunity budget is a policy, not a worst-case improvement
theorem. The expensive active fallback retains the conservative C4/U test.
"""
import argparse
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path

from q4 import Controller as BaseController, RestClient
from q4_v2 import Controller as PairedController
from cu import C4, Uhat, path_cost, snap, two_opt
from opportunities import certified_clear_point, distance_safe


@dataclass(frozen=True)
class RouteConfig:
    extra_budget: int = 2
    tau_route_s: float = 5.
    candidate_policy: str = 'route'
    problem_id: int = 4
    opt_rounds: int = 1
    consecutive_no_signal_limit: int = 2
    path_probe_count: int = 7
    path_refine_rounds: int = 4
    score_source_samples: int = 9
    score_error_samples: int = 3
    candidate_limit: int = 24
    global_grid_step_m: float = 300.
    global_refine_step_m: float = 100.
    global_refine_count: int = 2

    def __post_init__(self):
        if type(self.extra_budget) is not int or not 0 <= self.extra_budget <= 3:
            raise ValueError('extra_budget must be an integer in 0..3')
        if not math.isfinite(self.tau_route_s) or self.tau_route_s < 0:
            raise ValueError('tau_route_s must be nonnegative and finite')
        if self.candidate_policy not in ('route', 'global_q2'):
            raise ValueError('candidate_policy must be route or global_q2')
        if self.problem_id not in (3, 4) or not 0 <= self.opt_rounds <= 3:
            raise ValueError('invalid problem_id or opt_rounds')
        if not 1 <= self.consecutive_no_signal_limit <= 3:
            raise ValueError('consecutive_no_signal_limit must be in 1..3')


def prepare_runtime():
    from opportunities import prepare_score_runtime
    return prepare_score_runtime()


class Controller(PairedController):
    def __init__(self, client, *, seed=None, error_mode=None, config=None):
        self.config = config or RouteConfig()
        self.score_runtime = prepare_runtime()
        self._ready = False
        # Preserve exactly the baseline's initial 25-point open tour. Online
        # rounds are separately configurable; the initial tour uses baseline 2.
        super().__init__(client, opt_rounds=2)
        self.opt_rounds = self.config.opt_rounds
        self.reservations = {}
        self.optical_pending = {}
        self.optical_plans = {}
        self._optical_summaries = {}
        self._execution_segments = {}
        self.near_pending = set()
        self.measured_points = {k: set() for k in self.channels}
        self.directional_confirmed = set()
        self.no_signal_streak = {k: 0 for k in self.channels}
        self.source_metrics = {}
        self._geometry = {}
        self._reservation_signature = {}
        self._core_signature = None
        self._core = []
        self.route_revision = 0
        self._ready = True

    def log(self, event, k=None, **fields):
        row = dict(event=event, action_step=len(self.actions), T=self.T,
                   L_move=self.counts['L_move'], route_revision=self.route_revision, **fields)
        if k is not None:
            row['k'] = k
        self.decisions.append(row)
        return row

    def action(self, kind, k, p):
        p = snap(p)
        if self.channels[k].sigma == 'CLEARED':
            raise RuntimeError('cleared channel cannot be acted on again')
        if kind == 'measure' and p in self.measured_points[k]:
            raise RuntimeError('duplicate snapped position/channel measurement')
        r = BaseController.action(self, kind, k, p)
        if kind == 'measure':
            self.measured_points[k].add(p)
        self.actions[-1].update(action_step=len(self.actions), L_move=self.counts['L_move'])
        return r

    def direction(self, s, theta):
        first = s.sigma == 'UNKNOWN'
        BaseController.direction(self, s, theta)
        self._score_routes.pop(s.k, None)
        self._geometry.pop(s.k, None)
        self.cancel_reservation(s.k, 'new_direction')
        if first:
            s.r_k = self.config.extra_budget
            self.source_metrics[s.k] = dict(
                first_detect_step=len(self.actions), first_detect_position=list(self.p),
                first_detect_T=self.T, first_detect_L_move=self.counts['L_move'],
                scheduled_remeasure_step=None, actual_first_remeasure_step=None,
                first_remeasure_position=None, no_remeasure_reason=None,
                first_remeasure_delay_distance=None, first_remeasure_delay_time=None,
                first_remeasure_wait_time=None, certified_direct_after_opportunity=False)
        self.no_signal_streak[s.k] = 0
        q = certified_clear_point(s.F, s.E)
        if q is not None:
            self.direct_points[s.k] = tuple(q)
            metric = self.source_metrics[s.k]
            metric['certified_direct_after_opportunity'] = s.followups > 0
            if s.followups == 0:
                metric['no_remeasure_reason'] = 'already_clearable'
            self.log('certified_clear', s.k, point=list(q), followups=s.followups)
        else:
            self.direct_points.pop(s.k, None)
        self._core_signature = None

    def cancel_reservation(self, k, reason):
        if k in self.reservations:
            old = self.reservations.pop(k)
            self.log('reservation_cancelled', k, reason=reason, target=list(old.point))
        self._reservation_signature.pop(k, None)

    def opportunity_snapshot(self):
        return [dict(channel=k, point=list(v.point), position=list(v.point), level=v.level,
                     J_hat=v.J_hat, extra_move_s=v.extra_move_s,
                     segment_index=v.segment_index, reservation_epoch=self.route_revision,
                     r_k=self.channels[k].r_k, certificate=v.certificate)
                for k, v in sorted(self.reservations.items())]

    def task_snapshot(self, kind, key):
        if kind == 'search':
            return dict(kind='anchor_scan', anchor_index=key, position=list(self.Z[key]))
        point = (self.reservations[key].point if kind == 'opportunity' else
                 self.direct_points[key] if kind == 'direct' else
                 self.p if kind == 'near' else self.optical_pending[key][0])
        result = dict(kind=kind, channel=key, position=list(point), route_revision=self.route_revision)
        if kind == 'opportunity':
            v = self.reservations[key]
            result.update(level=v.level, J_hat=v.J_hat, extra_move_s=v.extra_move_s)
        return result

    def _complete_anchor(self, j):
        if all(s.sigma != 'UNKNOWN' or j in s.scanned for s in self.channels.values()):
            if j in self.remaining:
                self.remaining.remove(j)
                self.visited.add(j)
                self.log('anchor_complete', anchor=j)
                self._core_signature = None

    def scan(self, j):
        """Exactly one accepted observation, followed by global replanning."""
        ks = [k for k, s in self.channels.items() if s.sigma == 'UNKNOWN' and j not in s.scanned]
        if not ks:
            self._complete_anchor(j)
            return
        k = min(ks, key=lambda k: (k != self.c, k))
        s = self.channels[k]
        r = self.action('measure', k, self.Z[j])
        result = r['measure_result']
        s.scanned[j], s.ell = result, self.ell
        if result == 'direction':
            self.direction(s, r['svd_deg'])
        elif result == 'near':
            s.sigma = 'FOUND'
            self.near_pending.add(k)
            self.log('near', k)
        else:
            # Existing F negative history retains its original physical joint
            # interpretation; new certified followup failures are never added.
            s.F.negative_history.append(self.p)
            if len(s.scanned) == 25 and set(s.scanned.values()) == {'no_signal'}:
                s.sigma = 'ABSENT'
        self._complete_anchor(j)

    def optical(self, s, plan, reason):
        """Freeze a certified finite cover; clear one point per execute_task."""
        if s.k in self.optical_pending:
            return
        if not plan.verifies():
            raise RuntimeError('uncertified optical fallback')
        self.cancel_reservation(s.k, 'optical_fallback')
        self.optical_pending[s.k] = list(plan.route)
        self.optical_plans[s.k] = plan
        metric = self.source_metrics.get(s.k)
        if metric is not None and not s.followups and metric['no_remeasure_reason'] is None:
            metric['no_remeasure_reason'] = ('budget_zero' if s.r_k == 0 else
                                           'no_route_candidate' if reason == 'no_route_candidate' else
                                           'optical_fallback')
        self.log('U', s.k, reason=reason, points=len(plan.route), bound=plan.cost)
        self._core_signature = None

    def _enter_U(self, s, reason):
        self.optical(s, Uhat(s.F, self.p, E=s.E, saved=s.H_k0), reason)

    def followup(self, s, S, fallback=None, **kwargs):
        if s.sigma != 'FOUND' or s.r_k <= 0 or s.k in self.optical_pending:
            raise RuntimeError('followup budget exhausted or source not eligible')
        S = snap(S)
        if not distance_safe(s.F, s.E, S):
            raise RuntimeError('remeasurement lacks whole-F reception certificate')
        candidate = self.reservations.get(s.k)
        r = self.action('measure', s.k, S)
        s.r_k -= 1
        s.followups += 1
        s.ell = self.ell
        metric = self.source_metrics.get(s.k)
        if metric is not None and metric['actual_first_remeasure_step'] is None:
            metric.update(actual_first_remeasure_step=len(self.actions), first_remeasure_position=list(S),
                          first_remeasure_delay_distance=self.counts['L_move'] - metric['first_detect_L_move'],
                          first_remeasure_delay_time=self.T - metric['first_detect_T'],
                          first_remeasure_wait_time=self.T - metric['first_detect_T'] - 5)
        self.log('remeasurement', s.k, point=list(S), result=r['measure_result'], r_k=s.r_k,
                 level=candidate.level if candidate else None,
                 J_hat=candidate.J_hat if candidate else None,
                 extra_move_s=candidate.extra_move_s if candidate else None)
        self.cancel_reservation(s.k, 'executed')
        result = r['measure_result']
        if s.r_k == 0:
            self.log('budget_exhausted', s.k, followups=s.followups)
        if result == 'near':
            self.near_pending.add(s.k)
            self.log('near', s.k)
        elif result == 'direction':
            self.direction(s, r['svd_deg'])
        else:
            # This accepted, distance-certified failure excludes omnidirectionality
            # only. F/P/E/H remain exactly unchanged; no phi predictor is created.
            self.no_signal_streak[s.k] += 1
            self.log('safe_no_signal', s.k, streak=self.no_signal_streak[s.k])
            if self.config.problem_id == 3:
                raise RuntimeError('Q3 certified reception returned no_signal: protocol/certificate anomaly')
            self.directional_confirmed.add(s.k)
            if s.r_k == 0 or self.no_signal_streak[s.k] >= self.config.consecutive_no_signal_limit:
                self._enter_U(s, 'continuous_no_signal' if s.r_k else 'budget_zero')
        if s.r_k == 0 and s.k not in self.direct_points and s.k not in self.near_pending:
            self._enter_U(s, 'budget_zero')
        return result

    def _task_U(self, k, p, b):
        if self._ready and k in self.optical_pending:
            route = self.optical_pending[k]
            if k not in self._optical_summaries or self._optical_summaries[k][0] != len(route):
                self._optical_summaries[k] = (len(route), path_cost(route, route[0]))
            return (math.dist(p, route[0]) / 5 + self._optical_summaries[k][1] +
                    (0 if b is None else math.dist(route[-1], b) / 5))
        return super()._task_U(k, p, b)

    def _point(self, task):
        kind, key = task
        if kind == 'search':
            return self.Z[key]
        if kind == 'direct':
            return self.direct_points[key]
        if kind == 'opportunity':
            return self.reservations[key].point
        return None  # Adaptive U has no invented successful endpoint.

    def schedule_cost(self, tasks):
        if not self._ready:
            return super().schedule_cost(tasks)
        p, c, cost = self.p, self.c, 0.
        served = set()
        unknown = [k for k, s in self.channels.items() if s.sigma == 'UNKNOWN']
        for i, (kind, key) in enumerate(tasks):
            if kind in ('source', 'optical'):
                # The concrete policy returns to a known next point after any
                # early success. This makes multiple bundles composable.
                b = next((q for t in tasks[i + 1:] if (q := self._point(t)) is not None), p)
                cost += self._task_U(key, p, b)
                p = b
                served.add(key)
                continue
            q = self._point((kind, key))
            cost += math.dist(p, q) / 5
            p = q
            if kind == 'search':
                ks = [k for k in unknown if key not in self.channels[k].scanned]
                for k in sorted(ks, key=lambda k: (k != c, k)):
                    cost += 5 + (k != c)
                    c = k
            elif kind == 'direct':
                cost += 5
                served.add(key)
            elif kind == 'opportunity':
                cost += 5 + (key != c)
                c = key
            else:
                raise ValueError(f'unknown task kind {kind}')
        # Retain every known source in the common terminal policy, including
        # all radio failures; no sampled J is converted into a fictional cost.
        for k, s in self.channels.items():
            if s.sigma == 'FOUND' and k not in served:
                cost += self._task_U(k, p, p)
        return cost + self._unknown_reserve()

    def _build_core(self):
        signature = (tuple(self.remaining), tuple(sorted(self.direct_points)),
                     tuple((k, s.ell) for k, s in self.channels.items() if s.sigma == 'FOUND'),
                     tuple((k, len(route)) for k, route in sorted(self.optical_pending.items())),
                     self.p, self.c)
        if signature == self._core_signature:
            return list(self._core)
        route = [('search', j) for j in self.remaining]
        # All certified hard clear points are inserted using the complete
        # C/U policy cost. Reversals also evaluate that complete cost.
        pending = set(self.direct_points)
        while pending:
            options = []
            for k in sorted(pending):
                for i in range(len(route) + 1):
                    trial = route[:i] + [('direct', k)] + route[i:]
                    options.append((self.schedule_cost(trial), k, i, trial))
            _, k, _, route = min(options, key=lambda row: row[:3])
            pending.remove(k)
        # Finish an anchor's outstanding UNKNOWN scan while physically at it.
        # Newly discovered opportunities may still execute before its next scan.
        fixed = 1 if route and route[0][0] == 'search' and self.p == self.Z[route[0][1]] else 0
        if self.opt_rounds and self.direct_points:
            route = route[:fixed] + list(two_opt(route[fixed:],
                lambda tail: self.schedule_cost(route[:fixed] + list(tail)), rounds=self.opt_rounds))
        self.remaining = [j for kind, j in route if kind == 'search']
        self.route_revision += 1
        self._core = list(route)
        self._core_signature = (tuple(self.remaining), *signature[1:])
        return route

    def _prepare(self, s):
        if s.k not in self._geometry:
            from opportunities import PreparedRouteGeometry
            self._geometry[s.k] = PreparedRouteGeometry(s.F, s.E,
                measured_points=self.measured_points[s.k], options=asdict(self.config))
        self._geometry[s.k].exclude_measured(self.measured_points[s.k])
        return self._geometry[s.k]

    def _active_fallback(self, s):
        """Level 3 is still gated by the existing full worst-branch C4/U."""
        if s.followups:
            self._enter_U(s, 'no_route_candidate')
            return None
        plan = C4(s.F, self.p, self.c, s.k, s.r_k, E=s.E, saved=s.H_k0)
        self.log('C4/U', s.k, C4=plan.cost, U=plan.U.cost, S=plan.S)
        if plan.S is None:
            self.optical(s, plan.U, 'no_route_candidate')
            return None
        candidates = self._prepare(s).global_candidates([(self.p, self.p)])
        # Evaluate the chosen Q2 point with the same unchanged-F fallback,
        # rather than carrying over the cost of a different C4 point.
        if candidates:
            candidate = min(candidates, key=lambda v: (v.J_hat, v.point))
            fallback = Uhat(s.F, candidate.point, E=s.E, saved=plan.U)
            C = math.dist(self.p, candidate.point) / 5 + (self.c != s.k) + 5 + fallback.cost
            if C < plan.U.cost - 1e-9:
                return candidate
        self.optical(s, plan.U, 'C4>=U')
        return None

    def _update_reservations(self, core):
        points = [self.p] + [self._point(t) for t in core]
        segments = list(zip(points, points[1:]))
        for k, s in self.channels.items():
            if s.sigma != 'FOUND' or k in self.direct_points or k in self.near_pending or k in self.optical_pending:
                continue
            if not s.r_k:
                self._enter_U(s, 'budget_zero')
                continue
            signature = (s.ell, tuple(segments), tuple(sorted(self.measured_points[k])))
            if signature == self._reservation_signature.get(k):
                continue
            geometry = self._prepare(s)
            if self.config.candidate_policy == 'global_q2':
                # Controlled candidate-location ablation: same finite budget,
                # stopping rules and certificates, intentionally relax tau.
                candidates = geometry.global_candidates(segments or [(self.p, self.p)])
            else:
                candidates = geometry.path_candidates(segments)
                if not candidates:
                    candidates = geometry.nearby_candidates(segments, self.config.tau_route_s)
            candidates = [v for v in candidates if tuple(v.point) not in self.measured_points[k]
                          and math.isfinite(v.J_hat)]
            selected = min(candidates, key=lambda v: (v.J_hat, v.extra_move_s, v.segment_index, v.point)) if candidates else None
            if selected is None:
                selected = self._active_fallback(s)
            if selected is None:
                continue
            old = self.reservations.get(k)
            self.reservations[k] = selected
            self._reservation_signature[k] = signature
            if old != selected:
                self.log('remeasurement_scheduled', k, target=list(selected.point), level=selected.level,
                         J_hat=selected.J_hat, extra_move_s=selected.extra_move_s,
                         segment_index=selected.segment_index, certificate=selected.certificate,
                         replaces=None if old is None else list(old.point))
            metric = self.source_metrics.get(k)
            if metric is not None and metric['scheduled_remeasure_step'] is None:
                metric['scheduled_remeasure_step'] = len(self.actions)

    def next_tasks(self):
        if self._finished():
            return []
        if self.near_pending:
            return [('near', min(self.near_pending))]
        for j in list(self.remaining):
            self._complete_anchor(j)
        core = self._build_core()
        self._update_reservations(core)
        # Only one appointment per segment enters this executable plan. Other
        # channels retain reservations and are reassessed after the next real
        # response. Two individually cheap detours can otherwise combine into
        # a large crossing detour, even when each met tau on the original line.
        route, A = [], self.p
        self._execution_segments = {}
        for i in range(len(core) + 1):
            B = self._point(core[i]) if i < len(core) else A
            attached = [(k, v) for k, v in self.reservations.items()
                        if max(0, min(v.segment_index, len(core))) == i]
            if attached:
                k, v = min(attached, key=lambda kv: (kv[1].J_hat, kv[1].extra_move_s, kv[0]))
                route.append(('opportunity', k))
                self._execution_segments[k] = (A, B)
            if i < len(core):
                route.append(core[i])
                A = B
        # Insert one adaptive U bundle, exactly as the existing scheduler, with
        # all remaining known covers included in the score. Execute only its
        # first clear point, then assess the complete remaining plan again.
        best = None
        baseline = self.schedule_cost(route)
        for k in sorted(self.optical_pending):
            for i in range(len(route) + 1):
                # Keep the following deterministic endpoint of an opportunity
                # adjacent to it; a U bundle can be inserted before the pair.
                if i and route[i - 1][0] == 'opportunity':
                    continue
                trial = route[:i] + [('optical', k)] + route[i:]
                value = (self.schedule_cost(trial) - baseline, k, i)
                if best is None or value < best[0]:
                    best = value, trial
        if best:
            route = best[1]
        # A tail opportunity without remaining anchors has no zero-detour
        # interpretation; only the explicit global-Q2 ablation can add it.
        self.log('replan', tasks=len(route), reservations=len(self.reservations))
        return route

    def _cleared(self, k):
        s = self.channels[k]
        s.sigma, s.ell = 'CLEARED', self.ell
        self.cancel_reservation(k, 'cleared')
        self.direct_points.pop(k, None)
        self.optical_pending.pop(k, None)
        self.near_pending.discard(k)
        self._geometry.pop(k, None)
        self._core_signature = None
        self.log('cleared', k, followups=s.followups)

    def execute_task(self, kind, key):
        if kind == 'search':
            self.scan(key)
        elif kind == 'opportunity':
            candidate = self.reservations[key]
            A, B = self._execution_segments[key]
            delta = (math.dist(self.p, candidate.point) + math.dist(candidate.point, B) -
                     math.dist(self.p, B)) / 5
            limit = math.sqrt(2) / 5000 if candidate.level == 1 else self.config.tau_route_s
            if self.config.candidate_policy == 'route' and candidate.level in (1, 2) and delta > limit + 1e-9:
                raise RuntimeError('stale opportunity exceeds actual next-segment detour bound')
            self.log('opportunity_detour_verified', key, A=list(self.p), B=list(B),
                     point=list(candidate.point), actual_extra_move_s=delta)
            self.followup(self.channels[key], candidate.point)
        elif kind in ('direct', 'near', 'optical'):
            q = self.p if kind == 'near' else self.direct_points[key] if kind == 'direct' else self.optical_pending[key][0]
            r = self.action('clear', key, q)
            if r['clear_result'] == 'success':
                self._cleared(key)
            elif kind != 'optical':
                raise RuntimeError('certified clear/near failed: protocol/certificate anomaly')
            else:
                self.optical_pending[key].pop(0)
                if not self.optical_pending[key]:
                    raise RuntimeError('certified finite F cover exhausted without success')
        else:
            raise ValueError(f'unknown task {kind}')

    def run(self):
        entered, failure = False, None
        try:
            r = self.client.enter()
            if r.get('accepted') is not True:
                raise RuntimeError(str(r))
            entered = True
            while not self._finished():
                tasks = self.next_tasks()
                if not tasks:
                    raise RuntimeError('unresolved channels without executable tasks')
                self.execute_task(*tasks[0])
        except Exception as exc:
            failure = f'{type(exc).__name__}: {exc}'
        finally:
            if entered:
                try:
                    r = self.client.exit()
                    if r.get('accepted') is not True:
                        raise RuntimeError(str(r))
                except Exception as exc:
                    failure = f'{failure or ""}; exit failed: {exc}'
        complete = failure is None and self._finished()
        for k, metric in self.source_metrics.items():
            s = self.channels[k]
            metric['until_clear_no_remeasure'] = s.sigma == 'CLEARED' and s.followups == 0
            if metric['until_clear_no_remeasure'] and metric['no_remeasure_reason'] is None:
                metric['no_remeasure_reason'] = 'other'
            metric.update(followups=s.followups, r_k=s.r_k, sigma=s.sigma,
                          directional_confirmed=k in self.directional_confirmed)
        return dict(completed=complete, failure=failure, T=self.T, counts=self.counts,
            completion_reason='maximum_16_cleared' if self.counts['N_succ'] >= 16 else
                              'all_channels_resolved' if complete else 'incomplete',
            discovery_certificate=self.certificate, initial_order=self.initial_order,
            route_config=asdict(self.config), score_runtime=self.score_runtime, route_metrics=self.source_metrics,
            channels={k: dict(sigma=s.sigma, r_k=s.r_k, followups=s.followups,
                              scans=len(s.scanned), directions=len(s.F.directions))
                      for k, s in self.channels.items()}, decisions=self.decisions)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:2027')
    parser.add_argument('--robot-id', default='q4-route-local')
    parser.add_argument('--extra-budget', type=int, default=2)
    parser.add_argument('--tau-route', type=float, default=5)
    parser.add_argument('--output', type=Path, default=Path('q4-route-run'))
    args = parser.parse_args()
    prepare_runtime()
    controller = Controller(RestClient(args.url, args.robot_id), config=RouteConfig(
        extra_budget=args.extra_budget, tau_route_s=args.tau_route))
    result = controller.run()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (args.output / 'actions.jsonl').write_text(''.join(json.dumps(a, ensure_ascii=False) + '\n'
                                            for a in controller.actions), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('completed', 'failure', 'T', 'counts')}, ensure_ascii=False))
    return 0 if result['completed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
