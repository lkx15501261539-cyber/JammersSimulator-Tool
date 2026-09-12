"""Actual Q4 GUI backend, source isolation, observer parity and cancellation."""
import math

from enhanced.q4_adapter import run_q4, _controller_module
from enhanced.world import World,ScenarioConfig
from enhanced.runner import ResponseClient
from enhanced.replay import project,load_run,validate


def test_q4_backend_runs_actual_controller_and_saves_replay(tmp_path):
    cfg=ScenarioConfig(seed=48,error_model='worst_edge')
    progress=[]
    run=run_q4(cfg,tmp_path/'q4',progress=progress.append)
    assert run['metadata']['problem']==4 and run['metadata']['model']=='q4_cu'
    assert run['metadata']['completion']=='completed'
    assert {s['source_type'] for s in run['sources']}=={'directional','omnidirectional'}
    validate(run['events'])
    final=project(run['events'],run['events'][-1]['end'])
    assert len(final['cleared'])==len(run['sources'])
    assert final['strategy']['available'] and len(final['route_points'])==25
    assert all(p['scan_completed'] for p in final['route_points'])
    assert all(s['limit']==3 for s in final['strategy']['channel_iterations'].values())
    snapshots=[e for e in run['events'] if e['type']=='StrategyState']
    assert any(e['data']['q4_decision'] for e in snapshots)
    assert load_run(tmp_path/'q4')['metadata']['model']=='q4_cu'
    assert progress[-1]['phase']=='complete'


def test_readonly_observer_does_not_change_q4_policy():
    cfg=ScenarioConfig(seed=49,problem=4,error_model='worst_edge')
    run=run_q4(cfg)
    w=World(cfg)
    controller=_controller_module().Controller(ResponseClient(w.request))
    result=controller.run()
    assert result==run['summary']
    physical=[e for e in run['events'] if e['type']!='StrategyState']
    assert [{k:v for k,v in e.items() if k!='seq'} for e in physical]==[
        {k:v for k,v in e.items() if k!='seq'} for e in w.events]
    assert math.isclose(result['T'],w.t,abs_tol=1e-7)


def test_q4_cancellation_saves_partial_session(tmp_path):
    state={'count':0}
    def progress(p): state['count']=p['action_count']
    run=run_q4(ScenarioConfig(seed=50),tmp_path/'cancelled',progress=progress,
               cancelled=lambda:state['count']>=10)
    assert run['metadata']['completion']=='cancelled'
    assert not run['summary']['completed'] and len(run['events'])>0
    assert any(e['type']=='MissionEnd' for e in run['events'])
    assert load_run(tmp_path/'cancelled')['metadata']['completion']=='cancelled'
