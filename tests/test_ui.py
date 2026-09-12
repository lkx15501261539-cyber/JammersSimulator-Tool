import os
import time
import threading
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPalette, QColor
from enhanced.ui import Window, MapView, configure_app
from enhanced.replay import project
from enhanced.world import ScenarioConfig
from enhanced.runner import simulate
from enhanced.strategy import DEFAULT_Q1


def wait_for_worker(app,window,timeout=15):
    deadline = time.monotonic()+timeout
    while window.worker is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert window.worker is None, 'Simulation worker did not finish'


def sample_run(completion='completed'):
    return dict(events=[dict(seq=0,type='MissionEnd',start=0.,end=1.,data={})],sources=[],
                metadata=dict(scenario='uniform',seed=42,strategy='六边形 7 点 · Baseline 1.0',
                              baseline_model='hexagon_v1',completion=completion))


def test_desktop_play_seek_step_and_render(tmp_path):
    if not DEFAULT_Q1.exists(): pytest.skip('Q1 module required')
    app=QApplication.instance() or QApplication([])
    window=Window(ScenarioConfig(),DEFAULT_Q1)
    window.show(); app.processEvents()
    window.set_run(simulate())
    window.toggle_play(); assert window.playing
    window.toggle_play(); assert not window.playing
    window.step(); assert window.t>0
    window.seek(50000); assert window.t==window.duration/2
    window.seek(100000); assert window.values['State'].text()=='mission ended'
    window.truth.setChecked(False); window.radii.setChecked(True)
    window.seek(0); assert window.values['Measure Count'].text()=='0'
    assert window.speed.count()==7
    assert window.map.width()>500
    assert window.grab().save(str(tmp_path/'ui.png'))
    window.close()


def test_start_q1_worker_automatically_plays(monkeypatch):
    if not DEFAULT_Q1.exists(): pytest.skip('Q1 module required')
    app=QApplication.instance() or QApplication([])
    window=Window(ScenarioConfig(),DEFAULT_Q1)
    window.timer.stop()
    errors=[]; monkeypatch.setattr(window,'show_error',errors.append)
    window.model.setCurrentIndex(window.model.findData('q1_demo'))
    assert not window.archive.isEnabled()
    window.new.click()
    assert not window.new.isEnabled()
    wait_for_worker(app,window)
    assert errors==[]
    assert window.run_data and window.duration>0
    assert window.playing and window.play.isEnabled()
    assert window.new.isEnabled() and not window.cancel.isEnabled()
    window.close()


def test_baseline_button_dispatches_selected_original_archive(tmp_path,monkeypatch):
    from enhanced import baseline
    app=QApplication.instance() or QApplication([])
    archive=tmp_path/'delivery.zip'; archive.write_bytes(b'UI dispatch fixture')
    calls=[]
    def run(config,archive,model,output,progress,cancelled):
        calls.append((config,archive,model,output))
        progress(dict(phase='running',action_count=2,virtual_time_s=10.))
        return sample_run('incomplete_unresolved')
    monkeypatch.setattr(baseline,'run_baseline',run)
    window=Window(ScenarioConfig(error_model='baseline_fixed_field'),DEFAULT_Q1)
    window.show(); window.timer.stop(); app.processEvents()
    errors=[]; monkeypatch.setattr(window,'show_error',errors.append)
    assert window.model.currentData()=='hexagon_v1'
    assert window.error.currentText()=='baseline_fixed_field'
    assert window.model.count()==7  # Three Q3 models, three Q4 versions, and the Q1 demo.
    window.archive.setText(str(archive))
    window.model.setCurrentIndex(window.model.findData('spiral_v1'))
    window.new.click()
    wait_for_worker(app,window)
    assert errors==[]
    assert calls[0][1]==archive and calls[0][2]=='spiral_v1'
    assert calls[0][0].error_model=='baseline_fixed_field'
    assert window.playing
    assert '未解决' in window.note.text()
    assert window.archive.width()>250 and window.map.width()>500
    assert window.grab().save(str(tmp_path/'baseline-ui.png'))
    window.close()


def test_close_cancels_baseline_worker_safely(tmp_path,monkeypatch):
    from enhanced import baseline
    app=QApplication.instance() or QApplication([])
    archive=tmp_path/'delivery.zip'; archive.write_bytes(b'UI cancellation fixture')
    started=threading.Event(); stopped=threading.Event()
    def run(config,archive,model,output,progress,cancelled):
        started.set()
        deadline=time.monotonic()+10
        while not cancelled() and time.monotonic()<deadline: time.sleep(.005)
        if cancelled(): stopped.set()
        return sample_run('cancelled')
    monkeypatch.setattr(baseline,'run_baseline',run)
    window=Window(ScenarioConfig(),DEFAULT_Q1)
    window.show(); window.archive.setText(str(archive))
    window.new.click()
    assert started.wait(2)
    window.close()
    assert window._close_when_finished
    wait_for_worker(app,window)
    assert stopped.is_set() and not window.isVisible()
    assert window.run_data is None


def test_light_chrome_overrides_dark_system_palette():
    app=QApplication.instance() or QApplication([])
    palette=app.palette()
    palette.setColor(QPalette.ColorRole.WindowText,QColor('#ffffff'))
    palette.setColor(QPalette.ColorRole.Window,QColor('#303030'))
    app.setPalette(palette)
    configure_app(app)
    assert app.palette().color(QPalette.ColorRole.WindowText).lightness()<80
    assert app.palette().color(QPalette.ColorRole.Window).lightness()>220
    assert app.palette().color(QPalette.ColorRole.Base).name()=='#ffffff'


def test_scan_labels_group_exact_positions_and_keep_full_action_tooltips():
    app=QApplication.instance() or QApplication([])
    view=MapView()
    state=project([],0)
    state['actions']=[dict(position=[0.,0.],channel=c,result='direction',svd_deg=float(c)) for c in range(1,21)]
    state['actions'].append(dict(position=[.001,0.],channel=1,result='none'))
    view.draw(state,[],False,False)
    labels=[item for item in view.scene().items() if hasattr(item,'toPlainText')]
    combined=next(item for item in labels if item.toPlainText()=='1–20')
    assert '#1 · 频道 1' in combined.toolTip()
    assert '#20 · 频道 20' in combined.toolTip()
    assert any(item.toPlainText()=='21' for item in labels)
    assert not any(item.toPlainText()=='1' for item in labels)
    view.close()


def q4_replay_with_old_queue():
    from enhanced.q4_adapter import search_points
    strategy=dict(available=True,problem=4,model='q4_opportunity_v2',phase='planning',
        route_points=[dict(index=j,position=list(point),scan_completed=False)
                      for j,point in enumerate(search_points())],
        tasks=[dict(kind='service',channel=8,position=[100.,200.])],
        pending_targets=[dict(channel=8)],opportunities=[])
    return dict(events=[dict(seq=0,type='StrategyState',start=0.,end=0.,data=strategy),
                        dict(seq=1,type='MissionEnd',start=0.,end=317.,data={})],
                sources=[],metadata=dict(problem=4,model='q4_opportunity_v2',
                    scenario='uniform',seed=22,error_model='baseline_fixed_field',
                    strategy='Q4 · 左右机会复测 · v2.0',completion='completed'))


def test_switching_model_clears_old_replay_and_shows_selected_skeleton():
    app=QApplication.instance() or QApplication([])
    window=Window(ScenarioConfig(),None)
    window.timer.stop()
    old=q4_replay_with_old_queue()
    try:
        window.set_run(old)
        window.seek(100000)
        assert len(window.map.state['route_points'])==25
        assert window.mission_panel.queue_table.rows
        assert window.mission_panel.time.text()=='317.0 s'
        window.model.setCurrentIndex(window.model.findData('hexagon_v2'))
        assert window.run_data is None and window.t==0 and not window.playing
        assert window.model.currentData()=='hexagon_v2' and window.config.problem==3
        assert len(window.map.state['route_points'])==7
        assert window.mission_panel.queue_table.rows==()
        assert window.mission_panel.iteration_table.rows==()
        assert window.mission_panel.time.text()=='0.0 s'
        assert '左右机会' not in window.mission_panel.queue_note.text()
        assert '当前回放' not in window.note.text()
        assert window.values['Virtual Time'].text()=='—'
        assert window.clock.text()=='0.0 / 0.0 s' and window.timeline.value()==0
        assert not window.play.isEnabled() and not window.export.isEnabled()
        assert not window.open_run.isEnabled() and not window.details_button.isEnabled()
        # A deliberate replay load still selects its own model and retains it.
        window.set_run(old)
        window.seek(100000)
        assert window.run_data is old and window.model.currentData()=='q4_opportunity_v2'
        assert len(window.map.state['route_points'])==25
        assert window.mission_panel.queue_table.rows and window.mission_panel.time.text()=='317.0 s'
    finally:
        window.close()


def test_starting_same_model_clears_old_results_while_worker_is_preparing(monkeypatch):
    from enhanced import q4_adapter
    app=QApplication.instance() or QApplication([])
    started,release=threading.Event(),threading.Event()
    next_run=q4_replay_with_old_queue()
    def run(config,output,progress=None,cancelled=None,model='q4_cu'):
        started.set()
        deadline=time.monotonic()+5
        while not release.is_set() and not cancelled() and time.monotonic()<deadline:
            time.sleep(.005)
        return next_run
    monkeypatch.setattr(q4_adapter,'run_q4',run)
    window=Window(ScenarioConfig(problem=4),None)
    window.timer.stop()
    try:
        window.set_run(q4_replay_with_old_queue())
        window.seek(100000)
        window.new.click()
        assert started.wait(2)
        assert window.run_data is None and window.t==0
        assert window.mission_panel.queue_table.rows==()
        assert window.mission_panel.time.text()=='0.0 s'
        assert window.mission_panel.target.text()=='正在计算本局，完成后自动播放'
        assert window.values['Virtual Time'].text()=='—'
        assert not window.play.isEnabled() and not window.export.isEnabled()
        release.set()
        wait_for_worker(app,window)
        assert window.run_data is next_run and window.playing
        assert window.mission_panel.queue_table.rows
    finally:
        release.set()
        if window.worker is not None:
            window.cancel_simulation();wait_for_worker(app,window)
        window.close()


def test_progress_distinguishes_real_wait_from_virtual_task_time():
    app=QApplication.instance() or QApplication([])
    window=Window(ScenarioConfig(),None)
    window.timer.stop()
    class Worker:
        def isInterruptionRequested(self):return False
    window.worker=Worker()
    try:
        window.show_progress(dict(phase='认证七点搜索骨架与几何',elapsed_s=35.2,
                                  phase_elapsed_s=13.6,action_count=0,virtual_time_s=0.))
        text=window.progress_label.text()
        assert '认证七点搜索骨架与几何' in text
        assert '实际用时 35.2 s' in text and '本阶段 13.6 s' in text
        assert '已执行 0 次动作' in text and '虚拟时间 0.0 s' in text
        # Q4 and older workers need not supply wall-clock heartbeat fields.
        window.show_progress(dict(phase='q4_running',action_count=7,virtual_time_s=42.))
        text=window.progress_label.text()
        assert '正在运行第四问' in text and '虚拟时间 42.0 s' in text
        assert '实际用时' not in text and '本阶段' not in text
    finally:
        window.worker=None
        window.close()
