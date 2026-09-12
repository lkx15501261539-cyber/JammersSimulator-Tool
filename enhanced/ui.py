"""Qt observer: renders immutable run data; never supplies truth to a strategy."""
from datetime import datetime
import math
from pathlib import Path
import time
from PySide6.QtCore import Qt, QTimer, QThread, Signal, QPointF
from PySide6.QtGui import QColor, QPen, QBrush, QPainter, QPainterPath, QPolygonF
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGraphicsView, QGraphicsScene, QPushButton, QComboBox, QSpinBox, QLabel, QSlider,
    QCheckBox, QFileDialog, QMessageBox, QSplitter, QFormLayout, QGroupBox, QScrollArea)
from .world import ScenarioConfig, SCENARIOS
from .replay import project, load_run


def pen(color, width=1):
    p = QPen(QColor(color), width)
    p.setCosmetic(True)
    return p


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
        def label(p, text, color='#9bb0c3'):
            item = scene.addText(text)
            item.setDefaultTextColor(QColor(color))
            item.setFlag(item.GraphicsItemFlag.ItemIgnoresTransformations)
            item.setPos(p[0], -p[1])
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
        for index,d in enumerate(state['actions'],1):
            circle(d['position'],8,'#72bfd0','#72bfd0')
            label(d['position'],str(index))
        clears = state['clear_actions'] + ([state['active_clear']] if 'active_clear' in state else [])
        for d in clears:
            color = '#65dfa4' if d['result']=='success' else '#ff7f7f' if d['result']=='no_target_in_range' else '#eeeeaa'
            circle(d['position'],20,color,width=3)
            label(d['position'], '  '+d['result'],color)
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
        label((p[0]+45,p[1]+50),state['status'],'#ecf7ff')


class SimulationWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)
    def __init__(self, config, q1):
        super().__init__()
        self.config, self.q1 = config,q1
    def run(self):
        try:
            from .runner import simulate
            directory = Path('runs')/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            run = simulate(self.config,directory,self.q1)
            run['directory'] = str(directory.resolve())
            self.completed.emit(run)
        except Exception as exc:
            self.failed.emit(str(exc))


class Window(QMainWindow):
    def __init__(self, config, q1, replay=None):
        super().__init__()
        self.q1, self.run_data, self.t, self.playing, self.worker = q1,None,0.,False,None
        self.setWindowTitle('Jammers Lab · Q1 Integration Demo')
        self.resize(1350,900)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        title = QLabel('JAMMERS LAB    /    探索与定位仿真')
        title.setStyleSheet('font-size:22px;font-weight:600;padding:8px')
        layout.addWidget(title)
        top = QHBoxLayout()
        self.scenario = QComboBox(); self.scenario.addItems(SCENARIOS); self.scenario.setCurrentText(config.scenario)
        self.seed = QSpinBox(); self.seed.setRange(0,2147483647); self.seed.setValue(config.seed)
        self.error = QComboBox(); self.error.addItems(['deterministic_hash_fixed','worst_edge']); self.error.setCurrentText(config.error_model)
        self.new = QPushButton('Simulation · 新建')
        self.new.clicked.connect(self.simulate)
        open_button = QPushButton('Replay · 打开日志'); open_button.clicked.connect(self.open_replay)
        for widget in (QLabel('场景'),self.scenario,QLabel('Seed'),self.seed,self.error,self.new,open_button): top.addWidget(widget)
        layout.addLayout(top)
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
        note = QLabel('Q1 演示仅验证观测 → 定位 → 清除接口。\n不代表完整第三问探索策略。\n滚轮缩放，拖动地图。'); note.setWordWrap(True)
        side.addWidget(note); side.addStretch()
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(panel); scroll.setMinimumWidth(325)
        splitter.addWidget(scroll); splitter.setSizes([950,350]); layout.addWidget(splitter,1)
        self.timeline = QSlider(Qt.Orientation.Horizontal); self.timeline.setRange(0,100000)
        self.timeline.valueChanged.connect(self.seek); layout.addWidget(self.timeline)
        controls = QHBoxLayout()
        self.play = QPushButton('▶ 播放'); self.play.clicked.connect(self.toggle_play)
        step = QPushButton('单步 →'); step.clicked.connect(self.step)
        self.speed = QComboBox(); self.speed.addItems(['0.5x','1x','2x','5x','10x','20x','50x']); self.speed.setCurrentText('10x')
        self.clock = QLabel('0.0 / 0.0 s')
        for widget in (self.play,step,QLabel('速度'),self.speed,self.clock): controls.addWidget(widget)
        controls.addStretch(); layout.addLayout(controls)
        self.statusBar().showMessage('选择 Simulation 新建演示，或 Replay 打开保存的日志目录。')
        self.last_tick = time.monotonic()
        self.timer = QTimer(self); self.timer.timeout.connect(self.tick); self.timer.start(33)
        if replay: self.set_run(load_run(replay))
        else: self.map.draw(project([],0),[],False,False)
    def simulate(self):
        if self.worker is not None and self.worker.isRunning(): return
        self.playing=False; self.play.setText('▶ 播放'); self.new.setEnabled(False)
        self.statusBar().showMessage('正在运行 Q1 演示并保存日志…')
        self.worker = SimulationWorker(ScenarioConfig(self.seed.value(),self.scenario.currentText(),error_model=self.error.currentText()), self.q1)
        self.worker.completed.connect(self.set_run)
        self.worker.failed.connect(self.show_error)
        self.worker.finished.connect(lambda: self.new.setEnabled(True))
        self.worker.start()
    def show_error(self,text):
        QMessageBox.critical(self,'运行失败',text)
        self.statusBar().showMessage(text)
    def open_replay(self):
        path = QFileDialog.getExistingDirectory(self,'选择含 events.jsonl 的目录')
        if path:
            try: self.set_run(load_run(path))
            except Exception as exc: self.show_error(str(exc))
    def set_run(self,run):
        self.run_data, self.t, self.playing = run,0.,False
        self.play.setText('▶ 播放')
        self.statusBar().showMessage(f"{run['metadata']['scenario']} · seed {run['metadata']['seed']} · {run['metadata']['strategy']} · {run.get('directory','Replay')}")
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
            self.statusBar().showMessage('请等待当前演示保存完成后关闭。')
            event.ignore()
        else: event.accept()


def launch(config,q1,replay=None):
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    app.setStyleSheet('QWidget { font-size: 12px; } QMainWindow { background: #edf1f5; } QGroupBox { font-weight:600; margin-top:10px; padding-top:12px; } QPushButton { padding:6px 10px; }')
    window = Window(config,q1,replay); window.show()
    app.exec()
