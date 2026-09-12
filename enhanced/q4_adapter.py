"""Run the bundled Q4 controller and publish read-only GUI/replay snapshots.

The controller receives only the public response client. Observations never
feed world truth back into planning, and no Q3 controller is invoked.
"""
from dataclasses import asdict, replace
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

from .world import ScenarioConfig, World
from .runner import ResponseClient, save_run

BUNDLED_CODE = Path(__file__).resolve().parents[1]/'model_sources'/'q4'
LEGACY_CODE = Path(__file__).resolve().parents[2]/'CUMCM-2026'/'code'
CODE = BUNDLED_CODE if (BUNDLED_CODE/'q4.py').is_file() else LEGACY_CODE
Q4_MODEL_LABELS = {
    'q4_cu': '第四问 · 25 点 C/U · v1.0',
    'q4_opportunity_v2': '第四问 · 左右机会复测 · v2.0',
}
_MODEL_FILES = {
    'q4_cu': ('q4.py', 'cu.py', '第一问.py'),
    'q4_opportunity_v2': ('q4_v2.py', 'opportunities.py', 'q4.py', 'cu.py', '第一问.py'),
}


def _controller_module(model='q4_cu'):
    if model not in Q4_MODEL_LABELS:
        raise ValueError(f'Unknown Q4 model: {model}')
    source = CODE/_MODEL_FILES[model][0]
    name = 'jammers_q4_controller' if model == 'q4_cu' else 'jammers_q4_opportunity_v2'
    if name not in sys.modules:
        if not source.is_file():
            raise FileNotFoundError(f'找不到第四问模型：{source}。请完整下载包含 model_sources/q4 的模拟器仓库。')
        spec = importlib.util.spec_from_file_location(name,source)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name]=module
        sys.path.insert(0,str(CODE))
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(name,None)
            raise
        finally:
            sys.path.remove(str(CODE))
    return sys.modules[name]


def search_points():
    return _controller_module().search_points()


def run_q4(config=ScenarioConfig(problem=4),output=None,progress=None,cancelled=None,model='q4_cu'):
    module = _controller_module(model)
    config = replace(config,problem=4)
    report = progress or (lambda value:None)
    should_cancel = cancelled or (lambda:False)
    world = World(config)
    cancel_seen=False

    def request(path,payload):
        nonlocal cancel_seen
        if path!='/exit' and should_cancel():
            cancel_seen=True
            raise RuntimeError('Q4 cancelled by user')
        return world.request(path,payload)

    class ObservedController(module.Controller):
        def __init__(self,client):
            super().__init__(client)
            self.phase='starting'
            self.target=None
            self.tasks=[]
            self.current_anchor=None
            self.q4_decision=None
            self.measurements={k:0 for k in self.channels}
            self.completed_scans=set()

        def publish(self):
            completed=self.completed_scans
            opportunity_snapshot=getattr(self,'opportunity_snapshot',lambda:[])
            snapshot=dict(schema_version=1,available=True,model=model,problem=4,
                          phase=self.phase,route_name='q4_25_points',
                          route_points=[dict(index=j,position=list(z),scan_completed=j in completed)
                                        for j,z in enumerate(self.Z)],
                          current_anchor=self.current_anchor,current_target=copy.deepcopy(self.target),
                          tasks=copy.deepcopy(self.tasks),queue_kind='q4',
                          queue_note='按当前状态做最小增量插入；执行下一任务后重新规划。',
                          candidates=[],q4_decision=copy.deepcopy(self.q4_decision),
                          opportunities=copy.deepcopy(opportunity_snapshot()),
                          anchor_measurements={str(j):sorted(channels) for j,channels
                                               in getattr(self,'M_j',{}).items()},
                          pending_targets=[dict(channel=k,followups=s.followups) for k,s in self.channels.items()
                                           if s.sigma=='FOUND' and (self.target or {}).get('channel')!=k],
                          channel_iterations={str(k):dict(state=s.sigma,followups=s.followups,
                                                          limit=3,r_k=s.r_k,measurements=self.measurements[k],
                                                          scanned=len(s.scanned))
                                              for k,s in self.channels.items()})
            world.emit('StrategyState',**snapshot)
            report(dict(phase='q4_running',action_count=len(self.actions),virtual_time_s=self.T))

        def scan(self,j):
            self.phase,self.current_anchor='anchor_scan',j
            self.q4_decision=None
            self.target=dict(kind='anchor_scan',anchor_index=j,position=list(self.Z[j]))
            self.publish()
            result=super().scan(j)
            if all(s.sigma!='UNKNOWN' or j in s.scanned for s in self.channels.values()):
                self.completed_scans.add(j)
            self.tasks=[t for t in self.tasks if not (t['kind']=='anchor_scan' and t['anchor_index']==j)]
            self.target=None
            self.phase='planning'
            self.publish()
            return result

        def next_tasks(self):
            result=super().next_tasks()
            self.tasks=[dict(kind='anchor_scan',anchor_index=key,position=list(self.Z[key]))
                        if kind=='search' else dict(kind='service',channel=key,position=None)
                        for kind,key in result]
            self.phase='planning'
            self.publish()
            return result

        def service(self,k,b=None):
            self.phase='service'
            self.q4_decision=None
            self.target=dict(kind='service',channel=k,position=None,role='selected_service')
            self.publish()
            result=super().service(k,b)
            self.tasks=[t for t in self.tasks if not (t['kind']=='service' and t['channel']==k)]
            self.target=None
            self.phase='planning'
            self.publish()
            return result

        def optical(self,s,plan,reason):
            self.q4_decision=dict(channel=s.k,mode='optical',U=plan.cost,reason=reason,
                                  points=len(plan.route),r_k=s.r_k)
            self.publish()
            return super().optical(s,plan,reason)

        def direction(self,s,theta):
            result=super().direction(s,theta)
            self.publish()
            return result

        def followup(self,*args,**kwargs):
            self.q4_decision=None
            result=super().followup(*args,**kwargs)
            self.publish()
            return result

        def action(self,kind,k,p):
            self.target=dict(kind=kind,channel=k,position=list(p),
                             role='selected_service' if self.phase=='service' else 'request')
            self.publish()  # Inputs are visible before transit, results only afterwards.
            result=super().action(kind,k,p)
            if kind=='measure':
                self.measurements[k]+=1
            self.publish()
            return result

    controller=ObservedController(ResponseClient(request))
    controller.publish()
    result=controller.run()
    controller.phase='finished'
    controller.target=None
    controller.tasks=[]
    controller.publish()
    completion='cancelled' if cancel_seen else 'completed' if result['completed'] else 'incomplete_unresolved'
    metadata=dict(schema_version=1,**asdict(config),model=model,
                  strategy='Q4 · 25 点 C/U' if model == 'q4_cu' else 'Q4 · 左右机会复测 · v2.0',
                  strategy_version='1.1-gui' if model == 'q4_cu' else '2.0',
                  completion=completion,failure=result['failure'],
                  source_files_sha256={name:hashlib.sha256((CODE/name).read_bytes()).hexdigest()
                                       for name in _MODEL_FILES[model]})
    if 'completion_reason' in result:
        metadata['completion_reason']=result['completion_reason']
    if 'opportunities' in result:
        metadata['opportunity_stats']=copy.deepcopy(result['opportunities'])
    if output is not None:
        save_run(world,output,metadata)
        (Path(output)/'controller.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    report(dict(phase='complete',action_count=len(controller.actions),virtual_time_s=controller.T))
    return dict(events=world.events,sources=world.sources,metadata=metadata,
                summary=result,directory=str(Path(output).resolve()) if output is not None else None)
