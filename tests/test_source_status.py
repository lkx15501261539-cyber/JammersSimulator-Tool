"""Source state follows completed observations and committed service order."""
import copy
from enhanced.replay import project
from enhanced.source_status import source_status, selected_source_channel


def fixture_events():
    def strategy(t,phase,role=None,channel=8):
        target=dict(kind='measure',channel=channel,role=role) if role else None
        return dict(type='StrategyState',start=t,end=t,data=dict(phase=phase,current_target=target,tasks=[]))
    def measure(start,channel,result):
        return dict(type='Measure',start=start,end=start+5,data=dict(channel=channel,position=[0.,0.],result=result,svd_deg=30.))
    def clear(start,result):
        return dict(type='Clear',start=start,end=start+(5 if result=='success' else 3),data=dict(channel=8,position=[100.,0.],result=result,radius=20.))
    events=[strategy(0,'anchor_scan','anchor_scan'),measure(0,8,'no_signal'),measure(5,8,'direction'),
            strategy(10,'anchor_scan','anchor_scan',4),measure(10,4,'direction'),
            strategy(15,'service','selected_service'),
            dict(type='Move',start=15,end=35,data=dict(origin=[0.,0.],destination=[100.,0.],distance=100.)),
            clear(35,'failure'),strategy(38,'anchor_scan','anchor_scan',4),measure(38,4,'direction'),
            strategy(43,'service','mec'),clear(43,'success'),dict(type='MissionEnd',start=48,end=48,data={})]
    for index,event in enumerate(events):event['seq']=index
    return events


def test_detection_selection_failure_and_clear_follow_virtual_time_and_rewind():
    events=fixture_events();unchanged=copy.deepcopy(events)
    for t,expected in [(4.99,'unseen'),(5,'unseen'),(9.99,'unseen'),(10,'detected'),
                       (14.99,'detected'),(15,'target'),(37.99,'target'),(38,'detected'),
                       (43,'target'),(47.99,'target'),(48,'cleared'),(15,'target'),(0,'unseen')]:
        state=project(events,t)
        assert source_status(state,8)==expected
        assert source_status(state,19)=='unseen'
    state=project(events,16)
    assert state['channel']==1  # Receiver channel cannot choose the highlighted source.
    assert selected_source_channel(state)==8
    assert source_status(state,4)=='detected'
    assert events==unchanged


def test_tail_queue_selects_only_committed_first_service_and_excludes_finished_sources():
    state=project([],0)
    state['strategy']=dict(phase='tail',current_target=None,
        tasks=[dict(kind='service',channel=9),dict(kind='service',channel=11)],
        candidates=[dict(channel=4)],pending_targets=[dict(channel=5)])
    assert selected_source_channel(state)==9
    assert source_status(state,4)=='unseen'
    state['cleared']={9}
    assert selected_source_channel(state) is None
    assert source_status(state,9)=='cleared'
    state['cleared']=set();state['strategy']['phase']='finished'
    assert selected_source_channel(state) is None
    state['strategy']['phase']='anchor_scan'
    assert selected_source_channel(state) is None
