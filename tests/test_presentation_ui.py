"""Presentation controls consume saved runs without changing model results."""
import copy
import csv
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest

pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication, QFileDialog, QPushButton

from enhanced import baseline, exports, runner, ui
from enhanced.replay import load_run, metrics, project
from enhanced.world import ScenarioConfig


@pytest.fixture
def saved_run():
    directory = Path(__file__).resolve().parents[1]/'examples'/'enhanced-demo'
    run = load_run(directory)
    run['directory'] = str(directory)
    return run


@pytest.fixture(autouse=True)
def prohibit_model_rerun(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('A presentation control attempted to run the model again')
    monkeypatch.setattr(baseline, 'run_baseline', forbidden)
    monkeypatch.setattr(runner, 'simulate', forbidden)
    monkeypatch.setattr(ui.SimulationWorker, 'start', forbidden)


@pytest.fixture
def window(saved_run):
    app = QApplication.instance() or QApplication([])
    ui.configure_app(app)
    window = ui.Window(ScenarioConfig(), None)
    window.timer.stop()
    window.set_run(saved_run)
    window.show()
    app.processEvents()
    yield window
    # Tests below use only a synchronous worker double, never a real thread.
    window.worker = None
    window.close()
    window.deleteLater()
    app.processEvents()


def test_view_toggles_preserve_time_observer_state_and_run_data(window):
    window.seek(53000)
    before = copy.deepcopy(window.run_data)
    time_before = window.t
    state_before = copy.deepcopy(window.map.state)
    metrics_before = {key: value.text() for key, value in window.values.items()}
    bindings = [
        (window.truth, 'truth'),
        (window.radii, 'radii'),
        (window.follow, 'follow_robot'),
        (window.detail, 'show_closeup'),
        (window.labels, 'show_annotations'),
    ]
    for checkbox, attribute in bindings:
        for enabled in (True, False, True):
            checkbox.setChecked(enabled)
            assert getattr(window.map, attribute) is enabled
            assert window.t == time_before
            assert window.map.state == state_before
            assert window.run_data == before
            assert {key: value.text() for key, value in window.values.items()} == metrics_before
    window.slow.setChecked(False)
    window.speed.setCurrentText('50x')
    window.slow.setChecked(True)
    assert project(window.run_data['events'], window.t) == state_before
    assert window.run_data == before


def test_csv_export_matches_saved_replay_metrics_without_rerunning(saved_run, tmp_path):
    original = copy.deepcopy(saved_run)
    expected = metrics(saved_run['events'], len(saved_run['sources']))
    expected.update({f'{key}_time_s': value for key, value in expected.pop('time_breakdown').items()})
    path = tmp_path/'任务统计.csv'
    exported = exports.write_metrics_csv(saved_run, path)
    with path.open(encoding='utf-8-sig', newline='') as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 1
    for key, value in expected.items():
        assert exported[key] == value
        if value is None:
            assert rows[0][key] == ''
        else:
            assert float(rows[0][key]) == pytest.approx(value)
    for key in ('seed', 'scenario', 'error_model', 'strategy', 'strategy_version', 'completion'):
        value = saved_run['metadata'].get(key)
        assert rows[0][key] == ('' if value is None else str(value))
    assert saved_run == original


def test_export_button_uses_selected_path_and_existing_run(window, tmp_path, monkeypatch):
    window.seek(27000)
    before = copy.deepcopy(window.run_data)
    time_before = window.t
    path = tmp_path/'selected-metrics.csv'
    dialogs = []
    writes = []
    original_export = exports.write_metrics_csv
    def choose_path(*args, **kwargs):
        dialogs.append(args)
        return str(path), 'CSV (*.csv)'
    def record_export(run, target):
        writes.append((run is window.run_data, target))
        return original_export(run, target)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', choose_path)
    monkeypatch.setattr(exports, 'write_metrics_csv', record_export)
    assert window.export.isEnabled()
    window.export.click()
    assert len(dialogs) == 1 and writes == [(True, str(path))]
    assert path.is_file()
    assert str(path) in window.statusBar().currentMessage()
    assert window.run_data == before and window.t == time_before


def test_cancelled_export_dialog_does_not_write_or_change_playback(window, monkeypatch):
    before = copy.deepcopy(window.run_data)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: ('', ''))
    def forbidden_write(*args, **kwargs):
        pytest.fail('Cancelling the file dialog must not export anything')
    monkeypatch.setattr(exports, 'write_metrics_csv', forbidden_write)
    window.export.click()
    assert window.run_data == before
    assert not window.playing


def test_global_camera_button_clears_follow_without_seeking(window):
    window.seek(41000)
    time_before = window.t
    state_before = copy.deepcopy(window.map.state)
    window.follow.setChecked(True)
    assert window.map.follow_robot
    follow_scale = abs(window.map.transform().m11())
    reset = next(button for button in window.findChildren(QPushButton) if button.text() == '全域视角')
    reset.click()
    assert not window.follow.isChecked() and not window.map.follow_robot
    assert abs(window.map.transform().m11()) < follow_scale
    assert window.t == time_before and window.map.state == state_before


def test_completed_signal_waits_for_worker_finished_before_autoplay(window, saved_run):
    class FinishingWorker:
        deleted = False
        def isRunning(self): return True
        def deleteLater(self): self.deleted = True
    worker = FinishingWorker()
    window.worker = worker
    window.set_busy(True)
    window.simulation_completed(copy.deepcopy(saved_run))
    assert not window.playing
    assert not window.play.isEnabled()
    # A completed signal can arrive before QThread reports that it has stopped.
    window.toggle_play()
    assert not window.playing
    window.worker_finished()
    assert window.worker is None and worker.deleted
    assert window.playing and window.play.isEnabled() and window.export.isEnabled()
    assert window.t == 0.
    window.toggle_play()
    assert not window.playing


def test_log_button_opens_saved_directory_without_replaying(window, monkeypatch):
    opened = []
    monkeypatch.setattr(ui.QDesktopServices, 'openUrl', lambda url: opened.append(url.toLocalFile()) or True)
    before = copy.deepcopy(window.run_data)
    assert window.open_run.isEnabled()
    window.open_run.click()
    assert opened == [window.run_data['directory']]
    assert window.run_data == before and not window.playing


@pytest.mark.parametrize('error_model', ['baseline_fixed_field', 'deterministic_hash_fixed', 'worst_edge'])
def test_gui_preserves_explicit_error_model_configuration(error_model):
    app = QApplication.instance() or QApplication([])
    window = ui.Window(ScenarioConfig(error_model=error_model), None)
    window.timer.stop()
    try:
        assert window.error.currentText() == error_model
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_constructor_replay_can_open_its_log_directory(saved_run, monkeypatch):
    app = QApplication.instance() or QApplication([])
    directory = Path(saved_run['directory'])
    window = ui.Window(ScenarioConfig(), None, replay=directory)
    window.timer.stop()
    opened = []
    monkeypatch.setattr(ui.QDesktopServices, 'openUrl', lambda url: opened.append(url.toLocalFile()) or True)
    try:
        assert window.run_data['directory'] == str(directory.resolve())
        assert window.open_run.isEnabled()
        window.open_run.click()
        assert opened == [str(directory.resolve())]
        assert window.error.currentText() == saved_run['metadata']['error_model']
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
