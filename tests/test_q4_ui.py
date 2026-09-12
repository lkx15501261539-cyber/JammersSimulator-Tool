"""Q4 desktop/CLI controls must dispatch the Q4 controller, including after replay."""
import os
import sys
import threading
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest

pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication
from enhanced import baseline, q4_adapter, ui
from enhanced.world import ScenarioConfig


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def sample_run(**metadata):
    data = dict(problem=4, model='q4_cu', strategy='Q4 · 25 点 C/U',
                scenario='uniform', seed=47, error_model='worst_edge', completion='completed')
    data.update(metadata)
    return dict(events=[dict(seq=0, type='MissionEnd', start=0., end=1., data={})],
                sources=[], metadata=data)


def wait_worker(app, window):
    deadline = time.monotonic() + 10
    while window.worker is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert window.worker is None, 'Q4 worker did not finish'


def test_problem4_selects_controller_and_25_point_preview(app):
    window = ui.Window(ScenarioConfig(problem=4), None)
    window.timer.stop()
    try:
        assert window.model.currentData() == 'q4_cu'
        assert window.model.currentText() == q4_adapter.Q4_MODEL_LABELS['q4_cu']
        assert not window.archive.isEnabled() and not window.browse_archive.isEnabled()
        assert all(w.isHidden() for w in (window.archive_label, window.archive, window.browse_archive))
        assert not window.current_model_label.isHidden()
        assert window.current_model_label.text() == '当前模型：'+q4_adapter.Q4_MODEL_LABELS['q4_cu']
        assert '第四问' in window.windowTitle()
        assert 'no_signal' in window.note.text() and '覆盖 F' in window.note.text()
        points = window.map.state['route_points']
        assert len(points) == 25
        assert [p['position'] for p in points] == [list(p) for p in q4_adapter.search_points()]
    finally:
        window.close()


def test_q4_start_without_zip_runs_adapter_and_autoplays(app, monkeypatch, tmp_path):
    calls = []
    def run(config, output, progress=None, cancelled=None, model='q4_cu'):
        calls.append(config)
        assert callable(progress) and callable(cancelled)
        progress(dict(phase='running', action_count=4, virtual_time_s=20.))
        return sample_run()
    monkeypatch.setattr(q4_adapter, 'run_q4', run)
    monkeypatch.setattr(baseline, 'run_baseline', lambda *a, **k: pytest.fail('Q4 entered Q3 baseline'))
    window = ui.Window(ScenarioConfig(problem=4), None)
    window.timer.stop()
    window.archive.setText(str(tmp_path/'does-not-exist.zip'))
    errors = []
    monkeypatch.setattr(window, 'show_error', errors.append)
    try:
        window.new.click()
        wait_worker(app, window)
        assert errors == [] and len(calls) == 1
        assert calls[0].problem == 4
        assert window.playing and window.run_data['metadata']['model'] == 'q4_cu'
        assert not window.archive.isEnabled()
    finally:
        window.close()


@pytest.mark.parametrize('metadata', [dict(problem=4, model=None), dict(problem=None, model='q4_cu')])
def test_q4_replay_selects_q4_for_next_start(app, metadata):
    window = ui.Window(ScenarioConfig(), None)
    window.timer.stop()
    try:
        assert window.model.currentData() == 'hexagon_v1'
        window.set_run(sample_run(**metadata))
        assert window.model.currentData() == 'q4_cu'
        assert window.config.problem == 4
        assert '第四问' in window.windowTitle()
        assert '当前回放：第四问 Q4' in window.note.text()
        assert not window.archive.isEnabled()
        assert all(w.isHidden() for w in (window.archive_label, window.archive, window.browse_archive))
        assert window.current_model_label.text() == '当前模型：'+q4_adapter.Q4_MODEL_LABELS['q4_cu']
    finally:
        window.close()


def test_switching_back_to_q3_restores_zip_and_problem3(app, monkeypatch, tmp_path):
    calls = []
    def run(config, archive, model, output, progress=None, cancelled=None):
        calls.append((config.problem, model))
        return sample_run(problem=3, model='spiral_v1', baseline_model='spiral_v1', strategy='Q3 spiral')
    monkeypatch.setattr(baseline, 'run_baseline', run)
    monkeypatch.setattr(q4_adapter, 'run_q4', lambda *a, **k: pytest.fail('Q3 entered Q4 adapter'))
    archive = tmp_path/'model.zip'
    archive.write_bytes(b'dispatch fixture')
    window = ui.Window(ScenarioConfig(problem=4), None)
    window.timer.stop()
    try:
        window.archive.setText(str(archive))
        window.model.setCurrentIndex(window.model.findData('spiral_v1'))
        assert window.config.problem == 3 and window.archive.isEnabled()
        assert window.browse_archive.isEnabled() and '第三问' in window.windowTitle()
        assert all(not w.isHidden() for w in (window.archive_label, window.archive, window.browse_archive))
        assert window.archive.text() == str(archive)
        assert window.current_model_label.isHidden()
        window.new.click()
        wait_worker(app, window)
        assert calls == [(3, 'spiral_v1')]
    finally:
        window.close()


def test_q1_hides_zip_and_q3_restores_saved_path(app):
    window = ui.Window(ScenarioConfig(), None)
    window.timer.stop()
    original_path = window.archive.text()
    try:
        window.model.setCurrentIndex(window.model.findData('q1_demo'))
        assert all(w.isHidden() for w in (window.archive_label, window.archive, window.browse_archive))
        assert window.current_model_label.text() == '当前模型：Q1 单目标验证'
        window.model.setCurrentIndex(window.model.findData('hexagon_v1'))
        assert all(not w.isHidden() for w in (window.archive_label, window.archive, window.browse_archive))
        assert window.archive.text() == original_path
        assert window.current_model_label.isHidden()
    finally:
        window.close()


def test_q4_worker_cancellation_reaches_adapter(app, monkeypatch):
    started, stopped = threading.Event(), threading.Event()
    def run(config, output, progress=None, cancelled=None, model='q4_cu'):
        started.set()
        deadline = time.monotonic() + 5
        while not cancelled() and time.monotonic() < deadline:
            time.sleep(.005)
        if cancelled():
            stopped.set()
        return sample_run(completion='cancelled')
    monkeypatch.setattr(q4_adapter, 'run_q4', run)
    window = ui.Window(ScenarioConfig(problem=4), None)
    window.timer.stop()
    try:
        window.new.click()
        assert started.wait(2)
        window.cancel.click()
        wait_worker(app, window)
        assert stopped.is_set() and window.run_data is None
        assert window.new.isEnabled() and not window.cancel.isEnabled()
    finally:
        window.close()


@pytest.mark.parametrize('argv, problem, model', [
    (['gui'], 3, 'hexagon_v1'),
    (['gui', '--problem', '4'], 4, 'q4_cu'),
    (['gui', '--model', 'q4_cu'], 4, 'q4_cu'),
    (['gui', '--model', 'q4_opportunity_v2'], 4, 'q4_opportunity_v2'),
])
def test_gui_cli_selects_requested_problem(monkeypatch, argv, problem, model):
    from enhanced.__main__ import main
    calls = []
    monkeypatch.setattr(ui, 'launch', lambda config, q1, replay, selected, archive: calls.append((config, selected)))
    monkeypatch.setattr(sys, 'argv', ['enhanced', *argv])
    main()
    assert len(calls) == 1 and calls[0][0].problem == problem and calls[0][1] == model


@pytest.mark.parametrize('argv', [['q4'], ['baseline', '--model', 'q4_cu'], ['baseline', '--problem', '4']])
def test_q4_cli_dispatches_adapter(monkeypatch, tmp_path, argv):
    from enhanced.__main__ import main
    calls = []
    def run(config, output, progress=None, model='q4_cu'):
        calls.append((config, output))
        return sample_run()
    monkeypatch.setattr(q4_adapter, 'run_q4', run)
    monkeypatch.setattr(baseline, 'run_baseline', lambda *a, **k: pytest.fail('Q4 CLI entered Q3 baseline'))
    monkeypatch.setattr(sys, 'argv', ['enhanced', *argv, '--output', str(tmp_path/'q4-run')])
    main()
    assert len(calls) == 1 and calls[0][0].problem == 4
    assert calls[0][1] == tmp_path/'q4-run'


def test_conflicting_q4_and_q3_model_rejected(monkeypatch):
    from enhanced.__main__ import main
    monkeypatch.setattr(sys, 'argv', ['enhanced', 'gui', '--problem', '4', '--model', 'spiral_v1'])
    with pytest.raises(SystemExit, match='2'):
        main()


def test_unified_window_switches_all_five_models(app):
    window = ui.Window(ScenarioConfig(), None)
    window.timer.stop()
    try:
        for model in (*baseline.MODEL_LABELS, *q4_adapter.Q4_MODEL_LABELS):
            index=window.model.findData(model)
            assert index >= 0
            window.model.setCurrentIndex(index)
            assert window.model.currentData() == model
            assert window.config.problem == (4 if model in q4_adapter.Q4_MODEL_LABELS else 3)
            if model in q4_adapter.Q4_MODEL_LABELS:
                assert window.archive.isHidden() and not window.archive.isEnabled()
                assert len(window.map.state['route_points']) == 25
            if model == 'q4_opportunity_v2':
                assert window.model.currentText() == '第四问 · 左右机会复测 · v2.0'
                assert '左右机会复测' in window.windowTitle()
                assert '追加 3 次' in window.note.text()
    finally:
        window.close()


@pytest.mark.parametrize('model', list(q4_adapter.Q4_MODEL_LABELS))
def test_q4_replay_preserves_version_and_dispatches_next_run(app,monkeypatch,tmp_path,model):
    calls=[]
    def run(config,output,progress=None,cancelled=None,model='q4_cu'):
        calls.append((config.problem,model))
        return sample_run(model=model)
    monkeypatch.setattr(q4_adapter,'run_q4',run)
    window=ui.Window(ScenarioConfig(),None)
    window.timer.stop()
    monkeypatch.setattr(window,'show_error',lambda text:pytest.fail(text))
    try:
        window.set_run(sample_run(model=model))
        assert window.model.currentData() == model
        assert q4_adapter.Q4_MODEL_LABELS[model].replace('第四问','第四问 Q4',1) in window.note.text()
        window.new.click()
        wait_worker(app,window)
        assert calls == [(4,model)]
        assert window.run_data['metadata']['model'] == model
        assert window.model.currentData() == model and window.playing
    finally:
        window.close()


def test_q4_v2_cancellation_preserves_gui_state(app,monkeypatch):
    started=threading.Event()
    models=[]
    def run(config,output,progress=None,cancelled=None,model='q4_cu'):
        models.append(model)
        started.set()
        deadline=time.monotonic()+5
        while not cancelled() and time.monotonic()<deadline:
            time.sleep(.005)
        assert cancelled()
        return sample_run(model=model,completion='cancelled')
    monkeypatch.setattr(q4_adapter,'run_q4',run)
    window=ui.Window(ScenarioConfig(problem=4),None)
    window.timer.stop()
    try:
        window.model.setCurrentIndex(window.model.findData('q4_opportunity_v2'))
        window.new.click()
        assert started.wait(2)
        window.cancel.click()
        wait_worker(app,window)
        assert models == ['q4_opportunity_v2']
        assert window.run_data is None and window.new.isEnabled()
        assert window.model.currentData() == 'q4_opportunity_v2'
    finally:
        window.close()


@pytest.mark.parametrize('mode',['q4','baseline'])
def test_q4_v2_cli_dispatches_selected_version(monkeypatch,tmp_path,mode):
    from enhanced.__main__ import main
    calls=[]
    def run(config,output,progress=None,model='q4_cu'):
        calls.append((config.problem,model,output))
        return sample_run(model=model)
    monkeypatch.setattr(q4_adapter,'run_q4',run)
    monkeypatch.setattr(baseline,'run_baseline',lambda *a,**k:pytest.fail('Q4 v2 entered Q3'))
    monkeypatch.setattr(sys,'argv',['enhanced',mode,'--model','q4_opportunity_v2','--output',str(tmp_path/'v2')])
    main()
    assert calls == [(4,'q4_opportunity_v2',tmp_path/'v2')]


def test_q4_v2_replay_explains_maximum16_early_completion(app):
    window=ui.Window(ScenarioConfig(problem=4),None)
    window.timer.stop()
    try:
        window.set_run(sample_run(model='q4_opportunity_v2',completion_reason='maximum_16_cleared'))
        assert '已清除 16 个频道' in window.note.text()
        assert '剩余存在性扫描无需继续' in window.note.text()
    finally:
        window.close()
