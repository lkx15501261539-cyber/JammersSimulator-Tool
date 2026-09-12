"""Target detail follows completed observations and closes at clear completion."""
import copy
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication
from enhanced.ui import Window,configure_app
from enhanced.world import ScenarioConfig


def sample_run():
    strategy=dict(available=True,phase='service',queue_kind='route',
        current_target=dict(kind='measure',channel=8,position=[100.,0.],role='target_followup'),
        tasks=[],route_points=[dict(index=0,position=[0.,0.])],channel_iterations={})
    events=[
        dict(type='StrategyState',start=0.,end=0.,data=strategy),
        dict(type='Measure',start=0.,end=5.,data=dict(channel=8,position=[0.,0.],result='direction',svd_deg=30.)),
        dict(type='Measure',start=5.,end=10.,data=dict(channel=8,position=[100.,0.],result='direction',svd_deg=120.)),
        dict(type='LocalizationUpdate',start=10.,end=10.,data=dict(channel=8,vertices=[[70.,40.],[80.,40.],[75.,48.]],diameter=10.,center=[75.,40.],radius=5.,farthest_pair=[[70.,40.],[80.,40.]],circle_covers=False,status='多边形')),
        dict(type='Clear',start=10.,end=15.,data=dict(channel=8,position=[75.,40.],result='success',radius=20.)),
        dict(type='MissionEnd',start=15.,end=15.,data={})]
    for i,e in enumerate(events):e['seq']=i
    return dict(events=events,sources=[dict(channel=8,x=75.,y=43.,recv_radius=1200.)],metadata=dict(seed=42,scenario='uniform',strategy='test observer'))


@pytest.fixture
def window():
    app=QApplication.instance() or QApplication([]);configure_app(app)
    w=Window(ScenarioConfig(),None);w.timer.stop();w.show();app.processEvents()
    yield app,w
    w.close();w.deleteLater();app.processEvents()


def test_detail_appears_after_direction_and_closes_only_when_clear_completes(window,tmp_path):
    app,w=window;run=sample_run();unchanged=copy.deepcopy(run);w.set_run(run)
    for t,visible in [(4.9,False),(5.,True),(9.9,True),(10.,True),(14.99,True),(15.,False),(10.,True),(0.,False)]:
        w.t=t;w.render();app.processEvents()
        assert w.localization_dock.isVisible()==visible
        if visible:assert w.localization_view.channel==8
    w.t=10.;w.render();app.processEvents()
    assert len(w.localization_view.measurements)==2
    assert w.localization_view.localization['diameter']==10.
    w.localization_dock.close();w.render();assert not w.localization_dock.isVisible()
    assert w.details_button.isEnabled()
    w.details_button.click();assert w.localization_dock.isVisible()
    assert w.grab().save(str(tmp_path/'localization.png'))
    assert run==unchanged


def test_zoom_buttons_slider_and_follow_checkbox_remain_synchronized(window):
    app,w=window
    assert w.zoom_value.text()=='100%'
    w.zoom_in.click();assert w.map.zoom_factor()==pytest.approx(1.25)
    w.zoom_out.click();assert w.map.zoom_factor()==pytest.approx(1.)
    w.zoom_slider.setValue(150);assert w.map.zoom_factor()==pytest.approx(2**1.5)
    w.follow.setChecked(True);assert w.map.follow_robot
    w.map._pan_pixels(QPointF(5.,0.));assert not w.follow.isChecked()
    w.reset_camera();assert w.zoom_value.text()=='100%' and w.zoom_slider.value()==0


def test_gui_defaults_to_uniform_real_time_and_manual_speed_stays_uniform(window,monkeypatch):
    from enhanced import ui
    _,w=window
    assert w.speed.currentText()=='1x'
    assert not hasattr(w,'slow')
    w.set_run(sample_run());w.t=4.5;w.playing=True;w.last_tick=10.
    monkeypatch.setattr(ui.time,'monotonic',lambda:11.)
    w.tick();assert w.t==pytest.approx(5.5)
    w.speed.setCurrentText('20x');w.t=9.5;w.last_tick=20.
    monkeypatch.setattr(ui.time,'monotonic',lambda:20.05)
    w.tick();assert w.t==pytest.approx(10.5)


def test_failed_clear_keeps_detail_until_next_target_or_mission_end(window):
    app,w=window;run=sample_run();events=run['events']
    events[4]['end']=13.;events[4]['data']['result']='failure'
    events.insert(5,dict(type='StrategyState',start=13.,end=13.,data=dict(available=True,phase='anchor_scan',
        current_target=dict(kind='measure',channel=2,position=[100.,0.],role='anchor_scan'),tasks=[],route_points=[])))
    events[-1]['start']=events[-1]['end']=20.
    for i,event in enumerate(events):event['seq']=i
    w.set_run(run)
    for t,visible in [(14.,True),(20.,False),(14.,True),(4.9,False)]:
        w.t=t;w.render();app.processEvents()
        assert w.localization_dock.isVisible()==visible
        if visible:assert w.localization_view.channel==8
