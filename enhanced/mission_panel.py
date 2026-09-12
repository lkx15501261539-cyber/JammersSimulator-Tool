"""Read-only mission, planning queue and per-channel iteration widgets."""
from html import escape
from .source_status import SOURCE_COLORS, SOURCE_LABELS, source_status, selected_source_channel
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QGroupBox,QLabel,
    QTextBrowser)

STATE_NAMES={'UNKNOWN':'未发现','FOUND':'待处理','CLEARED':'已清除',
             'UNRESOLVED':'未解决','EMPTY_CERTIFIED':'已排除'}


def coordinates(position):
    if position is None:return '待规划'
    return f'({position[0]:.1f}, {position[1]:.1f})'


def task_name(task):
    kind=task.get('kind')
    channel=task.get('channel')
    if kind=='anchor_scan':return f"固定点 P{task.get('anchor_index',0)+1} 扫描"
    label={'measure':'测向','clear':'清除','service':'优先处理'}.get(kind,kind or '任务')
    return f'CH {channel:02d} · {label}' if channel is not None else label


class ReadOnlyTable(QTextBrowser):
    """One stable text widget, without native item-view/cell selection objects.

    Qt Cocoa can retain stale selected-cell accessibility interfaces while an
    item table changes. A text document presents the same read-only information
    without entering that cell-selection lifecycle.
    """
    def __init__(self,headers):
        super().__init__()
        self.headers=tuple(headers)
        self.show_header=True
        self.rows=()
        self.active_key=None
        self._signature=None
        self.setReadOnly(True)
        self.setOpenLinks(False);self.setOpenExternalLinks(False)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse|
                                     Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(0)
        self.setStyleSheet('QTextBrowser { border:0; background:white; color:#476682; font-size:11px; }')

    def set_rows(self,rows,colors,keys=None,active=None):
        rows=tuple(tuple(str(value) for value in row) for row in rows)
        colors=tuple(colors)
        keys=tuple(str(key) for key in keys) if keys is not None else tuple(str(i) for i in range(len(rows)))
        active=None if active is None else str(active)
        signature=(rows,colors,keys,active)
        if signature==self._signature:return
        previous_active=self.active_key
        scroll=self.verticalScrollBar().value()
        self.rows,self.active_key,self._signature=rows,active,signature
        widths=(12,43,45) if len(self.headers)==3 else (16,22,18,44)
        html=['<table width="100%" cellspacing="0" cellpadding="4" style="border:0;font-size:11px;">']
        if self.show_header:
            html.append('<tr>')
            for header,width in zip(self.headers,widths):
                html.append(f'<th width="{width}%" align="left" bgcolor="#f0f4f7" style="color:#617181;">{escape(header)}</th>')
            html.append('</tr>')
        for index,row in enumerate(rows):
            selected=keys[index]==active
            background='#e5f2f5' if selected else '#f6f8fa' if index%2 else '#ffffff'
            color=escape(colors[index] or '#476682',quote=True)
            html.append('<tr>')
            for column,value in enumerate(row):
                text=escape(value)
                if column==0:text=f'<a name="row-{escape(keys[index],quote=True)}">{text}</a>'
                if selected:text=f'<b>{text}</b>'
                html.append(f'<td width="{widths[column]}%" valign="top" bgcolor="{background}" style="color:{color};">{text}</td>')
            html.append('</tr>')
        html.append('</table>')
        if not rows:html.append('<p style="color:#84919c;font-size:11px;">暂无记录</p>')
        self.setHtml(''.join(html))
        self.setAccessibleDescription((' / '.join(self.headers)+'\n' if not self.show_header else '')+self.toPlainText())
        self.verticalScrollBar().setValue(scroll)
        if active!=previous_active and active in keys:self.scrollToAnchor(f'row-{active}')


class MissionPanel(QWidget):
    def __init__(self):
        super().__init__()
        layout=QVBoxLayout(self);layout.setContentsMargins(2,0,2,0);layout.setSpacing(8)
        summary=QGroupBox('任务概览');summary_layout=QVBoxLayout(summary)
        highlights=QHBoxLayout()
        self.time=QLabel('0.0 s');self.cleared=QLabel('— / —')
        for label in (self.time,self.cleared):label.setStyleSheet('font-size:21px;font-weight:600;color:#276f82;')
        highlights.addWidget(self.time);highlights.addStretch();highlights.addWidget(QLabel('已清除'));highlights.addWidget(self.cleared)
        summary_layout.addLayout(highlights)
        self.pose=QLabel('机器狗  X 0.0 · Y 0.0 m    |    CH 01');summary_layout.addWidget(self.pose)
        layout.addWidget(summary)
        queue=QGroupBox('当前任务与执行队列');queue_layout=QVBoxLayout(queue)
        self.target=QLabel('等待开始模拟');self.target.setWordWrap(True)
        self.target.setStyleSheet('font-weight:600;color:#345473;')
        self.target.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        queue_layout.addWidget(self.target)
        self.route_progress=QLabel('固定测点 —');queue_layout.addWidget(self.route_progress)
        self.queue_table=self.make_table(['顺序','任务','目标坐标 (m)'])
        self.queue_table.setAccessibleName('执行队列')
        self.queue_table.setMinimumHeight(92);self.queue_table.setMaximumHeight(116)
        queue_layout.addWidget(self.queue_table)
        self.queue_note=QLabel('执行顺序来自原模型记录。');self.queue_note.setWordWrap(True)
        self.queue_note.setStyleSheet('font-size:11px;color:#758391;');queue_layout.addWidget(self.queue_note)
        layout.addWidget(queue)
        channels=QGroupBox('各干扰源的迭代进度');channels_layout=QVBoxLayout(channels)
        self.iteration_table=self.make_table(['频道','迭代','测量','状态'])
        self.iteration_table.show_header=False
        header=QLabel('<table width="100%" cellspacing="0" cellpadding="4"><tr>'+''.join(
            f'<td width="{width}%" bgcolor="#f0f4f7">{name}</td>' for name,width in zip(self.iteration_table.headers,(16,22,18,44)))+'</tr></table>')
        header.setStyleSheet('font-size:11px;color:#617181;');header.setFixedHeight(26)
        channels_layout.addWidget(header)
        self.iteration_table.setAccessibleName('频道迭代进度')
        self.iteration_table.setMinimumHeight(160)
        self.iteration_table.setToolTip('迭代为原模型 followups：追加测点次数。测量包含固定点扫描和追加测量。')
        channels_layout.addWidget(self.iteration_table,1)
        self.iteration_note=QLabel('迭代 = 追加测点次数；测量包含固定点扫描。')
        self.iteration_note.setWordWrap(True);self.iteration_note.setStyleSheet('font-size:11px;color:#758391;')
        channels_layout.addWidget(self.iteration_note);layout.addWidget(channels,1)

    @staticmethod
    def make_table(headers):
        return ReadOnlyTable(headers)

    def fill_table(self,table,rows,colors,keys=None,active=None):
        table.set_rows(rows,colors,keys,active)

    def update_state(self,state,sources,show_truth):
        strategy=state.get('strategy') or {}
        self.time.setText(f"{state['time']:.1f} s")
        self.cleared.setText(f"{len(state['cleared'])} / {len(sources)}")
        x,y=state['position'];self.pose.setText(f"机器狗  X {x:.1f} · Y {y:.1f} m    |    CH {state['channel']:02d}")
        target=strategy.get('current_target')
        if target:self.target.setText(task_name(target)+'\nX / Y  '+coordinates(target.get('position'))+' m')
        elif state['status']=='mission ended':self.target.setText('本局任务结束')
        elif strategy.get('available'):self.target.setText('正在更新任务计划')
        else:self.target.setText('当前日志未记录任务计划')
        points=state.get('route_points',[])
        self.route_progress.setText(f"固定点已到访 {sum(bool(p.get('visited')) for p in points)} / {len(points)}" if points else '固定点信息未记录')
        rows=[]
        for i,task in enumerate(strategy.get('tasks',[]),1):
            rows.append([str(i),task_name(task),coordinates(task.get('position'))])
        self.fill_table(self.queue_table,rows,['#476682']*len(rows))
        if not strategy.get('available'):
            self.queue_note.setText('旧日志缺少队列记录；重新开始模拟即可记录。')
        else:
            pending=strategy.get('pending_targets',[])
            pending_channels=[p.get('channel') if isinstance(p,dict) else p for p in pending]
            pending_text='、'.join(f'CH {c:02d}' for c in pending_channels if c is not None)
            kind=strategy.get('queue_kind')
            explanation='尾扫按原模型发现顺序执行。' if kind=='tail' else '每段最多优先处理 1 个目标，其余固定点保持顺序。'
            self.queue_note.setText(explanation+ ('\n待调度：'+pending_text if pending_text else ''))
        info=strategy.get('channel_iterations',{})
        channels=sorted(s['channel'] for s in sources) if show_truth else sorted(state['detected'])
        rows=[];colors=[]
        for channel in channels:
            record=info.get(str(channel),info.get(channel,{}))
            count=sum(a.get('channel')==channel for a in state['actions'] if 'svd_deg' in a)
            count=record.get('measurements',count)
            visual_status=source_status(state,channel)
            status=SOURCE_LABELS[visual_status]
            if visual_status not in ('target','cleared') and record.get('state') in ('UNRESOLVED','EMPTY_CERTIFIED'):
                status=STATE_NAMES[record['state']]
            followups=record.get('followups')
            iteration='—' if followups is None else f"{followups} / {record.get('limit',5)}"
            rows.append([f'{channel:02d}',iteration,str(count),status]);colors.append(SOURCE_COLORS[visual_status])
        active=selected_source_channel(state)
        self.fill_table(self.iteration_table,rows,colors,keys=channels,active=active)
        self.iteration_note.setText('迭代 = 原模型追加测点次数。\n'+('含真值源频道；关闭真值后仅列已发现源。' if show_truth else '仅列已发现源；未发现源不提前显示。'))
