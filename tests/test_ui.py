import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication
from enhanced.ui import Window
from enhanced.world import ScenarioConfig
from enhanced.runner import simulate
from enhanced.strategy import DEFAULT_Q1


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
