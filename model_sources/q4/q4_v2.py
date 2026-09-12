"""Q4 v2: certified paired opportunities on the existing 25-point skeleton.

The v1 Q1/C/U implementation stays byte-for-byte unchanged. Opportunities are
preferred as specified by the proposal; without a pair we retain its conservative
C4/U decision. No source truth or stochastic forecast is used by this controller.
"""
import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path

from q4 import Controller as BaseController, RestClient
from cu import (C4, Uhat, optical_routes, path_cost, search_points,
                discovery_certificate, two_opt)
from opportunities import find_pairs, certified_clear_point


@dataclass
class Opportunity:
    left: int
    right: int
    quality: float
    ell: int
    failed_anchor: int | None = None
    guaranteed_anchor: int | None = None

    def pending(self):
        return (self.guaranteed_anchor,) if self.guaranteed_anchor is not None else (self.left, self.right)


class Controller(BaseController):
    def __init__(self, client, *, forward_window=3, opt_rounds=2):
        super().__init__(client)
        if not isinstance(forward_window, int) or forward_window < 0:
            raise ValueError('forward_window must be a nonnegative integer')
        if not isinstance(opt_rounds, int) or not 0 <= opt_rounds <= 3:
            raise ValueError('opt_rounds must be between 0 and 3')
        self.forward_window, self.opt_rounds = forward_window, opt_rounds
        self.opportunities, self.M_j, self.direct_points = {}, {}, {}
        self.visited = set()
        self._active_anchor = None
        self._promote_after_scan = set()
        self._score_routes = {}
        self.opportunity_stats = dict(opportunities_registered=0, opportunity_measurements=0,
                                      safe_no_signal=0, guaranteed_measurements=0,
                                      direct_clears=0, promotions=0)
        # Fixed open initial tour, with O kept first. Normal soft attachments do
        # not rebuild this order on every decision.
        todo, order, p = set(range(1, 25)), [], self.Z[0]
        while todo:
            j = min(todo, key=lambda j: (math.dist(p, self.Z[j]), j))
            todo.remove(j)
            order.append(j)
            p = self.Z[j]
        score = lambda route: self.schedule_cost([('search', 0), *[('search', j) for j in route]])
        self.remaining = [0, *two_opt(order, score, rounds=opt_rounds)]
        self.initial_order = tuple(self.remaining)

    def opportunity_snapshot(self):
        return [dict(channel=k, left=pair.left, right=pair.right, quality=pair.quality,
                     ell=pair.ell, failed_anchor=pair.failed_anchor,
                     guaranteed_anchor=pair.guaranteed_anchor,
                     pending_anchors=list(pair.pending()))
                for k, pair in sorted(self.opportunities.items())]

    def _refresh_attachments(self):
        self.M_j = {}
        for k, pair in self.opportunities.items():
            for j in pair.pending():
                self.M_j.setdefault(j, set()).add(k)

    def _cancel_pair(self, k):
        self.opportunities.pop(k, None)
        self._refresh_attachments()

    def _finished(self):
        return (sum(s.sigma == 'CLEARED' for s in self.channels.values()) >= 16 or
                all(s.sigma in ('CLEARED', 'ABSENT') for s in self.channels.values()))

    def clear_near(self, s):
        super().clear_near(s)
        self._cancel_pair(s.k)
        self.direct_points.pop(s.k, None)

    def _assign_pair(self, s):
        if s.sigma != 'FOUND' or s.r_k < 2 or s.k in self.direct_points:
            return
        future = [j for j in self.remaining if j != self._active_anchor and j not in self.visited]
        if len(future) < 2:
            return
        pairs = find_pairs(s.F, s.E, self.Z, future)
        if not pairs:
            self.decisions.append(dict(event='no_certified_opportunity_pair', k=s.k, r_k=s.r_k))
            return
        position = {j: i for i, j in enumerate(future)}
        pair = min(pairs, key=lambda pair: (
            max(position[pair.left], position[pair.right]),
            -pair.quality,
            not pair.preferred,
            len(self.M_j.get(pair.left, ())) + len(self.M_j.get(pair.right, ())),
            pair.left, pair.right))
        self.opportunities[s.k] = Opportunity(pair.left, pair.right, pair.quality, s.ell)
        self._refresh_attachments()
        self.opportunity_stats['opportunities_registered'] += 1
        self.decisions.append(dict(event='opportunity_pair', k=s.k, ell=s.ell,
                                   left=pair.left, right=pair.right, quality=pair.quality,
                                   preferred_angles=pair.preferred,
                                   r_k=s.r_k, J1=max(position[pair.left], position[pair.right])))

    def direction(self, s, theta):
        super().direction(s, theta)
        self._cancel_pair(s.k)
        self._score_routes.pop(s.k, None)
        q = certified_clear_point(s.F, s.E)
        if q is not None:
            self.direct_points[s.k] = tuple(q)
        else:
            self.direct_points.pop(s.k, None)
            self._assign_pair(s)

    def scan(self, j):
        if j not in self.remaining or j in self.visited:
            raise RuntimeError('search anchor already executed or not scheduled')
        self._active_anchor = j
        # UNKNOWN scanning and FOUND opportunities are different records, but
        # one shared physical channel sequence gives the true switching cost.
        unknown = {k for k, s in self.channels.items() if s.sigma == 'UNKNOWN' and j not in s.scanned}
        attached = set(self.M_j.get(j, ()))
        ks = sorted(unknown | attached, key=lambda k: (k != self.c, k))
        try:
            for k in ks:
                if self._finished():
                    break
                s = self.channels[k]
                if k in unknown and s.sigma == 'UNKNOWN':
                    r = self.action('measure', k, self.Z[j])
                    result = r['measure_result']
                    s.scanned[j], s.ell = result, self.ell
                    if result == 'direction':
                        self.direction(s, r['svd_deg'])
                    elif result == 'near':
                        s.sigma = 'FOUND'
                        self.clear_near(s)
                    else:
                        s.F.negative_history.append(self.p)
                        if len(s.scanned) == 25 and all(v == 'no_signal' for v in s.scanned.values()):
                            s.sigma = 'ABSENT'
                elif s.sigma == 'FOUND' and j in self.M_j and k in self.M_j[j]:
                    fallback = Uhat(s.F, self.Z[j], E=s.E, saved=s.H_k0)
                    self.followup(s, self.Z[j], fallback, anchor=j)
        finally:
            self._active_anchor = None
        self.visited.add(j)
        self.remaining.remove(j)
        # Promote only once per newly observed safe failure. Repeated replans
        # without observations cannot accumulate arbitrary forward movement.
        promote = sorted(self._promote_after_scan)
        self._promote_after_scan.clear()
        promoted_anchors = set()
        for k in promote:
            pair = self.opportunities.get(k)
            if pair is not None and pair.guaranteed_anchor not in promoted_anchors:
                promoted_anchors.add(pair.guaranteed_anchor)
                self._promote(k)

    def followup(self, s, S, fallback, *, anchor=None):
        pair = self.opportunities.get(s.k)
        if anchor is None or pair is None or anchor not in pair.pending():
            # This is the original active-measurement policy, not an earned
            # guarantee. Its no_signal branch still immediately executes U.
            if pair is not None:
                raise RuntimeError('active measurement would consume a reserved opportunity budget')
            return super().followup(s, S, fallback)
        if s.sigma != 'FOUND' or s.r_k <= 0 or tuple(S) != tuple(self.Z[anchor]):
            raise RuntimeError('invalid opportunity state, point or exhausted budget')
        if pair.guaranteed_anchor is None and s.r_k < 2:
            raise RuntimeError('a new pair requires two reserved measurements')
        was_guaranteed = pair.guaranteed_anchor == anchor
        r = self.action('measure', s.k, S)
        s.r_k -= 1
        s.followups += 1
        s.ell = self.ell
        self.opportunity_stats['opportunity_measurements'] += 1
        if was_guaranteed:
            self.opportunity_stats['guaranteed_measurements'] += 1
        result = r['measure_result']
        if result == 'near':
            self.clear_near(s)
        elif result == 'direction':
            self.direction(s, r['svd_deg'])
        else:
            if was_guaranteed:
                raise RuntimeError('guaranteed counterpart returned no_signal: certificate/protocol mismatch')
            opposite = pair.right if anchor == pair.left else pair.left
            if s.r_k < 1 or opposite not in self.remaining or opposite in self.visited:
                raise RuntimeError('guaranteed counterpart or its reserved measurement is unavailable')
            pair.failed_anchor, pair.guaranteed_anchor = anchor, opposite
            self._refresh_attachments()
            self._promote_after_scan.add(s.k)
            self.opportunity_stats['safe_no_signal'] += 1
            # F/P/E/H remain unchanged. An orientation inference is recorded,
            # not converted into a false distance exclusion or ABSENT record.
            self.decisions.append(dict(event='safe_no_signal', k=s.k, anchor=anchor,
                                       guaranteed_anchor=opposite, r_k=s.r_k))
        return result

    def optical(self, s, plan, reason):
        self._cancel_pair(s.k)
        result = super().optical(s, plan, reason)
        self.direct_points.pop(s.k, None)
        return result

    def service(self, k, b=None):
        s = self.channels[k]
        if s.sigma != 'FOUND':
            return
        if k in self.opportunities:
            raise RuntimeError('cannot discard a pending opportunity through source insertion')
        if k in self.direct_points:
            q = self.direct_points[k]
            if self.action('clear', k, q)['clear_result'] != 'success':
                raise RuntimeError('certified direct clear failed: geometry/protocol mismatch')
            s.sigma, s.ell = 'CLEARED', self.ell
            self.opportunity_stats['direct_clears'] += 1
            self.direct_points.pop(k, None)
            self.decisions.append(dict(event='direct_clear', k=k, point=q))
            return
        # Use the original C4 interface and saved branch policy. A direction
        # that earns a new pair must hand control back to the global route.
        saved = s.H_k0
        while s.sigma == 'FOUND':
            plan = C4(s.F, self.p, self.c, k, s.r_k, b, E=s.E, saved=saved)
            self.decisions.append(dict(k=k, event='C4/U', r_k=s.r_k,
                                       C4=plan.cost, U=plan.U.cost, S=plan.S))
            if plan.S is None:
                self.optical(s, plan.U, 'r_k=0' if s.r_k == 0 else 'C4>=U')
                return
            saved = plan.fallback
            self.followup(s, plan.S, saved)
            if k in self.opportunities or k in self.direct_points:
                return

    def _task_U(self, k, p, b):
        if k in self.direct_points:
            q = self.direct_points[k]
            return (math.dist(p, q) + (0 if b is None else math.dist(q, b))) / 5 + 5
        s = self.channels[k]
        signature = id(s.E)
        if k not in self._score_routes or self._score_routes[k][0] != signature:
            summaries = [(r[0], r[-1], path_cost(r, r[0])) for r in optical_routes(s.E)]
            if s.H_k0 is not None:
                r = s.H_k0.route
                summaries.append((r[0], r[-1], path_cost(r, r[0])))
            self._score_routes[k] = signature, summaries
        return min(math.dist(p, first) / 5 + cost +
                   (0 if b is None else math.dist(last, b) / 5)
                   for first, last, cost in self._score_routes[k][1])

    def _unknown_reserve(self):
        """Common worst-case completion reserve, not a probabilistic forecast.

        A 20m square grid covers the entire arena. After finishing discovery,
        each newly discovered source can be cleared on that finite snake and
        return to the last search anchor. This deliberately large, route-common
        reserve cancels in comparisons; it makes the frozen-plan bound include
        unknown future sources without guessing their positions or count.
        """
        known = sum(s.sigma in ('FOUND', 'CLEARED') for s in self.channels.values())
        unknown = sum(s.sigma == 'UNKNOWN' for s in self.channels.values())
        count = min(unknown, max(0, 16 - known))
        n = 181 * 181
        snake = 181 * 3600 + 180 * 20
        endpoint_bound = max(math.hypot(*self.p), *(math.hypot(*z) for z in self.Z))
        one = (snake + 2 * (endpoint_bound + math.hypot(1800, 1800))) / 5 + 3 * n + 2
        return count * one

    def schedule_cost(self, tasks):
        """Upper cost of a concrete frozen completion policy for this state.

        Scan remaining UNKNOWNs; execute each reserved radio opportunity at
        most once; use the current certified F cover for unresolved known
        sources. Failed/early-success branches may omit actions. Source bundles
        join the same following anchor and never invent a successful endpoint.
        Actual rolling replanning can change this bound; it is not a claim that
        every true scenario improves when this upper evaluation decreases.
        """
        p, c, cost = self.p, self.c, 0.
        unknown = {k for k, s in self.channels.items() if s.sigma == 'UNKNOWN'}
        served, measured, joined = set(), set(), False
        for i, (kind, key) in enumerate(tasks):
            if kind == 'source':
                if key in served:
                    raise ValueError('duplicate source bundle')
                if key in self.opportunities:
                    raise ValueError('source bundle conflicts with a reserved opportunity')
                b = self.Z[tasks[i + 1][1]] if i + 1 < len(tasks) and tasks[i + 1][0] == 'search' else p
                cost += self._task_U(key, p, b)
                p, joined = b, (i + 1 < len(tasks) and tasks[i + 1][0] == 'search')
                served.add(key)
            elif kind == 'search':
                z = self.Z[key]
                if not joined:
                    cost += math.dist(p, z) / 5
                p, joined = z, False
                ks = {k for k in unknown if key not in self.channels[k].scanned}
                ks |= {k for k in self.M_j.get(key, ()) if (k, key) not in measured}
                for k in sorted(ks, key=lambda k: (k != c, k)):
                    cost += 5 + (k != c)
                    c = k
                    measured.add((k, key))
            else:
                raise ValueError('unknown task kind')
        # This explicit terminal policy visits each retained cover and returns
        # to p; no unobserved success point is used to start the next source.
        for k, s in self.channels.items():
            if s.sigma == 'FOUND' and k not in served:
                cost += self._task_U(k, p, p)
        return cost + self._unknown_reserve()

    def _promote(self, k):
        pair = self.opportunities.get(k)
        if pair is None or pair.guaranteed_anchor is None:
            return
        j = pair.guaranteed_anchor
        if j not in self.remaining:
            raise RuntimeError('guaranteed anchor disappeared from route')
        index = self.remaining.index(j)
        original = list(self.remaining)
        score = lambda route: self.schedule_cost([('search', x) for x in route])
        baseline = score(original)
        best_cost, best_route, moved = baseline, original, 0
        for h in range(1, min(index, self.forward_window) + 1):
            route = original[:index] + original[index + 1:]
            route.insert(index - h, j)
            cost = score(route)
            if cost < best_cost - 1e-8:
                best_cost, best_route, moved = cost, route, h
        if moved:
            self.remaining = best_route
            self.opportunity_stats['promotions'] += 1
        self.decisions.append(dict(event='limited_forward', k=k, anchor=j, moved=moved,
                                   window=self.forward_window, previous_bound=baseline,
                                   bound=best_cost))

    def next_tasks(self):
        if self._finished():
            return []
        if not any(s.sigma == 'UNKNOWN' for s in self.channels.values()):
            needed = set(self.M_j)
            self.remaining = [j for j in self.remaining if j in needed]
        route = [('search', j) for j in self.remaining]
        base = self.schedule_cost(route)
        candidates = []
        for k, s in self.channels.items():
            if s.sigma == 'FOUND' and k not in self.opportunities:
                for i in range(len(route) + 1):
                    tasks = route[:i] + [('source', k)] + route[i:]
                    candidates.append((self.schedule_cost(tasks) - base, k, i, tasks))
        if candidates:
            delta, k, _, tasks = min(candidates, key=lambda row: row[:3])
            # A safe-failure counterpart can advance only via its one bounded
            # Or-opt event. Otherwise the original finite 2-opt is reused.
            locked = any(pair.guaranteed_anchor is not None for pair in self.opportunities.values())
            route = list(tasks if locked else two_opt(tasks, self.schedule_cost, rounds=self.opt_rounds))
            self.remaining = [key for kind, key in route if kind == 'search']
            self.decisions.append(dict(event='insertion', k=k, delta=delta,
                                       evaluated_slots=len(candidates), plan_bound=self.schedule_cost(route)))
        return route

    def run(self):
        entered, failure = False, None
        try:
            response = self.client.enter()
            if response.get('accepted') is not True:
                raise RuntimeError(str(response))
            entered = True
            self.scan(0)
            while not self._finished():
                tasks = self.next_tasks()
                if not tasks:
                    raise RuntimeError('pending channels but no executable task')
                kind, key = tasks[0]
                if kind == 'search':
                    self.scan(key)
                else:
                    b = self.Z[tasks[1][1]] if len(tasks) > 1 else None
                    self.service(key, b)
        except Exception as exc:
            failure = f'{type(exc).__name__}: {exc}'
        finally:
            if entered:
                try:
                    response = self.client.exit()
                    if response.get('accepted') is not True:
                        raise RuntimeError(str(response))
                except Exception as exc:
                    failure = f'{failure or ""}; exit failed: {exc}'
        complete = failure is None and self._finished()
        reason = ('maximum_16_cleared' if sum(s.sigma == 'CLEARED' for s in self.channels.values()) >= 16
                  else 'all_channels_resolved' if complete else 'incomplete')
        return dict(completed=complete, completion_reason=reason, failure=failure,
                    T=self.T, counts=self.counts, discovery_certificate=self.certificate,
                    initial_order=self.initial_order, opportunities=self.opportunity_stats,
                    channels={k: dict(sigma=s.sigma, r_k=s.r_k, followups=s.followups,
                                      scans=len(s.scanned), directions=len(s.F.directions))
                              for k, s in self.channels.items()}, decisions=self.decisions)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:2027')
    parser.add_argument('--robot-id', default='q4-v2-local')
    parser.add_argument('--output', type=Path, default=Path('q4-v2-run'))
    parser.add_argument('--forward-window', type=int, default=3)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    controller = Controller(RestClient(args.url, args.robot_id), forward_window=args.forward_window)
    result = controller.run()
    (args.output / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (args.output / 'actions.jsonl').write_text(''.join(json.dumps(a, ensure_ascii=False) + '\n'
                                                    for a in controller.actions), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('completed', 'failure', 'T', 'counts', 'opportunities')},
                     ensure_ascii=False, indent=2))
    return 0 if result['completed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
