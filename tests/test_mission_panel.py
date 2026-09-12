"""User-facing queue and iteration readouts must reflect recorded strategy state."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
pytest.importorskip('PySide6')
from PySide6.QtGui import QAccessible,QFont
from PySide6.QtWidgets import QApplication,QAbstractItemView,QTextBrowser
from enhanced.mission_panel import MissionPanel
from enhanced.replay import project


@pytest.fixture
def panel():
    app=QApplication.instance() or QApplication([])
    panel=MissionPanel()
    panel.resize(440,760);panel.show();app.processEvents()
    yield panel
    panel.close();panel.deleteLater();app.processEvents()


def state_with_plan():
    s=project([],119.)
    s['detected']={8};s['position']=[120.,-20.]
    s['route_points']=[dict(index=0,position=[0.,0.],visited=True),dict(index=1,position=[1125.255,0.],visited=False)]
    s['strategy']=dict(available=True,current_target=dict(kind='measure',channel=8,position=[984.468,-166.483],role='selected_service'),
        tasks=[dict(kind='service',channel=8,position=[984.468,-166.483]),dict(kind='anchor_scan',anchor_index=1,position=[1125.255,0.]),dict(kind='service',channel=4,position=None)],
        pending_targets=[dict(channel=4)],queue_kind='route',
        channel_iterations={'8':dict(followups=2,limit=5,measurements=4,state='FOUND'),'4':dict(followups=0,limit=5,measurements=1,state='FOUND')})
    return s


def test_current_target_queue_and_unknown_coordinates_are_explicit(panel):
    s=state_with_plan();panel.update_state(s,[dict(channel=8),dict(channel=4)],True)
    assert '984.5, -166.5' in panel.target.text()
    assert panel.queue_table.rows[0][1]=='CH 08 · 优先处理'
    assert panel.queue_table.rows[1][1]=='固定点 P2 扫描'
    assert panel.queue_table.rows[2][2]=='待规划'
    assert '固定点 P2 扫描' in panel.queue_table.toPlainText()
    assert panel.queue_table.accessibleDescription()==panel.queue_table.toPlainText()
    assert '1 / 2' in panel.route_progress.text()
    assert '待调度：CH 04' in panel.queue_note.text()


def test_iterations_use_original_followup_count_not_measurement_count(panel):
    s=state_with_plan();panel.update_state(s,[dict(channel=8)],True)
    assert panel.iteration_table.rows[0][1]=='2 / 5'
    assert panel.iteration_table.rows[0][2]=='4'
    assert panel.iteration_table.rows[0][3]=='当前目标'
    before=panel.iteration_table.document().find('当前目标').charFormat().foreground().color()
    s['cleared']={8};s['strategy']['channel_iterations']['8']['state']='CLEARED'
    panel.update_state(s,[dict(channel=8)],True)
    assert panel.iteration_table.rows[0][3]=='已清除'
    assert panel.iteration_table.document().find('已清除').charFormat().foreground().color()!=before


def test_hidden_truth_does_not_list_undetected_source_channels(panel):
    s=state_with_plan();panel.update_state(s,[dict(channel=8),dict(channel=19)],False)
    assert len(panel.iteration_table.rows)==1
    assert panel.iteration_table.rows[0][0]=='08'
    assert '19' not in panel.iteration_table.toPlainText()


def test_old_log_does_not_invent_iteration_counts_or_queue(panel):
    s=project([],0);panel.update_state(s,[dict(channel=8)],True)
    assert panel.iteration_table.rows[0][1]=='—'
    assert panel.queue_table.rows==()
    assert '旧日志' in panel.queue_note.text()


def test_readouts_avoid_native_item_view_and_cell_selection_accessibility(panel):
    panel.update_state(state_with_plan(),[dict(channel=8)],True)
    assert not panel.findChildren(QAbstractItemView)
    for table in (panel.queue_table,panel.iteration_table):
        assert isinstance(table,QTextBrowser) and table.isReadOnly()
        assert not table.openLinks() and not table.openExternalLinks()
        accessible=QAccessible.queryAccessibleInterface(table)
        assert accessible is not None
        assert accessible.interface_cast(QAccessible.InterfaceType.TableInterface) is None
        assert accessible.selectionInterface() is None


def test_unchanged_rows_keep_text_document_and_revision_stable(panel):
    state=state_with_plan();sources=[dict(channel=8)]
    panel.update_state(state,sources,True)
    document=panel.iteration_table.document()
    revision=document.revision()
    for second in range(120,150):
        state['time']=second
        panel.update_state(state,sources,True)
    assert panel.iteration_table.document() is document
    assert document.revision()==revision
    assert panel.iteration_table.active_key=='8'
    assert document.find('08').charFormat().fontWeight()>=QFont.Weight.Bold


def test_changed_rows_preserve_scroll_and_new_target_scrolls_into_view(panel):
    table=panel.iteration_table
    table.setFixedHeight(120)
    rows=[[f'{channel:02d}','0 / 5','0','未发现'] for channel in range(1,21)]
    keys=list(range(1,21));colors=['#b66b24']*20
    table.set_rows(rows,colors,keys=keys,active=1)
    QApplication.processEvents()
    assert table.verticalScrollBar().maximum()>80
    table.verticalScrollBar().setValue(40)
    rows[0][2]='1';table.set_rows(rows,colors,keys=keys,active=1)
    assert table.verticalScrollBar().value()==40
    table.set_rows(rows,colors,keys=keys,active=20)
    assert table.verticalScrollBar().value()>40
    assert table.active_key=='20'


def test_recorded_text_is_escaped_in_rich_text_readout(panel):
    value='<img src="https://invalid.example/not-loaded">'
    panel.queue_table.set_rows([['1',value,'待规划']],['#476682'])
    assert value in panel.queue_table.toPlainText()
    assert '<img ' not in panel.queue_table.toHtml()
