"""Actual Q4 GUI backend, source isolation, observer parity and cancellation."""
import math
import hashlib
import json
import pytest

from enhanced import q4_adapter
from enhanced.q4_adapter import run_q4, _controller_module, Q4_MODEL_LABELS, CODE
from enhanced.world import World,ScenarioConfig
from enhanced.runner import ResponseClient
from enhanced.replay import project,load_run,validate


@pytest.mark.parametrize('model',list(Q4_MODEL_LABELS))
def test_q4_backend_runs_actual_controller_and_saves_replay(tmp_path,model):
    cfg=ScenarioConfig(seed=48,error_model='worst_edge')
    progress=[]
    run=run_q4(cfg,tmp_path/'q4',progress=progress.append,model=model)
    assert run['metadata']['problem']==4 and run['metadata']['model']==model
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
    assert all(e['data']['model']==model for e in snapshots)
    if model == 'q4_opportunity_v2':
        assert all(isinstance(e['data']['opportunities'],list) for e in snapshots)
        assert all(isinstance(e['data']['anchor_measurements'],dict) for e in snapshots)
        assert {'q4_v2.py','opportunities.py','q4.py','cu.py','第一问.py'} == set(run['metadata']['source_files_sha256'])
        assert run['metadata']['opportunity_stats']==run['summary']['opportunities']
        assert load_run(tmp_path/'q4')['metadata']['opportunity_stats']==run['summary']['opportunities']
        assert json.loads((tmp_path/'q4'/'controller.json').read_text())['opportunities']==run['summary']['opportunities']
    for name,sha in run['metadata']['source_files_sha256'].items():
        assert hashlib.sha256((CODE/name).read_bytes()).hexdigest() == sha
    assert load_run(tmp_path/'q4')['metadata']['model']==model
    assert progress[-1]['phase']=='complete'


@pytest.mark.parametrize('model',list(Q4_MODEL_LABELS))
def test_readonly_observer_does_not_change_q4_policy(model):
    cfg=ScenarioConfig(seed=49,problem=4,error_model='worst_edge')
    run=run_q4(cfg,model=model)
    w=World(cfg)
    controller=_controller_module(model).Controller(ResponseClient(w.request))
    result=controller.run()
    assert result==run['summary']
    physical=[e for e in run['events'] if e['type']!='StrategyState']
    assert [{k:v for k,v in e.items() if k!='seq'} for e in physical]==[
        {k:v for k,v in e.items() if k!='seq'} for e in w.events]
    assert math.isclose(result['T'],w.t,abs_tol=1e-7)


@pytest.mark.parametrize('model',list(Q4_MODEL_LABELS))
def test_q4_cancellation_saves_partial_session(tmp_path,model):
    state={'count':0}
    def progress(p): state['count']=p['action_count']
    run=run_q4(ScenarioConfig(seed=50),tmp_path/'cancelled',progress=progress,
               cancelled=lambda:state['count']>=10,model=model)
    assert run['metadata']['completion']=='cancelled'
    assert not run['summary']['completed'] and len(run['events'])>0
    assert any(e['type']=='MissionEnd' for e in run['events'])
    assert load_run(tmp_path/'cancelled')['metadata']['completion']=='cancelled'
    assert load_run(tmp_path/'cancelled')['metadata']['model']==model


def test_unknown_q4_model_is_rejected_before_running():
    with pytest.raises(ValueError,match='Unknown Q4 model'):
        run_q4(model='missing-version')


def test_q4_v2_maximum16_success_does_not_invent_absence_or_completed_scans(monkeypatch,tmp_path):
    class NearWorld(World):
        def __init__(self,config):
            super().__init__(config)
            self.sources=[dict(channel=k,x=1.,y=1.,recv_radius=1000.,
                               source_type='directional',orientation=225.) for k in range(1,17)]
    monkeypatch.setattr(q4_adapter,'World',NearWorld)
    run=run_q4(ScenarioConfig(count=16,problem=4),tmp_path/'sixteen',model='q4_opportunity_v2')
    assert run['metadata']['completion']=='completed'
    assert run['metadata']['completion_reason']=='maximum_16_cleared'
    assert len([s for s in run['summary']['channels'].values() if s['sigma']=='CLEARED'])==16
    assert all(run['summary']['channels'][k]['sigma']=='UNKNOWN' for k in range(17,21))
    assert run['summary']['counts']['N_meas']==16
    assert run['summary']['counts']['N_succ']==16
    assert math.isclose(run['summary']['T'],175.,abs_tol=1e-8)
    final=project(run['events'],run['events'][-1]['end'])
    assert len(final['cleared'])==16
    assert not any(p['scan_completed'] for p in final['route_points'])
    assert load_run(tmp_path/'sixteen')['metadata']['completion_reason']=='maximum_16_cleared'
