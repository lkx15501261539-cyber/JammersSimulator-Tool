import copy
import json
import math
from pathlib import Path
import threading
from urllib.request import Request, urlopen
import pytest
from enhanced.world import World, ScenarioConfig, SCENARIOS, generate
from enhanced.runner import ResponseClient, simulate
from enhanced.strategy import DEFAULT_Q1
from enhanced.replay import project, load_run, validate, metrics

@pytest.mark.parametrize('scenario',SCENARIOS)
def test_seed_and_geometry(scenario):
    config = ScenarioConfig(seed=27,scenario=scenario)
    sources = generate(config)
    assert sources == generate(config)
    assert 10 <= len(sources) <= 16
    assert len({s['channel'] for s in sources}) == len(sources)
    assert all(math.hypot(s['x'],s['y']) <= 1800 and 1000 <= s['recv_radius'] <= 1500 for s in sources)
    if scenario == 'repulsive':
        assert all(math.dist((a['x'],a['y']),(b['x'],b['y'])) >= 250 for i,a in enumerate(sources) for b in sources[i+1:])


def controlled():
    w = World()
    w.sources = [dict(channel=2,x=100.,y=0.,recv_radius=1200.,source_type='omnidirectional',orientation=0.)]
    c = ResponseClient(w.request)
    c.enter()
    return w,c

@pytest.mark.parametrize('model',['deterministic_hash_fixed','worst_edge'])
def test_fixed_error(model):
    w = World(ScenarioConfig(error_model=model))
    s = w.sources[0]
    c=ResponseClient(w.request); c.enter()
    first = c.measure(s['x']-100,s['y'],s['channel'])
    c.measure(s['x']-200,s['y']+10,s['channel'])
    repeat = c.measure(s['x']-100,s['y'],s['channel'])
    assert first['svd_deg'] == repeat['svd_deg']
    assert first['svd_deg'] == round(first['svd_deg'],2)
    assert abs((first['svd_deg']+180)%360-180) <= 1.005


def test_timing_switch_and_clear_channel():
    w,c = controlled()
    r=c.measure(3,4,2)
    assert r['virtual_time_s']==7
    assert r['cost_breakdown']==dict(movement_s=1,switch_channel_s=1,detection_s=5)
    assert c.measure(3,4,2)['consumed_virtual_duration_s']==5
    r=c.clear(3,4,7)
    assert r['consumed_virtual_duration_s']==3 and w.channel==2
    r=c.clear(100,0,2)
    assert r['clear_result']=='success'
    assert r['cost_breakdown']['clear_s']==2 and w.channel==2
    assert c.measure(100,0,2)['measure_result']=='no_signal'

@pytest.mark.parametrize('distance,result',[(5,'near'),(5.001,'direction'),(1200,'direction'),(1200.001,'no_signal')])
def test_measure_boundaries(distance,result):
    w,c=controlled()
    assert c.measure(100-distance,0,2)['measure_result']==result

@pytest.mark.parametrize('distance,result',[(20,'success'),(20.001,'no_target_in_range')])
def test_clear_boundaries(distance,result):
    w,c=controlled()
    response=c.clear(100-distance,0,2)
    assert response['clear_result']==result
    assert w.channel==1
    assert response['cost_breakdown']['detection_s']==3
    assert response['consumed_virtual_duration_s']==abs(100-distance)/5+(5 if result=='success' else 3)


def test_idempotency_and_atomic_validation():
    w,c=controlled()
    p=dict(robot_id='q1-demo',request_id='unique',position=dict(x=0,y=0),channel=2)
    result=w.request('/measure',p)
    events=copy.deepcopy(w.events)
    assert w.request('/measure',p)==result and w.events==events
    result['position']['x']=123
    assert w.request('/measure',p)['position']['x']==0
    assert not w.request('/measure',dict(p,channel=3))['accepted']
    for index,position in enumerate([dict(x=float('nan'),y=0),dict(x=True,y=0),dict(x=2000001,y=0),dict(x=0)]):
        assert not w.request('/measure',dict(p,request_id=f'bad{index}',position=position))['accepted']
    assert w.events==events
    c.exit()
    assert not c.measure(0,0,2)['accepted']


def test_replay_interpolation_and_no_future_results():
    w,c=controlled()
    c.measure(50,0,2)
    c.clear(100,0,2)
    c.exit()
    halfway=project(w.events,5)
    assert halfway['position']==[25.,0.] and halfway['distance']==25
    assert halfway['measure_count']==0 and halfway['detected']==set()
    switching=project(w.events,10.5)
    assert switching['channel']==1 and switching['status']=='switching'
    assert project(w.events,11)['channel']==2
    assert project(w.events,15.9)['detected']==set()
    assert project(w.events,16)['detected']=={2}
    assert project(w.events,30.9)['cleared']==set()
    assert project(w.events,31)['cleared']=={2}
    assert project(w.events,5)==halfway # rewind does not retain future truth
    bad=copy.deepcopy(w.events); bad[1]['seq']=99
    with pytest.raises(ValueError): validate(bad)
    result=metrics(w.events,1)
    assert sum(result['time_breakdown'].values())==result['virtual_time_s']


def test_q1_integration_and_saved_replay(tmp_path):
    if not DEFAULT_Q1.exists(): pytest.skip('Supply sibling CUMCM-2026 Q1 module for integration')
    path=tmp_path/'run'
    run=simulate(output=path)
    localizations=[e for e in run['events'] if e['type']=='LocalizationUpdate']
    assert localizations and localizations[0]['data']['measurements']>=2
    assert localizations[0]['data']['vertices']
    loaded=load_run(path)
    assert loaded['events']==run['events']
    assert len(list(path.iterdir()))==5
    assert loaded['events'][-1]['type']=='MissionEnd'
    assert simulate()['events']==run['events']
    assert all('sources' not in row['response'] for row in map(json.loads,(path/'observations.jsonl').read_text().splitlines()))


def test_http_original_client_and_duplicate_exit(tmp_path):
    from enhanced.server import make_server
    from simulator_client import SimulatorClient
    server=make_server(port=0,output=tmp_path/'http')
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    try:
        client=SimulatorClient(f'http://127.0.0.1:{server.server_port}',robot_id='test')
        assert client.enter()['accepted']
        assert client.measure(0,0,1)['accepted']
        assert client.clear(0,0,1)['accepted']
        assert client.exit()['accepted']
        assert (tmp_path/'http'/'events.jsonl').exists()
    finally:
        server.shutdown(); server.server_close(); thread.join()
