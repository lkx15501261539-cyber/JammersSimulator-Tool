"""Qt observer: renders immutable run data; never supplies truth to a strategy."""
from dataclasses import replace
from datetime import datetime
import math
from pathlib import Path
import time
from PySide6.QtCore import Qt, QTimer, QThread, Signal, QPointF
from PySide6.QtGui import QColor, QPen, QBrush, QPainter, QPainterPath, QPolygonF, QPalette
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGraphicsView, QGraphicsScene, QPushButton, QComboBox, QSpinBox, QLabel, QSlider,
    QCheckBox, QFileDialog, QMessageBox, QSplitter, QFormLayout, QGroupBox, QScrollArea,
    QLineEdit, QProgressBar)
from .world import ScenarioConfig, SCENARIOS
from .replay import project, load_run


def pen(color, width=1):
    p = QPen(QColor(color), width)
    p.setCosmetic(True)
    return p


def action_range(indices):
    """Compact consecutive sequence numbers without merging nearby positions."""
    ranges=[]
    first=last=indices[0]
    for index in indices[1:]:
        if index==last+1:
            last=index
        else:
            ranges.append(str(first) if first==last else f'{first}–{last}')
            first=last=index
    ranges.append(str(first) if first==last else f'{first}–{last}')
    text=', '.join(ranges)
    return text if len(text)<=28 else f'{indices[0]}…{indices[-1]} ({len(indices)} 次)'


class MapView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(QColor('#101c29'))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setSceneRect(-2000, -2000, 4000, 4000)
        self.fitted = False
    def showEvent(self, e):
        super().showEvent(e)
        if not self.fitted:
            self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
            self.fitted = True
    def wheelEvent(self, e):
        factor = 1.15 if e.angleDelta().y() > 0 else 1/1.15
        self.scale(factor, factor)
    def reset_view(self):
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
    def draw(self, state, sources, truth, radii):
        scene = self.scene()
        scene.clear()
        def line(a, b, color, width=1):
            return scene.addLine(a[0], -a[1], b[0], -b[1], pen(color, width))
        def circle(p, r, color, fill=None, width=1):
            return scene.addEllipse(p[0]-r, -p[1]-r, 2*r, 2*r, pen(color, width),
                                    QBrush(QColor(fill)) if fill else QBrush(Qt.BrushStyle.NoBrush))
        def label(p, text, color='#9bb0c3', offset=(0,0)):
            item = scene.addText(text)
            item.setDefaultTextColor(QColor(color))
            item.setFlag(item.GraphicsItemFlag.ItemIgnoresTransformations)
            units_per_pixel=1/max(abs(self.transform().m11()),1e-6)
            item.setPos(p[0]+offset[0]*units_per_pixel,-p[1]+offset[1]*units_per_pixel)
            return item
        for v in range(-1500, 1501, 500):
            line((v,-1800),(v,1800),'#213348')
            line((-1800,v),(1800,v),'#213348')
        circle((0,0),1800,'#7892ab',width=2)
        line((-70,0),(70,0),'#b4c8d9',2)
        line((0,-70),(0,70),'#b4c8d9',2)
        label((35,35),'O · 0,0')
        label((-1700,1750),'ARENA R = 1800 m')
        label((1450,-1750),'E →   N ↑')
        for source in sources:
            p = source['x'], source['y']
            color = '#526875' if source['channel'] in state['cleared'] else '#eaaf68'
            if radii:
                circle(p,source['recv_radius'],'#39463e')
            if truth:
                circle(p,22,color,color)
                label(p,f"G{source['channel']}"+(' ✓' if source['channel'] in state['cleared'] else ''),color)
        path = QPainterPath(QPointF(0,0))
        for x,y in state['trajectory']:
            path.lineTo(x,-y)
        scene.addPath(path,pen('#51c6df',2))
        channel = state['channel']
        for d in state['measurements']:
            if d['channel'] != channel: continue
            x,y = d['position']
            angle = math.radians(d['svd_deg'])
            rays = [(x+4000*math.cos(angle+sign*math.pi/180),y+4000*math.sin(angle+sign*math.pi/180)) for sign in (-1,1)]
            polygon = QPolygonF([QPointF(x,-y),*(QPointF(a,-b) for a,b in rays)])
            scene.addPolygon(polygon,pen('#576045'),QBrush(QColor(205,195,87,22)))
            line((x,y),(x+4000*math.cos(angle),y+4000*math.sin(angle)),'#aa9d58')
        loc = state['localizations'].get(channel)
        if loc:
            vertices = loc['vertices']
            if len(vertices) >= 3:
                scene.addPolygon(QPolygonF([QPointF(x,-y) for x,y in vertices]),pen('#b29bf3',2),QBrush(QColor(170,139,238,65)))
            elif len(vertices) == 2:
                line(*vertices,'#b29bf3',3)
            elif vertices:
                circle(vertices[0],7,'#b29bf3','#b29bf3')
            if loc['farthest_pair']:
                line(*loc['farthest_pair'],'#e4c9ff',3)
            if loc['center'] is not None and loc['radius'] is not None:
                circle(loc['center'], max(.1,loc['radius']),'#b29bf3',width=2)
        action_groups={}
        for index,d in enumerate(state['actions'],1):
            action_groups.setdefault(tuple(d['position']),[]).append((index,d))
        for position,actions in action_groups.items():
            marker=circle(position,8,'#72bfd0','#72bfd0')
            item=label(position,action_range([index for index,_ in actions]),offset=(6,2))
            details=[f'位置 ({position[0]:.6f}, {position[1]:.6f}) m']
            for index,d in actions:
                angle=f" · 示向 {d['svd_deg']:.2f}°" if d.get('svd_deg') is not None else ''
                details.append(f"#{index} · 频道 {d['channel']} · {d['result']}{angle}")
            marker.setToolTip('\n'.join(details)); item.setToolTip('\n'.join(details))
        clears = state['clear_actions'] + ([state['active_clear']] if 'active_clear' in state else [])
        for d in clears:
            color = '#65dfa4' if d['result']=='success' else '#ff7f7f' if d['result']=='no_target_in_range' else '#eeeeaa'
            circle(d['position'],20,color,width=3)
            label(d['position'],d['result'],color,offset=(6,-17))
        for p in state['candidates']:
            circle(p,12,'#82afef')
        p = state['position']
        circle(p,30,'#effcff','#42b9d6',2)
        # Four legs make a compact quadruped glyph at map scale.
        for dx in (-18,18):
            for dy in (-1,1):
                line((p[0]+dx,p[1]+dy*15),(p[0]+dx*1.5,p[1]+dy*45),'#dbf6ff',2)
        if state['status'] == 'measuring':
            a = state['time']*math.tau/2
            line(p,(p[0]+110*math.cos(a),p[1]+110*math.sin(a)),'#f1da6b',3)
        label(p,state['status'],'#ecf7ff',offset=(10,-30))


class SimulationWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(object)
    cancelled = Signal()
    def __init__(self, config, q1, model='q1_demo', archive=None, parent=None):
        super().__init__(parent)
        self.config, self.q1 = config,q1
        self.model, self.archive = model,archive
    def run(self):
        try:
            directory = Path('runs')/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            if self.model == 'q1_demo':
                from .runner import simulate
                run = simulate(self.config,directory,self.q1)
            else:
                from .baseline import run_baseline
                run = run_baseline(self.config,self.archive,self.model,directory,
                                   progress=self.progress.emit,
                                   cancelled=self.isInterruptionRequested)
            run['directory'] = str(directory.resolve())
            if self.isInterruptionRequested(): self.cancelled.emit()
            else: self.completed.emit(run)
        except Exception as exc:
            if self.isInterruptionRequested(): self.cancelled.emit()
            else: self.failed.emit(str(exc))


class Window(QMainWindow):
    def __init__(self, config, q1, replay=None):
        super().__init__()
        from .baseline import MODEL_LABELS, default_archive
        self.q1, self.run_data, self.t, self.playing, self.worker = q1,None,0.,False,None
        self.config, self._close_when_finished = config,False
        self.setWindowTitle('Jammers Lab · Baseline 1.0 探索仿真')
        self.resize(1350,900)
        self.setMinimumSize(1050,700)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        title = QLabel('JAMMERS LAB    /    探索与定位仿真')
        title.setStyleSheet('font-size:22px;font-weight:600;padding:8px')
        layout.addWidget(title)
        model_row = QHBoxLayout()
        self.model = QComboBox()
        for key,label in MODEL_LABELS.items(): self.model.addItem(label,key)
        self.model.addItem('Q1 Integration Demo · 单目标验证','q1_demo')
        self.model.setCurrentIndex(max(0,self.model.findData('hexagon_v1')))
        self.model.setMinimumWidth(265)
        self.archive = QLineEdit(str(default_archive()))
        self.archive.setPlaceholderText('选择 Baseline 1.0 两模型完整交付.zip')
        self.archive.setToolTip('直接读取交付压缩包，在独立目录运行模型原代码。')
        self.browse_archive = QPushButton('选择文件…')
        self.browse_archive.clicked.connect(self.select_archive)
        model_row.addWidget(QLabel('模型')); model_row.addWidget(self.model)
        model_row.addWidget(QLabel('交付压缩包')); model_row.addWidget(self.archive,1)
        model_row.addWidget(self.browse_archive)
        layout.addLayout(model_row)
        top = QHBoxLayout()
        self.scenario = QComboBox(); self.scenario.addItems(SCENARIOS); self.scenario.setCurrentText(config.scenario)
        self.seed = QSpinBox(); self.seed.setRange(0,2147483647); self.seed.setValue(config.seed)
        self.error = QComboBox(); self.error.addItems(['baseline_fixed_field','deterministic_hash_fixed','worst_edge'])
        self.error.setCurrentText('baseline_fixed_field')
        self.error.setToolTip('baseline_fixed_field：交付模型原始固定误差场；其余选项用于压力测试。')
        self.new = QPushButton('▶ 开始模拟')
        self.new.setStyleSheet('QPushButton { background:#176785; color:white; font-weight:600; } QPushButton:disabled { background:#8397a0; }')
        self.new.clicked.connect(self.simulate)
        self.cancel = QPushButton('停止计算'); self.cancel.setEnabled(False)
        self.cancel.clicked.connect(self.cancel_simulation)
        self.open_button = QPushButton('Replay · 打开日志'); self.open_button.clicked.connect(self.open_replay)
        for widget in (QLabel('场景'),self.scenario,QLabel('Seed'),self.seed,QLabel('测向误差'),self.error): top.addWidget(widget)
        top.addStretch()
        for widget in (self.new,self.cancel,self.open_button): top.addWidget(widget)
        layout.addLayout(top)
        self.progress_panel = QWidget()
        progress_layout = QHBoxLayout(self.progress_panel); progress_layout.setContentsMargins(0,0,0,0)
        self.progress_bar = QProgressBar(); self.progress_bar.setRange(0,0)
        self.progress_bar.setMaximumWidth(160); self.progress_bar.setMaximumHeight(12)
        self.progress_label = QLabel('正在准备模型…'); self.progress_label.setWordWrap(True)
        progress_layout.addWidget(self.progress_bar); progress_layout.addWidget(self.progress_label,1)
        self.progress_panel.hide(); layout.addWidget(self.progress_panel)
        toggles = QHBoxLayout()
        self.truth = QCheckBox('Ground Truth 源'); self.truth.setChecked(True)
        self.radii = QCheckBox('接收半径（真值）')
        self.truth.toggled.connect(self.render); self.radii.toggled.connect(self.render)
        reset = QPushButton('地图复位'); reset.clicked.connect(lambda: self.map.reset_view())
        toggles.addWidget(self.truth); toggles.addWidget(self.radii)
        toggles.addWidget(QLabel('青色：轨迹   黄色：示向 ±1°   紫色：定位区域 / 直径圆'))
        toggles.addStretch(); toggles.addWidget(reset)
        layout.addLayout(toggles)
        splitter = QSplitter()
        self.map = MapView(); splitter.addWidget(self.map)
        panel = QWidget(); side = QVBoxLayout(panel)
        group = QGroupBox('MISSION OBSERVER'); form = QFormLayout(group)
        self.values = {}
        for key in ['Virtual Time','Position','Current Channel','Detected','Cleared','Distance','Measure Count',
                    'Switch Count','Failed Clear Count','State','Measurements','Region','Diameter','Circle covers',
                    'Moving','Measuring','Switching','Clear time']:
            value = QLabel('—'); value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.values[key] = value; form.addRow(key,value)
        side.addWidget(group)
        channel_group = QGroupBox('CHANNELS · ? 未知 / D 已检测 / ✓ 已清除')
        channel_layout = QVBoxLayout(channel_group)
        self.channels = QLabel(); self.channels.setWordWrap(True)
        self.channels.setMinimumHeight(100)
        channel_group.setMinimumHeight(140)
        channel_layout.addWidget(self.channels)
        side.addWidget(channel_group)
        self.note = QLabel(); self.note.setWordWrap(True)
        side.addWidget(self.note); side.addStretch()
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(panel); scroll.setMinimumWidth(325)
        splitter.addWidget(scroll); splitter.setSizes([950,350]); layout.addWidget(splitter,1)
        self.timeline = QSlider(Qt.Orientation.Horizontal); self.timeline.setRange(0,100000)
        self.timeline.valueChanged.connect(self.seek); layout.addWidget(self.timeline)
        controls = QHBoxLayout()
        self.play = QPushButton('▶ 播放'); self.play.clicked.connect(self.toggle_play)
        self.step_button = QPushButton('单步 →'); self.step_button.clicked.connect(self.step)
        self.speed = QComboBox(); self.speed.addItems(['0.5x','1x','2x','5x','10x','20x','50x']); self.speed.setCurrentText('10x')
        self.clock = QLabel('0.0 / 0.0 s')
        for widget in (self.play,self.step_button,QLabel('速度'),self.speed,self.clock): controls.addWidget(widget)
        controls.addStretch(); layout.addLayout(controls)
        self.model.currentIndexChanged.connect(self.update_model_controls)
        self.update_model_controls()
        self.set_busy(False)
        self.statusBar().showMessage('选择模型与场景，点击“开始模拟”。计算完成后自动播放完整轨迹。')
        self.last_tick = time.monotonic()
        self.timer = QTimer(self); self.timer.timeout.connect(self.tick); self.timer.start(33)
        if replay: self.set_run(load_run(replay))
        else: self.map.draw(project([],0),[],False,False)
    def update_model_controls(self,*args):
        baseline = self.model.currentData() != 'q1_demo'
        busy = self.worker is not None and self.worker.isRunning()
        self.archive.setEnabled(baseline and not busy)
        self.browse_archive.setEnabled(baseline and not busy)
        self.update_note()
    def update_note(self):
        metadata = self.run_data['metadata'] if self.run_data else {}
        outcome = metadata.get('completion',metadata.get('outcome'))
        is_q1 = ('q1' in str(metadata.get('strategy','')).lower()
                 if metadata else self.model.currentData() == 'q1_demo')
        text = ('Q1 演示仅验证观测 → 定位 → 清除接口。\n不代表完整第三问探索策略。' if is_q1 else
                'Baseline 1.0：保持交付代码原样运行。\n先计算完整任务，再连续播放日志。\n'
                '图中紫色为 Q1 直径圆；策略清除使用原模型最小包围圆。')
        if outcome == 'incomplete_unresolved':
            text += '\n本局模型已结束，仍有目标未解决。'
            if metadata.get('unresolved_channels'):
                text += '\n未解决频道：'+', '.join(map(str,metadata['unresolved_channels']))
        elif outcome == 'cancelled':
            text += '\n本日志为中途停止的部分任务。'
        self.note.setText(text+'\n滚轮缩放，拖动地图。')
    def select_archive(self):
        path,_ = QFileDialog.getOpenFileName(self,'选择 Baseline 1.0 模型交付包',self.archive.text(),
                                             'ZIP 压缩包 (*.zip);;所有文件 (*)')
        if path: self.archive.setText(path)
    def set_busy(self,busy):
        for widget in (self.new,self.model,self.scenario,self.seed,self.error,self.open_button): widget.setEnabled(not busy)
        baseline = self.model.currentData() != 'q1_demo'
        self.archive.setEnabled(baseline and not busy)
        self.browse_archive.setEnabled(baseline and not busy)
        self.cancel.setEnabled(busy)
        self.progress_panel.setVisible(busy)
        for widget in (self.play,self.step_button,self.timeline): widget.setEnabled(not busy and self.run_data is not None)
    def simulate(self):
        if self.worker is not None and self.worker.isRunning(): return
        model = self.model.currentData()
        archive = Path(self.archive.text()).expanduser()
        if model != 'q1_demo' and not archive.is_file():
            self.show_error('找不到模型交付压缩包，请先选择 Baseline 1.0 的 ZIP 文件。')
            return
        self.playing=False; self.play.setText('▶ 播放')
        self.progress_label.setText('正在启动原始模型，计算完成后自动播放。复杂场景可能需要数分钟。')
        self.statusBar().showMessage(f'正在计算：{self.model.currentText()} · seed {self.seed.value()}')
        self.worker = SimulationWorker(replace(self.config,seed=self.seed.value(),scenario=self.scenario.currentText(),
                                               error_model=self.error.currentText()), self.q1,model,archive,self)
        self.worker.completed.connect(self.simulation_completed)
        self.worker.failed.connect(self.show_error)
        self.worker.progress.connect(self.show_progress)
        self.worker.cancelled.connect(self.simulation_cancelled)
        self.worker.finished.connect(self.worker_finished)
        self.set_busy(True)
        self.worker.start()
    def show_progress(self,progress):
        if not self.worker or self.worker.isInterruptionRequested(): return
        phases = {'starting':'正在启动模型','running':'正在执行原始模型','saving':'正在保存日志',
                  'extracting':'正在校验交付包','localizing':'正在计算定位区域',
                  'preparing':'正在准备模型','complete':'计算完成'}
        phase = progress.get('phase','running')
        self.progress_label.setText(f"{phases.get(phase,phase)} · 已执行 {progress.get('action_count',0)} 次动作"
                                    f" · 虚拟时间 {progress.get('virtual_time_s',0):.1f} s · 完成后自动播放")
    def cancel_simulation(self):
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            self.cancel.setEnabled(False)
            self.progress_label.setText('正在停止模型进程，请稍候…')
            self.statusBar().showMessage('正在停止计算…')
    def simulation_cancelled(self):
        self.statusBar().showMessage('本次计算已停止。可以调整配置后重新开始。')
    def simulation_completed(self,run):
        if self._close_when_finished: return
        self.set_run(run)
        self.toggle_play()
    def worker_finished(self):
        worker,self.worker = self.worker,None
        if worker is not None: worker.deleteLater()
        self.set_busy(False)
        if self._close_when_finished: self.close()
    def show_error(self,text):
        self.statusBar().showMessage(text)
        if not self._close_when_finished: QMessageBox.critical(self,'运行失败',text)
    def open_replay(self):
        path = QFileDialog.getExistingDirectory(self,'选择含 events.jsonl 的目录')
        if path:
            try: self.set_run(load_run(path))
            except Exception as exc: self.show_error(str(exc))
    def set_run(self,run):
        self.run_data, self.t, self.playing = run,0.,False
        self.play.setText('▶ 播放')
        outcome = {'incomplete_unresolved':' · 模型结束，仍有未解决目标',
                   'cancelled':' · 部分日志：计算已停止'}.get(run['metadata'].get('completion',run['metadata'].get('outcome')),'')
        self.statusBar().showMessage(f"{run['metadata']['scenario']} · seed {run['metadata']['seed']} · {run['metadata']['strategy']}{outcome} · {run.get('directory','Replay')}")
        self.update_note()
        if not self.worker or not self.worker.isRunning(): self.set_busy(False)
        self.render()
    @property
    def duration(self):
        return self.run_data['events'][-1]['end'] if self.run_data and self.run_data['events'] else 0
    def toggle_play(self):
        if not self.run_data: return
        if self.t >= self.duration: self.t = 0
        self.playing = not self.playing
        self.last_tick = time.monotonic()
        self.play.setText('⏸ 暂停' if self.playing else '▶ 播放')
    def step(self):
        if not self.run_data: return
        self.playing=False; self.play.setText('▶ 播放')
        self.t = next((e['end'] for e in self.run_data['events'] if e['end'] > self.t+1e-9),self.duration)
        self.render()
    def seek(self,value):
        self.t = self.duration*value/100000
        self.render()
    def tick(self):
        now = time.monotonic(); elapsed = now-self.last_tick; self.last_tick=now
        if self.playing:
            self.t = min(self.duration,self.t+elapsed*float(self.speed.currentText()[:-1]))
            if self.t >= self.duration:
                self.playing=False; self.play.setText('▶ 播放')
            self.render()
    def render(self,*args):
        if not self.run_data: return
        s = project(self.run_data['events'],self.t)
        self.map.draw(s,self.run_data['sources'],self.truth.isChecked(),self.radii.isChecked())
        loc = s['localizations'].get(s['channel'],{})
        vals = {'Virtual Time':f'{self.t:.1f} s','Position':f"({s['position'][0]:.1f}, {s['position'][1]:.1f}) m",
                'Current Channel':s['channel'],'Detected':len(s['detected']),
                'Cleared':f"{len(s['cleared'])} / {len(self.run_data['sources'])}",
                'Distance':f"{s['distance']:.1f} m",'Measure Count':s['measure_count'],
                'Switch Count':s['switch_count'],'Failed Clear Count':s['failed_clear_count'],'State':s['status'],
                'Measurements':sum(d['channel']==s['channel'] for d in s['measurements']),
                'Region':loc.get('status','—'),'Diameter':f"{loc['diameter']:.2f} m" if loc.get('diameter') is not None else '—',
                'Circle covers':loc.get('circle_covers','—')}
        vals.update({label:f"{s['breakdown'][key]:.1f} s" for label,key in [('Moving','moving'),('Measuring','measuring'),('Switching','switching'),('Clear time','clear')]})
        for key,value in vals.items(): self.values[key].setText(str(value))
        self.channels.setText('\n'.join('   '.join(f"{c:02d} {'✓' if c in s['cleared'] else 'D' if c in s['detected'] else '?'}" for c in range(i,i+4)) for i in range(1,21,4)))
        self.clock.setText(f'{self.t:.1f} / {self.duration:.1f} s')
        self.timeline.blockSignals(True); self.timeline.setValue(round(100000*self.t/self.duration) if self.duration else 0); self.timeline.blockSignals(False)
    def closeEvent(self,event):
        if self.worker and self.worker.isRunning():
            self._close_when_finished = True
            self.cancel_simulation()
            self.statusBar().showMessage('正在停止模型进程，完成后自动关闭窗口…')
            event.ignore()
        else: event.accept()


def configure_app(app):
    """Keep readable desktop chrome even when the operating system uses dark mode."""
    app.setStyle('Fusion')
    if hasattr(app.styleHints(),'setColorScheme'):
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    palette=QPalette()
    colors={
        'Window':'#edf1f5','WindowText':'#192c3d','Base':'#ffffff','AlternateBase':'#f3f6f9',
        'ToolTipBase':'#fffbea','ToolTipText':'#192c3d','Text':'#192c3d',
        'Button':'#f4f7fa','ButtonText':'#192c3d','BrightText':'#ffffff',
        'Light':'#ffffff','Midlight':'#e4ebf1','Mid':'#aab8c5','Dark':'#718293','Shadow':'#405467',
        'Highlight':'#176785','HighlightedText':'#ffffff','Link':'#176785','LinkVisited':'#645498',
        'PlaceholderText':'#738394',
    }
    for role,color in colors.items():
        palette.setColor(getattr(QPalette.ColorRole,role),QColor(color))
    for role in ('WindowText','Text','ButtonText'):
        palette.setColor(QPalette.ColorGroup.Disabled,getattr(QPalette.ColorRole,role),QColor('#8392a0'))
    app.setPalette(palette)
    app.setStyleSheet('QWidget { font-size: 12px; } QMainWindow { background: #edf1f5; } QGroupBox { font-weight:600; margin-top:10px; padding-top:12px; } QPushButton { padding:6px 10px; }')


def launch(config,q1,replay=None):
    app = QApplication.instance() or QApplication([])
    configure_app(app)
    window = Window(config,q1,replay); window.show()
    app.exec()
