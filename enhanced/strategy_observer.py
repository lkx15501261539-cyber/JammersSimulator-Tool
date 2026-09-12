"""Read-only instrumentation around the unchanged Baseline controller.

The original controller still performs every calculation, selection and request.
This observer copies only its public decision state; it never invokes a planner,
changes a plan, or receives world truth. A pending candidate is not a task queue.
"""
from contextlib import contextmanager
import copy
import sys


def _point(value):
    return None if value is None else [float(value[0]), float(value[1])]


class StrategyObserver:
    def __init__(self, emit):
        self.emit = emit
        self.controller = None
        self.phase = 'starting'
        self.current_target = None
        self.service_channel = None
        self.tail_order = None
        self.plans = {}
        self.measurements = {}
        self.enabled = True

    def publish(self):
        if not self.enabled or self.controller is None:
            return
        try:
            self.emit(self.snapshot())
        except Exception as exc:
            # Observation failure must not change the original policy execution.
            self.enabled = False
            self.emit(dict(schema_version=1, available=False,
                           unavailable_reason=f'Observer unavailable: {type(exc).__name__}: {exc}'))

    def snapshot(self):
        c = self.controller
        route = [dict(index=i, position=_point(point)) for i, point in enumerate(c.route)]
        iterations = {str(k): dict(followups=int(ch.followups), limit=int(c.cfg['max_followups']),
                                  state=ch.state, measurements=self.measurements.get(k, 0),
                                  discovery=int(ch.discovery)) for k, ch in c.channels.items()}
        tasks = []
        if self.current_target is not None:
            tasks.append(dict(self.current_target, coordinate_status='planned'))
        elif self.service_channel is not None:
            tasks.append(dict(kind='service', channel=int(self.service_channel), position=None,
                              coordinate_status='not_planned'))
        elif self.phase == 'anchor_scan' and c.current_anchor >= 0:
            tasks.append(dict(kind='anchor_scan', anchor_index=c.current_anchor,
                              position=_point(c.route[c.current_anchor]), coordinate_status='fixed'))
        if self.phase != 'finished':
            tasks.extend(dict(kind='anchor_scan', anchor_index=i, position=_point(c.route[i]),
                              coordinate_status='fixed') for i in range(c.current_anchor+1, len(c.route)))
            if self.tail_order is not None:
                for _, k in self.tail_order:
                    if k == self.service_channel or c.channels[k].state != 'FOUND':
                        continue
                    # The actual tail list determines service order, but the
                    # future service point has not necessarily been selected.
                    tasks.append(dict(kind='service', channel=k, position=None,
                                      coordinate_status='not_planned'))
        pending = []
        active_channel = self.service_channel or (self.current_target or {}).get('channel')
        for k, ch in c.channels.items():
            if ch.state != 'FOUND' or k == active_channel:
                continue
            plan = self.plans.get(k)
            pending.append(dict(channel=k, followups=int(ch.followups), discovery=int(ch.discovery),
                                position=copy.deepcopy(plan['position']) if plan else None,
                                coordinate_status='candidate' if plan else 'not_planned'))
        return dict(schema_version=1, available=True, route_name=c.cfg['route_name'],
                    route_points=route, current_anchor=int(c.current_anchor), phase=self.phase,
                    current_target=copy.deepcopy(self.current_target), tasks=tasks,
                    queue_kind='finished' if self.phase == 'finished' else
                               'tail' if self.tail_order is not None else
                               'anchor_scan' if self.phase == 'anchor_scan' else 'route',
                    queue_note='每段最多插入 1 个目标；固定路线后按原模型发现顺序尾扫。候选方案不等于已承诺任务。',
                    pending_targets=pending, candidates=copy.deepcopy(list(self.plans.values())),
                    channel_iterations=iterations)

    def requested(self, path, payload):
        """Called immediately before the original backend sends an actual request."""
        if path in ('/measure', '/clear'):
            p = payload['position']
            self.current_target = dict(kind=path[1:], channel=int(payload['channel']),
                                       position=[float(p['x']), float(p['y'])],
                                       role=(self.current_target or {}).get('role', 'request'))
        elif path == '/exit':
            self.phase = 'finished'
            self.current_target = None
        self.publish()


@contextmanager
def observe_controller(module, emit):
    """Temporarily wrap methods in memory, retaining all original calls/returns."""
    if hasattr(module.Controller, 'execute_optical'):
        with observe_v2_controller(module, emit) as observer:
            yield observer
        return
    original = module.Controller
    observer = StrategyObserver(emit)

    class ObservedController(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            observer.controller = self
            observer.publish()

        def event(self, kind, **data):
            result = super().event(kind, **data)
            if kind == 'measure':
                # A near response immediately invokes a nested clear before
                # Controller.measure returns. Count the completed measurement
                # now, so that clear's snapshot is not one measurement behind.
                k = int(data['channel'])
                observer.measurements[k] = observer.measurements.get(k, 0)+1
            elif kind == 'anchor_start':
                observer.phase = 'anchor_scan'
                observer.current_target = None
                observer.publish()
            elif kind == 'anchor_end':
                observer.phase = 'route'
                observer.current_target = None
                observer.publish()
            return result

        def get_plan(self, k):
            plan = super().get_plan(k)
            if plan is None:
                observer.plans.pop(k, None)
            else:
                observer.plans[k] = dict(channel=int(k), kind=plan['kind'], position=_point(plan['point']))
                if observer.service_channel == k:
                    observer.current_target = dict(observer.plans[k], role='target_followup' if plan['kind'] == 'measure' else 'mec')
            observer.publish()
            return plan

        def pick_for_leg(self, i):
            observer.phase = 'planning'
            observer.publish()
            chosen = super().pick_for_leg(i)
            if chosen is not None:
                k, plan = chosen
                observer.current_target = dict(kind=plan['kind'], channel=int(k),
                                               position=_point(plan['point']), role='selected_service')
            observer.phase = 'route'
            observer.publish()
            return chosen

        def service(self, k, first_plan=None, tail=False):
            observer.service_channel = k
            observer.phase = 'service'
            if tail:
                # This is the real list already materialized by Controller.run,
                # not a second implementation of the controller's ordering.
                frame = sys._getframe(1)
                actual_tail = frame.f_locals.get('tail')
                if isinstance(actual_tail, list):
                    observer.tail_order = [(int(discovery), int(channel)) for discovery, channel in actual_tail]
                del frame
            if first_plan is not None:
                observer.current_target = dict(kind=first_plan['kind'], channel=int(k),
                                               position=_point(first_plan['point']), role='selected_service')
            observer.publish()
            result = super().service(k, first_plan, tail)
            observer.service_channel = None
            observer.current_target = None
            observer.phase = 'tail' if tail else 'route'
            observer.publish()
            return result

        def measure(self, k, p, role):
            observer.current_target = dict(kind='measure', channel=int(k), position=_point(p), role=role)
            observer.publish()
            result = super().measure(k, p, role)
            observer.plans.pop(k, None)
            observer.current_target = None
            observer.publish()
            return result

        def clear(self, k, p, reason, certificate=None):
            observer.current_target = dict(kind='clear', channel=int(k), position=_point(p), role=reason)
            observer.publish()
            result = super().clear(k, p, reason, certificate)
            observer.plans.pop(k, None)
            observer.current_target = None
            observer.publish()
            return result

    module.Controller = ObservedController
    try:
        yield observer
    finally:
        module.Controller = original


class V2StrategyObserver(StrategyObserver):
    """Copy v2's emitted task order and C/U decisions, without planning again."""
    def __init__(self, emit):
        super().__init__(emit)
        self.tasks = []
        self.completed_scans = set()
        self.decision = None
        self.optical_points = []

    def snapshot(self):
        c = self.controller
        tasks = copy.deepcopy(self.tasks)
        if self.phase == 'finished':
            tasks = []
        elif self.current_target is not None:
            # Replace the current abstract source task with its actual action.
            target = dict(self.current_target, coordinate_status='planned')
            if tasks and tasks[0].get('channel') == self.current_target['channel']:
                tasks[0] = target
            else:
                tasks.insert(0, target)
        return dict(schema_version=1, available=True, model='hexagon_v2', problem=3,
            version='baseline-v2.0', route_name=c.cfg['route_name'],
            route_points=[dict(index=i, position=_point(p), scan_completed=i in self.completed_scans)
                          for i, p in enumerate(c.route)],
            current_anchor=int(c.current_anchor), phase=self.phase,
            current_target=copy.deepcopy(self.current_target), tasks=tasks,
            queue_kind='finished' if self.phase == 'finished' else 'cu_tasks',
            queue_note='最小增量插入与两轮任务 2-opt；执行下一任务后重规划，允许连续服务多个源。',
            pending_targets=[dict(channel=int(k), followups=int(ch.followups),
                                  position=None, coordinate_status='not_planned')
                             for k, ch in c.channels.items()
                             if ch.state == 'FOUND' and k != self.service_channel],
            candidates=[], cu_decision=copy.deepcopy(self.decision),
            optical_points=copy.deepcopy(self.optical_points),
            channel_iterations={str(k): dict(followups=int(ch.followups),
                limit=int(c.cfg['max_followups']), r_k=int(c.cfg['max_followups']-ch.followups),
                state=ch.state, measurements=self.measurements.get(k, 0),
                discovery=int(ch.discovery), scanned=len(ch.anchors))
                for k, ch in c.channels.items()})


@contextmanager
def observe_v2_controller(module, emit):
    original = module.Controller
    observer = V2StrategyObserver(emit)

    class ObservedV2Controller(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            observer.controller = self
            observer.publish()

        def event(self, kind, **data):
            result = super().event(kind, **data)
            if kind == 'measure':
                k = int(data['channel'])
                observer.measurements[k] = observer.measurements.get(k, 0)+1
            elif kind == 'global_plan':
                observer.tasks = [dict(kind='anchor_scan', anchor_index=int(key),
                                       position=_point(self.route[key]), coordinate_status='fixed')
                                  if task == 'search' else
                                  dict(kind='service', channel=int(key), position=None,
                                       coordinate_status='not_planned')
                                  for task, key in data['tasks']]
                observer.phase = 'route'
            elif kind == 'anchor_start':
                observer.phase = 'anchor_scan'
                observer.current_target = None
                observer.decision = None
                observer.optical_points = []
            elif kind == 'anchor_end':
                observer.completed_scans.add(int(data['index']))
                observer.tasks = [t for t in observer.tasks
                                  if not (t['kind'] == 'anchor_scan' and t['anchor_index'] == data['index'])]
                observer.phase = 'route'
                observer.current_target = None
            elif kind == 'service_start':
                observer.service_channel = int(data['channel'])
                observer.phase = 'service'
                observer.decision = None
                observer.optical_points = []
            elif kind == 'service_end':
                observer.tasks = [t for t in observer.tasks
                                  if not (t['kind'] == 'service' and t['channel'] == data['channel'])]
                observer.service_channel = None
                observer.current_target = None
                observer.optical_points = []
                observer.phase = 'route'
            elif kind == 'cu_decision':
                observer.decision = module.jsonable(data)
            elif kind == 'optical_start':
                observer.optical_points = [_point(p) for p in data['planned_points']]
            observer.publish()
            return result

        def replan(self):
            observer.phase = 'planning'
            observer.publish()
            return super().replan()

        def measure(self, k, p, role):
            observer.current_target = dict(kind='measure', channel=int(k), position=_point(p), role=role)
            observer.publish()
            result = super().measure(k, p, role)
            observer.current_target = None
            observer.publish()
            return result

        def clear(self, k, p, reason, certificate=None, allow_failure=False):
            observer.current_target = dict(kind='clear', channel=int(k), position=_point(p), role=reason)
            observer.publish()
            result = super().clear(k, p, reason, certificate, allow_failure)
            if observer.optical_points and observer.optical_points[0] == _point(p):
                observer.optical_points.pop(0)
            observer.current_target = None
            observer.publish()
            return result

    module.Controller = ObservedV2Controller
    try:
        yield observer
    finally:
        module.Controller = original
