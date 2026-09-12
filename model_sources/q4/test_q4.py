"""Contract tests plus reproducible synthetic experiments (no official runs).

pytest test_q4.py
python test_q4.py --experiments ../docs/q4-results
"""
import copy
from fractions import Fraction
import json
import math
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from enhanced.world import World, ScenarioConfig, generate
from enhanced.runner import ResponseClient, save_run
from enhanced.server import make_server
from cu import (Feasible, certified_outer, Uhat, C4, S_dist, search_points,
                discovery_certificate, max_d2, path_cost, q1, two_opt)
from q4 import Controller, RestClient


def source(k=1,x=100.,y=0.,orientation=180.,kind='directional',radius=1000.):
    return dict(channel=k,x=x,y=y,orientation=orientation,source_type=kind,recv_radius=radius)


def setup(sources=None):
    world=World(ScenarioConfig(problem=4,error_model='worst_edge'))
    if sources is not None: world.sources=sources
    client=ResponseClient(world.request)
    return world,client


def found_controller():
    w,client=setup([source()])
    ctl=Controller(client)
    client.enter()
    ctl.scan(0)
    return w,ctl,ctl.channels[1]


def test_q1_original_regression():
    # The original module exposes its own 20-case runner.
    import subprocess
    result=subprocess.run([sys.executable,str(Path(__file__).with_name('第一问.py')),'--test'],
                          capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr


def test_continuous_25_certificate():
    cert=discovery_certificate()
    assert cert['passed'] and cert['triangles']==36 and cert['max_edge_m']<981
    Z=list(search_points())
    Z[13]=(0.,0.)
    assert not discovery_certificate(Z)['passed']
    assert not discovery_certificate(Z[:24])['passed']


def test_25_deterministic_position_orientation_crosscheck():
    # Supplementary finite stress check; continuous proof is the test above.
    Z=search_points()
    for radius in (0,5,500,979,980,1000,1500,1799.999,1800):
        for angle in range(0,360,3):
            X=(radius*math.cos(math.radians(angle)),radius*math.sin(math.radians(angle)))
            near=[(z[0]-X[0],z[1]-X[1]) for z in Z if math.dist(z,X)<=1000]
            for phi in range(0,360,5):
                u=(math.cos(math.radians(phi)),math.sin(math.radians(phi)))
                assert any(u[0]*v[0]+u[1]*v[1]>=-1e-9 for v in near)


def test_q4_generation_preserves_q3_geometry():
    a=generate(ScenarioConfig(seed=41))
    b=generate(ScenarioConfig(seed=41,problem=4))
    assert a==generate(ScenarioConfig(seed=41,problem=3))
    for x,y in zip(a,b):
        assert all(x[key]==y[key] for key in ('channel','x','y','recv_radius'))
    assert {s['source_type'] for s in b}=={'directional','omnidirectional'}
    assert all(0<=s['orientation']<360 for s in b)


def test_direction_near_no_signal_and_backside():
    w,c=setup([source(x=0,orientation=0)])
    c.enter()
    cases=[((1000,0),'direction'),((1000.001,0),'no_signal'),
           ((5,0),'near'),((5.001,0),'direction'),((-1,0),'no_signal'),
           ((0,100),'direction'),((0,-100),'direction'),((-100,0),'no_signal')]
    for p,expected in cases:
        r=c.measure(*p,1)
        assert r['measure_result']==expected
        assert ('svd_deg' in r)==(expected=='direction')
    w.sources[0]['orientation']=180
    assert c.measure(0,0,1)['measure_result']=='near'


def test_clear_20_and_channel_independence():
    w,c=setup([source(k=2,x=0,orientation=0)])
    c.enter()
    assert c.clear(-20.001,0,2)['clear_result']=='no_target_in_range'
    r=c.clear(-20,0,2)
    assert r['clear_result']=='success' and w.channel==1
    assert r['cost_breakdown']['clear_s']==2
    assert c.clear(0,0,2)['clear_result']=='no_target_in_range'


def test_near_clear_at_same_position():
    w,c=setup([source(x=3,orientation=180)])
    ctl=Controller(c);c.enter();ctl.scan(0)
    assert ctl.actions[0]['response']['measure_result']=='near'
    assert ctl.actions[1]['kind']=='clear' and ctl.actions[1]['p']==ctl.actions[0]['p']
    assert ctl.channels[1].sigma=='CLEARED'


def test_P_unbounded_F_bounded_and_optical_full_cells():
    F=Feasible([(0,0,0)])
    assert q1.localize(F.directions,error_deg=1.005).status=='无界'
    E=certified_outer(F)
    U=Uhat(F,(0,0),E=E)
    assert U.verifies() and 1<len(U.route)<200
    assert all(max_d2(((a+c)/2,(b+d)/2),(a,b,c,d))<=Fraction('19.9')**2 for a,b,c,d in E)
    assert all(any(a<=x<=c and b<=y<=d for a,b,c,d in E)
               for x,y in [(5.001,0),(1000,0),(1499,0)])
    # An interior point far from the polygon's vertices still has coverage.
    assert any(math.dist((700,0),q)<=20 for q in U.route)


def test_F_joint_negative_keeps_directional_backside():
    F=Feasible([(0,0,0)],[(101,0)])
    assert F.contains((100,0))  # R>=1000, but the negative is behind antenna.
    assert not Feasible([(0,0,0)],[(0,0)]).contains((100,0))


def test_S_dist_continuous_sufficient_certificate():
    F=Feasible([(0,0,0)])
    E=certified_outer(F)
    assert len(S_dist(F,E))>0
    for S in S_dist(F,E):
        for X in ((6,0),(500,1),(1000,-1),(1499,0)):
            assert math.dist(S,X)<=max(1000,math.dist((0,0),X))


def test_no_signal_executes_saved_U_and_does_not_shrink_F():
    w,ctl,s=found_controller()
    old=copy.deepcopy(s.F)
    E,P=s.E,s.P
    plan=Uhat(s.F,(200,0),E=s.E)
    result=ctl.followup(s,(200,0),plan)  # Behind the fixed west-facing source.
    assert result=='no_signal' and s.sigma=='CLEARED'
    assert s.F==old and s.E==E and s.P==P
    assert s.r_k==2 and s.followups==1
    assert ctl.decisions[-1]['reason']=='no_signal'
    assert math.isclose(ctl.T,w.t,abs_tol=1e-7)


def test_r_limit_no_reset_and_forced_U():
    w,ctl,s=found_controller()
    for S in ((10,0),(20,0),(30,0)):
        old_n=len(s.F.directions)
        ctl.followup(s,S,Uhat(s.F,S,E=s.E))
        assert len(s.F.directions)==old_n+1
    assert s.r_k==0 and s.followups==3
    try: ctl.followup(s,(40,0),s.H_k0)
    except RuntimeError: pass
    else: raise AssertionError('fourth followup accepted')
    ctl.service(1)
    assert s.sigma=='CLEARED' and s.followups==3
    assert ctl.decisions[-1]['reason']=='r_k=0'


def test_found_not_scanned_again_and_absent_requires_all_25():
    w,ctl,s=found_controller()
    assert len(s.scanned)==1 and s.sigma=='FOUND'
    assert all(z.sigma!='ABSENT' for z in ctl.channels.values())
    for j in range(1,25): ctl.scan(j)
    assert len(s.scanned)==1
    assert all(z.sigma=='ABSENT' and len(z.scanned)==25 for k,z in ctl.channels.items() if k!=1)


def test_C4_conservative_worst_branch_and_r0():
    F=Feasible([(0,0,0)]);E=certified_outer(F)
    for p in ((0,0),(300,500)):
        for b in (None,(800,800)):
            for r in (0,1,3):
                plan=C4(F,p,2,1,r,b,E=E)
                assert plan.S is None and plan.cost==plan.U.cost
                assert plan.U.verifies()


def test_prefix_cost_bounded_by_full_route():
    F=Feasible([(0,0,0)]);U=Uhat(F,(0,100),(1000,100))
    for i in range(1,len(U.route)+1):
        assert path_cost(U.route[:i],(0,100),(1000,100))<=U.cost+1e-9


def test_full_schedule_counts_join_and_scans_once():
    w,ctl,s=found_controller()
    tasks=[('source',1),('search',1)]
    b=ctl.Z[1]
    # current RF=20; 19 UNKNOWN channels, start with 20 then 2..19 =>18 switches.
    expected=ctl._task_U(1,ctl.p,b)+19*5+18
    assert math.isclose(ctl.schedule_cost(tasks),expected)
    reordered=two_opt(tasks,ctl.schedule_cost)
    assert ctl.schedule_cost(reordered)<=expected+1e-9


def test_protocol_rejection_is_incomplete():
    class Broken:
        def enter(self): return dict(accepted=False,error='test rejection')
    result=Controller(Broken()).run()
    assert not result['completed'] and 'rejection' in result['failure']


def test_http_end_to_end(tmp_path):
    output=tmp_path/'server-run'
    server=make_server(ScenarioConfig(seed=19,problem=4,error_model='worst_edge'),port=0,output=output)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        ctl=Controller(RestClient(f'http://127.0.0.1:{server.server_port}'))
        result=ctl.run()
        assert result['completed'],result['failure']
        assert all(s['sigma'] in ('CLEARED','ABSENT') for s in result['channels'].values())
        assert result['counts']['N_succ']>=10
        assert math.isclose(json.loads((output/'metrics.json').read_text())['virtual_time_s'],result['T'],abs_tol=1e-7)
    finally:
        server.shutdown();thread.join();server.server_close()


def run_experiments(output):
    output=Path(output)
    output.mkdir(parents=True,exist_ok=False)
    results=[]
    for scenario,seed,error in [('uniform',42,'baseline_fixed_field'),('boundary-biased',43,'worst_edge'),
                                ('clustered',44,'baseline_fixed_field'),('repulsive',45,'worst_edge'),
                                ('center-biased',46,'baseline_fixed_field')]:
        cfg=ScenarioConfig(seed=seed,scenario=scenario,problem=4,error_model=error)
        w=World(cfg)
        ctl=Controller(ResponseClient(w.request))
        start=time.monotonic();result=ctl.run();elapsed=time.monotonic()-start
        assert result['completed'],result['failure']
        assert len(w.cleared)==len(w.sources)
        assert math.isclose(ctl.T,w.t,abs_tol=1e-7)
        assert all(s.followups<=3 and s.r_k>=0 for s in ctl.channels.values())
        for k,s in ctl.channels.items():
            if s.sigma=='ABSENT':
                assert all(k!=z['channel'] for z in w.sources)
                assert len(s.scanned)==25 and set(s.scanned.values())=={'no_signal'}
        folder=output/f'{scenario}-{seed}'
        save_run(w,folder,dict(problem=4,seed=seed,scenario=scenario,error_model=error,strategy='Q4 C/U conservative baseline'))
        (folder/'controller.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        row=dict(scenario=scenario,seed=seed,error=error,sources=len(w.sources),cleared=len(w.cleared),
                 completed=result['completed'],T=ctl.T,wall_seconds=elapsed,**ctl.counts,
                 followups=sum(s.followups for s in ctl.channels.values()))
        results.append(row)
        print(json.dumps(row),flush=True)
    (output/'summary.json').write_text(json.dumps(results,indent=2),encoding='utf-8')


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--experiments':
        run_experiments(sys.argv[2])
    else:
        raise SystemExit('Use pytest, or --experiments OUTPUT')
