"""The v2 selection must load its own package and expose its actual rules."""
import json
import os
import sys
import time
import zipfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication
from enhanced import baseline, ui
from enhanced.replay import project
from enhanced.world import ScenarioConfig


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def replay():
    return dict(events=[dict(seq=0,type='MissionEnd',start=0.,end=1.,data={})],sources=[],
                metadata=dict(problem=3,scenario='uniform',seed=42,
                              strategy='七点六边形 · Baseline 2.0',baseline_model='hexagon_v2',
                              strategy_version='baseline-v2.0',completion='completed'))


def test_version_switches_keep_separate_archive_paths_and_correct_preview(app, monkeypatch, tmp_path):
    v1, v2 = tmp_path/'v1.zip', tmp_path/'v2.zip'
    points = [[0,0],[1200,0],[600,1039.23],[-600,1039.23],[-1200,0],[-600,-1039.23],[600,-1039.23]]
    with zipfile.ZipFile(v2,'w') as archive:
        archive.writestr('Baseline_v2.0_七点六边形/config.json',json.dumps(dict(points=points)))
    monkeypatch.setattr(ui,'default_archive',lambda model='hexagon_v1': v2 if model=='hexagon_v2' else v1)
    window = ui.Window(ScenarioConfig(),None)
    window.timer.stop()
    try:
        custom_v1 = str(tmp_path/'custom-v1.zip')
        window.archive.setText(custom_v1)
        window.model.setCurrentIndex(window.model.findData('hexagon_v2'))
        assert window.archive.text() == str(v2)
        assert '2.0' in window.windowTitle() and window.config.problem == 3
        assert '1200 m' in window.note.text() and '3 次' in window.note.text()
        assert [point['position'] for point in window.map.state['route_points']] == points
        assert window.archive.isEnabled() and not window.archive.isHidden()
        window.model.setCurrentIndex(window.model.findData('q4_cu'))
        assert window.archive.isHidden()
        window.model.setCurrentIndex(window.model.findData('spiral_v1'))
        assert window.archive.text() == custom_v1
        window.model.setCurrentIndex(window.model.findData('hexagon_v2'))
        assert window.archive.text() == str(v2)
    finally:
        window.close()


def test_v2_start_dispatches_exact_package_and_autoplays(app, monkeypatch, tmp_path):
    archive = tmp_path/'v2.zip'; archive.write_bytes(b'dispatch fixture')
    calls = []
    def run(config,path,model,output,progress=None,cancelled=None):
        calls.append((config.problem,path,model))
        return replay()
    monkeypatch.setattr(baseline,'run_baseline',run)
    window = ui.Window(ScenarioConfig(),None)
    window.timer.stop()
    errors = []
    monkeypatch.setattr(window,'show_error',errors.append)
    try:
        window.model.setCurrentIndex(window.model.findData('hexagon_v2'))
        window.archive.setText(str(archive))
        window.new.click()
        deadline=time.monotonic()+10
        while window.worker is not None and time.monotonic()<deadline:
            app.processEvents();time.sleep(.005)
        assert window.worker is None and not errors
        assert calls == [(3,archive,'hexagon_v2')]
        assert window.playing and window.model.currentData()=='hexagon_v2'
        assert '2.0' in window.windowTitle()
    finally:
        window.close()


def test_v2_replay_selects_v2_and_displays_three_followups(app):
    window=ui.Window(ScenarioConfig(problem=4),None)
    window.timer.stop()
    try:
        window.set_run(replay())
        assert window.model.currentData()=='hexagon_v2'
        assert window.archive.isEnabled() and '2.0' in window.note.text()
        state=project([],20.)
        state['detected']={8}
        state['strategy']=dict(available=True,model='hexagon_v2',queue_kind='cu_tasks',
                               cu_decision=dict(channel=8,decision='optical',remaining=1,bound_s=124.),
                               channel_iterations={'8':dict(followups=2,measurements=3,state='FOUND')})
        window.mission_panel.update_state(state,[dict(channel=8)],True)
        assert window.mission_panel.iteration_table.rows[0][1]=='2 / 3'
        assert '2-opt' in window.mission_panel.queue_note.text()
        assert '每段最多' not in window.mission_panel.queue_note.text()
        assert '上限 3 次' in window.mission_panel.iteration_note.text()
        assert '认证光学收尾 U' in window.mission_panel.q4_cost.text()
        assert '124.0 s' in window.mission_panel.q4_cost.text()
    finally:
        window.close()


def test_v2_cli_uses_v2_default_archive(monkeypatch,tmp_path):
    from enhanced.__main__ import main
    calls=[]
    archive=tmp_path/'v2.zip'
    monkeypatch.setattr(baseline,'default_archive',lambda model: archive if model=='hexagon_v2' else pytest.fail('Wrong archive'))
    monkeypatch.setattr(baseline,'run_baseline',lambda config,path,model,out,progress: calls.append((config.problem,path,model)) or replay())
    monkeypatch.setattr(sys,'argv',['enhanced','baseline','--model','hexagon_v2'])
    main()
    assert calls==[(3,archive,'hexagon_v2')]


def test_v2_cannot_be_selected_for_directional_problem4(monkeypatch):
    from enhanced.__main__ import main
    monkeypatch.setattr(sys,'argv',['enhanced','gui','--problem','4','--model','hexagon_v2'])
    with pytest.raises(SystemExit,match='2'):
        main()
