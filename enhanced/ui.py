"""Qt observer: renders immutable run data; never supplies truth to a strategy."""
from dataclasses import replace
from datetime import datetime
import math
from pathlib import Path
import time
from PySide6.QtCore import Qt, QTimer, QThread, Signal, QPointF, QUrl
from PySide6.QtGui import QColor, QPen, QBrush, QPainter, QPainterPath, QPolygonF, QPalette, QDesktopServices, QShortcut, QKeySequence
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGraphicsView, QGraphicsScene, QPushButton, QComboBox, QSpinBox, QLabel, QSlider,
    QCheckBox, QFileDialog, QMessageBox, QSplitter, QFormLayout, QGroupBox, QScrollArea,
    QLineEdit, QProgressBar, QTabWidget, QDockWidget)
from .world import ScenarioConfig, SCENARIOS
from .replay import project, load_run


from .map_view import MapView, action_range
from .playback import advance_playback
from .mission_panel import MissionPanel
from .localization_view import LocalizationView
from .baseline import MODEL_LABELS, default_archive
from .q4_adapter import Q4_MODEL_LABELS


def archive_family(model):
    """Both original v1 models share one archive; v2 has its own package."""
    return 'hexagon_v2' if model == 'hexagon_v2' else 'hexagon_v1'


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
            elif self.model in Q4_MODEL_LABELS:
                from .q4_adapter import run_q4
                run = run_q4(replace(self.config,problem=4),directory,
                             progress=self.progress.emit,
                             cancelled=self.isInterruptionRequested,model=self.model)
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
        self.q1, self.run_data, self.t, self.playing, self.worker = q1,None,0.,False,None
        self.config, self._close_when_finished = config,False
        self.setWindowTitle('Jammers Lab · Baseline 1.0 探索仿真')
        self.resize(1460,940)
        self.setMinimumSize(1050,700)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        title = QLabel('JAMMERS LAB   /   自主探索控制台')
        title.setStyleSheet('font-size:22px;font-weight:600;padding:8px')
        layout.addWidget(title)
        model_row = QHBoxLayout()
        self.model = QComboBox()
        for key,label in MODEL_LABELS.items(): self.model.addItem(label,key)
        for key,label in Q4_MODEL_LABELS.items(): self.model.addItem(label,key)
        self.model.addItem('Q1 Integration Demo · 单目标验证','q1_demo')
        self.model.setCurrentIndex(max(0,self.model.findData('q4_cu' if config.problem == 4 else 'hexagon_v1')))
        self.model.setMinimumWidth(265)
        self.archive = QLineEdit(str(default_archive()))
        self._archive_family = 'hexagon_v1'
        self._archive_paths = {}
        self.archive.setPlaceholderText('选择当前模型的交付 ZIP')
        self.archive.setToolTip('直接读取交付压缩包，在独立目录运行模型原代码。')
        self.browse_archive = QPushButton('选择文件…')
        self.browse_archive.clicked.connect(self.select_archive)
        self.archive_label = QLabel('交付压缩包')
        self.current_model_label = QLabel()
        model_row.addWidget(QLabel('模型')); model_row.addWidget(self.model)
        model_row.addWidget(self.archive_label); model_row.addWidget(self.archive,1)
        model_row.addWidget(self.browse_archive)
        model_row.addWidget(self.current_model_label,1)
        layout.addLayout(model_row)
        top = QHBoxLayout()
        self.scenario = QComboBox(); self.scenario.addItems(SCENARIOS); self.scenario.setCurrentText(config.scenario)
        self.seed = QSpinBox(); self.seed.setRange(0,2147483647); self.seed.setValue(config.seed)
        self.error = QComboBox(); self.error.addItems(['baseline_fixed_field','deterministic_hash_fixed','worst_edge'])
        self.error.setCurrentText(config.error_model)
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
        reset = QPushButton('全域视角'); reset.clicked.connect(self.reset_camera)
        self.follow = QCheckBox('跟随机器狗')
        self.follow.toggled.connect(lambda checked:self.map.set_follow(checked))
        self.detail = QCheckBox('近景画面'); self.detail.setChecked(True)
        self.detail.toggled.connect(lambda checked:self.map.set_closeup(checked))
        self.labels = QCheckBox('测点编号')
        self.labels.toggled.connect(lambda checked:self.map.set_annotations(checked))
        toggles.addWidget(self.truth); toggles.addWidget(self.radii)
        toggles.addWidget(self.follow); toggles.addWidget(self.detail); toggles.addWidget(self.labels)
        toggles.addStretch()
        self.zoom_out=QPushButton('−');self.zoom_out.setAccessibleName('缩小地图')
        self.zoom_in=QPushButton('+');self.zoom_in.setAccessibleName('放大地图')
        self.zoom_out.setFixedWidth(34);self.zoom_in.setFixedWidth(34)
        self.zoom_slider=QSlider(Qt.Orientation.Horizontal);self.zoom_slider.setRange(-100,400)
        self.zoom_slider.setFixedWidth(110);self.zoom_slider.setAccessibleName('地图缩放比例')
        self.zoom_value=QLabel('100%');self.zoom_value.setMinimumWidth(44)
        self.zoom_slider.setToolTip('拖动调整缩放；触控板可双指平移、捏合缩放，或按住 Ctrl / ⌘ 双指滑动缩放。')
        for widget in (self.zoom_out,self.zoom_slider,self.zoom_in,self.zoom_value,reset):toggles.addWidget(widget)
        layout.addLayout(toggles)
        splitter = QSplitter()
        self.map = MapView(); splitter.addWidget(self.map)
        self.zoom_out.clicked.connect(self.map.zoom_out);self.zoom_in.clicked.connect(self.map.zoom_in)
        self.zoom_slider.valueChanged.connect(lambda value:self.map.set_zoom(2**(value/100)))
        self.map.zoom_changed.connect(self.update_zoom)
        self.map.follow_changed.connect(self.follow.setChecked)
        panel = QWidget(); side = QVBoxLayout(panel)
        group = QGroupBox('任务遥测  /  TELEMETRY'); form = QFormLayout(group)
        self.values = {}
        for key in ['Virtual Time','Position','Current Channel','Detected','Cleared','Distance','Measure Count',
                    'Switch Count','Failed Clear Count','State','Measurements','Region','Diameter','Circle covers',
                    'Moving','Measuring','Switching','Clear time']:
            value = QLabel('—'); value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.values[key] = value
            names={'Virtual Time':'虚拟时间','Position':'实时坐标','Current Channel':'当前频道','Detected':'已发现源','Cleared':'清除进度','Distance':'累计行程','Measure Count':'测量次数','Switch Count':'切频次数','Failed Clear Count':'清除失败','State':'动作状态','Measurements':'当前频道测向数','Region':'定位区域','Diameter':'区域直径','Circle covers':'直径圆覆盖','Moving':'行进用时','Measuring':'测量用时','Switching':'切频用时','Clear time':'清除用时'}
            if key in ('Virtual Time','Cleared'):value.setStyleSheet('font-size:18px;font-weight:700;color:#176785;')
            form.addRow(names.get(key,key),value)
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
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(panel)
        self.mission_panel=MissionPanel()
        mission_scroll=QScrollArea();mission_scroll.setWidgetResizable(True);mission_scroll.setWidget(self.mission_panel)
        self.side_tabs=QTabWidget();self.side_tabs.setMinimumWidth(395)
        self.side_tabs.addTab(mission_scroll,'任务与队列');self.side_tabs.addTab(scroll,'完整指标')
        splitter.addWidget(self.side_tabs); splitter.setSizes([1000,420]); layout.addWidget(splitter,1)
        self.timeline = QSlider(Qt.Orientation.Horizontal); self.timeline.setRange(0,100000)
        self.timeline.valueChanged.connect(self.seek); layout.addWidget(self.timeline)
        controls = QHBoxLayout()
        self.play = QPushButton('▶ 播放'); self.play.clicked.connect(self.toggle_play)
        self.step_button = QPushButton('单步 →'); self.step_button.clicked.connect(self.step)
        self.speed = QComboBox(); self.speed.addItems(['0.5x','1x','2x','5x','10x','20x','50x']); self.speed.setCurrentText('1x')
        self.speed.setToolTip('所有动作统一使用所选倍速；默认 1x，不自动调整行进或扫描速度。')
        self.clock = QLabel('0.0 / 0.0 s')
        for widget in (self.play,self.step_button,QLabel('速度'),self.speed,self.clock): controls.addWidget(widget)
        controls.addStretch()
        self.details_button=QPushButton('定位详图');self.details_button.setEnabled(False)
        self.details_button.setToolTip('重新打开当前目标的测点与定位放大区。')
        self.details_button.clicked.connect(lambda:self.localization_dock.show())
        controls.addWidget(self.details_button)
        self.open_run = QPushButton('本局日志'); self.open_run.clicked.connect(self.open_run_directory)
        self.export = QPushButton('导出统计'); self.export.clicked.connect(self.export_metrics)
        self.screenshot = QPushButton('保存画面'); self.screenshot.clicked.connect(self.save_screenshot)
        for button in (self.open_run,self.export,self.screenshot):controls.addWidget(button)
        layout.addLayout(controls)
        self.localization_view=LocalizationView()
        self.localization_dock=QDockWidget('当前目标定位放大',self)
        self.localization_dock.setObjectName('localizationDetail')
        self.localization_dock.setWidget(self.localization_view)
        self.localization_dock.setMinimumWidth(315)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea,self.localization_dock)
        self.resizeDocks([self.localization_dock],[335],Qt.Orientation.Horizontal)
        self.localization_dock.hide();self._last_detail_channel=None
        QShortcut(QKeySequence('Space'),self,activated=self.toggle_play)
        QShortcut(QKeySequence('Right'),self,activated=self.step)
        self.model.currentIndexChanged.connect(self.update_model_controls)
        self.archive.editingFinished.connect(self.preview_route)
        self.update_model_controls()
        self.set_busy(False)
        self.statusBar().showMessage('选择模型与场景，点击“开始模拟”。计算完成后自动播放完整轨迹。')
        self.last_tick = time.monotonic()
        self.timer = QTimer(self); self.timer.timeout.connect(self.tick); self.timer.start(33)
        if replay:
            run=load_run(replay);run['directory']=str(Path(replay).resolve());self.set_run(run)
        else:self.preview_route()
    def reset_camera(self):
        self.follow.setChecked(False); self.map.reset_view()
    def update_zoom(self,factor):
        self.zoom_slider.blockSignals(True);self.zoom_slider.setValue(round(100*math.log2(factor)));self.zoom_slider.blockSignals(False)
        self.zoom_value.setText(f'{factor*100:.0f}%')
    def open_run_directory(self):
        if self.run_data and self.run_data.get('directory'):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.run_data['directory']))
    def export_metrics(self):
        if not self.run_data:return
        path,_=QFileDialog.getSaveFileName(self,'导出本局统计','mission-metrics.csv','CSV (*.csv)')
        if path:
            from .exports import write_metrics_csv
            try:
                write_metrics_csv(self.run_data,path);self.statusBar().showMessage(f'统计已保存：{path}')
            except OSError as exc:self.show_error(str(exc))
    def save_screenshot(self):
        path,_=QFileDialog.getSaveFileName(self,'保存当前仿真画面','mission-view.png','PNG (*.png)')
        if path:
            if self.grab().save(path):self.statusBar().showMessage(f'画面已保存：{path}')
            else:self.show_error('无法保存到此位置。')
    def update_model_controls(self,*args):
        model = self.model.currentData()
        baseline = model in MODEL_LABELS
        if baseline:
            family = archive_family(model)
            if family != self._archive_family:
                self._archive_paths[self._archive_family] = self.archive.text()
                self.archive.setText(self._archive_paths.get(family,str(default_archive(model))))
                self._archive_family = family
            self.archive.setPlaceholderText(f'选择 {MODEL_LABELS[model]} 的交付 ZIP')
        self.config = replace(self.config,problem=4 if model in Q4_MODEL_LABELS else 3)
        self.setWindowTitle('Jammers Lab · '+Q4_MODEL_LABELS[model].replace('第四问','第四问 Q4',1) if model in Q4_MODEL_LABELS else
                            'Jammers Lab · Q1 单目标验证' if model == 'q1_demo' else
                            f'Jammers Lab · 第三问 Q3 · {MODEL_LABELS.get(model,model)}')
        busy = self.worker is not None and self.worker.isRunning()
        for widget in (self.archive_label,self.archive,self.browse_archive):
            widget.setVisible(baseline)
        self.current_model_label.setText('当前模型：'+Q4_MODEL_LABELS[model] if model in Q4_MODEL_LABELS else
                                         '当前模型：Q1 单目标验证')
        self.current_model_label.setVisible(not baseline)
        self.archive.setEnabled(baseline and not busy)
        self.browse_archive.setEnabled(baseline and not busy)
        self.update_note()
        self.preview_route()
    def preview_route(self):
        if self.run_data is not None:return
        state=project([],0)
        model=self.model.currentData()
        if model in Q4_MODEL_LABELS:
            from .q4_adapter import search_points
            state['route_points']=[dict(index=i,position=list(point),visited=False)
                                   for i,point in enumerate(search_points())]
        elif model in MODEL_LABELS:
            try:
                import json,zipfile
                with zipfile.ZipFile(Path(self.archive.text()).expanduser()) as archive:
                    prefix='Baseline_v2.0_七点六边形' if model == 'hexagon_v2' else model
                    config=json.loads(archive.read(prefix+'/config.json'))
                state['route_points']=[dict(index=i,position=point,visited=False) for i,point in enumerate(config['points'])]
            except (OSError,ValueError,KeyError,zipfile.BadZipFile):pass
        self.map.draw(state,[],False,False)
        self.mission_panel.route_progress.setText(f"固定测点 {len(state.get('route_points',[]))} 个 · 尚未开始")
    def update_note(self):
        metadata = self.run_data['metadata'] if self.run_data else {}
        outcome = metadata.get('completion',metadata.get('outcome'))
        model = self.model.currentData()
        text = ('第四问 Q4 v2.0：25 点搜索 + 左右机会复测。\n'
                '利用后续搜索点复测；一侧安全失联时保留已认证的对侧机会。\n'
                '每源最多追加 3 次；预算耗尽或无可用机会时，光学覆盖 F 收尾。\n'
                '点击“开始模拟”运行当前模型，可切换 v1.0 对照。' if model == 'q4_opportunity_v2' else
                '第四问 Q4 v1.0：25 点确定性搜索 + C/U 源任务调度。\n'
                '混合全向源与定向源；定向背面可能无信号。\n'
                '后续测向 no_signal → 保存的 U 光学后备，覆盖 F；20 m 内清除。\n'
                '点击“开始模拟”运行本机 Q4 控制器，无需 Baseline ZIP。' if model == 'q4_cu' else
                'Q1 演示仅验证观测 → 定位 → 清除接口。\n不代表完整第三问探索策略。' if model == 'q1_demo' else
                'Baseline 2.0：七点六边形，极径 1200 m。\n'
                'C/U 选择测向或认证光学收尾，每源最多追加 3 次测向。\n'
                '最小增量插入 + 两轮任务 2-opt；原始交付代码独立运行。' if model == 'hexagon_v2' else
                'Baseline 1.0：保持交付代码原样运行。\n先计算完整任务，再连续播放日志。\n'
                '图中紫色为 Q1 直径圆；策略清除使用原模型最小包围圆。')
        if metadata:
            replay_model=metadata.get('model')
            if replay_model not in Q4_MODEL_LABELS and metadata.get('problem') == 4:
                replay_model='q4_cu'  # Older Q4 logs did not record a model key.
            text += '\n当前回放：'+(Q4_MODEL_LABELS[replay_model].replace('第四问','第四问 Q4',1)
                                   if replay_model in Q4_MODEL_LABELS else str(metadata.get('strategy','已保存任务')))
        if outcome == 'incomplete_unresolved':
            text += '\n本局模型已结束，仍有目标未解决。'
            if metadata.get('unresolved_channels'):
                text += '\n未解决频道：'+', '.join(map(str,metadata['unresolved_channels']))
        elif outcome == 'cancelled':
            text += '\n本日志为中途停止的部分任务。'
        elif metadata.get('completion_reason') == 'maximum_16_cleared':
            text += '\n已清除 16 个频道，达到题设源数量上限，剩余存在性扫描无需继续。'
        self.note.setText(text+'\n触控板双指平移、捏合缩放；也可使用 + / − 和缩放条。')
    def select_archive(self):
        path,_ = QFileDialog.getOpenFileName(self,f'选择 {self.model.currentText()} 交付包',self.archive.text(),
                                             'ZIP 压缩包 (*.zip);;所有文件 (*)')
        if path:self.archive.setText(path);self.preview_route()
    def set_busy(self,busy):
        for widget in (self.new,self.model,self.scenario,self.seed,self.error,self.open_button): widget.setEnabled(not busy)
        baseline = self.model.currentData() in MODEL_LABELS
        self.archive.setEnabled(baseline and not busy)
        self.browse_archive.setEnabled(baseline and not busy)
        self.cancel.setEnabled(busy)
        self.progress_panel.setVisible(busy)
        for widget in (self.play,self.step_button,self.timeline,self.export): widget.setEnabled(not busy and self.run_data is not None)
        self.open_run.setEnabled(not busy and bool(self.run_data and self.run_data.get('directory')))
    def simulate(self):
        if self.worker is not None and self.worker.isRunning(): return
        model = self.model.currentData()
        archive = Path(self.archive.text()).expanduser()
        if model in MODEL_LABELS and not archive.is_file():
            self.show_error(f'找不到模型交付压缩包，请选择 {MODEL_LABELS[model]} 的 ZIP 文件。')
            return
        self.playing=False; self.play.setText('▶ 播放')
        self.progress_label.setText(f'正在启动 {Q4_MODEL_LABELS[model]}，计算完成后自动播放。' if model in Q4_MODEL_LABELS else
                                    '正在启动原始模型，计算完成后自动播放。复杂场景可能需要数分钟。')
        self.statusBar().showMessage(f'正在计算：{self.model.currentText()} · seed {self.seed.value()}')
        self.worker = SimulationWorker(replace(self.config,seed=self.seed.value(),scenario=self.scenario.currentText(),
                                               error_model=self.error.currentText(),problem=4 if model in Q4_MODEL_LABELS else 3),
                                       self.q1,model,archive,self)
        self.worker.completed.connect(self.simulation_completed)
        self.worker.failed.connect(self.show_error)
        self.worker.progress.connect(self.show_progress)
        self.worker.cancelled.connect(self.simulation_cancelled)
        self.worker.finished.connect(self.worker_finished)
        self.set_busy(True)
        self.worker.start()
    def show_progress(self,progress):
        if not self.worker or self.worker.isInterruptionRequested(): return
        phases = {'starting':'正在启动模型','running':'正在执行模型','q4_running':'正在运行第四问','saving':'正在保存日志',
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
        self._autoplay_pending=True
    def worker_finished(self):
        worker,self.worker = self.worker,None
        if worker is not None: worker.deleteLater()
        self.set_busy(False)
        if getattr(self,'_autoplay_pending',False):
            self._autoplay_pending=False
            self.toggle_play()
        if self._close_when_finished: self.close()
    def show_error(self,text):
        self.statusBar().showMessage(text)
        if not self._close_when_finished: QMessageBox.critical(self,'运行失败',text)
    def open_replay(self):
        path = QFileDialog.getExistingDirectory(self,'选择含 events.jsonl 的目录')
        if path:
            try:
                run=load_run(path); run['directory']=str(Path(path).resolve()); self.set_run(run)
            except Exception as exc: self.show_error(str(exc))
    def set_run(self,run):
        self.run_data, self.t, self.playing = run,0.,False
        self.localization_view.reset_selection();self.localization_dock.hide();self._last_detail_channel=None
        self.map.label_signature=None
        for key,control in (('scenario',self.scenario),('error_model',self.error)):
            if run['metadata'].get(key):control.setCurrentText(run['metadata'][key])
        if 'seed' in run['metadata']:self.seed.setValue(run['metadata']['seed'])
        self.play.setText('▶ 播放')
        model=run['metadata'].get('model')
        if model not in Q4_MODEL_LABELS:
            model='q4_cu' if run['metadata'].get('problem') == 4 else run['metadata'].get('baseline_model')
        if model is None and 'q1' in str(run['metadata'].get('strategy','')).lower(): model='q1_demo'
        index=self.model.findData(model) if model is not None else -1
        if index>=0: self.model.setCurrentIndex(index)
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
        if not self.run_data or (self.worker and self.worker.isRunning()): return
        if self.t >= self.duration: self.t = 0
        self.playing = not self.playing
        self.last_tick = time.monotonic()
        self.play.setText('⏸ 暂停' if self.playing else '▶ 播放')
    def step(self):
        if not self.run_data or (self.worker and self.worker.isRunning()): return
        self.playing=False; self.play.setText('▶ 播放')
        self.t = next((e['end'] for e in self.run_data['events'] if e['end'] > self.t+1e-9),self.duration)
        self.render()
    def seek(self,value):
        self.t = self.duration*value/100000
        self.render()
    def tick(self):
        now = time.monotonic(); elapsed = now-self.last_tick; self.last_tick=now
        if self.playing:
            self.t = advance_playback(self.run_data['events'],self.t,elapsed,float(self.speed.currentText()[:-1]),slow_actions=False)
            if self.t >= self.duration:
                self.playing=False; self.play.setText('▶ 播放')
            self.render()
    def render(self,*args):
        if not self.run_data: return
        s = project(self.run_data['events'],self.t)
        self.map.draw(s,self.run_data['sources'],self.truth.isChecked(),self.radii.isChecked())
        self.mission_panel.update_state(s,self.run_data['sources'],self.truth.isChecked())
        detail_active=self.localization_view.update_state(s)
        self.details_button.setEnabled(detail_active)
        channel=self.localization_view.channel if detail_active else None
        if channel!=self._last_detail_channel:
            self._last_detail_channel=channel
            self.localization_dock.setWindowTitle(self.localization_view.title if detail_active else '当前目标定位放大')
            self.localization_dock.setVisible(detail_active)
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
    app.setStyleSheet('QWidget { font-size:12px; } QMainWindow { background:#edf1f5; } QGroupBox { background:#f9fbfc; border:1px solid #d8e3e9; border-radius:8px; font-weight:600; margin-top:12px; padding:18px 10px 10px; } QGroupBox::title { subcontrol-origin:margin; left:12px; padding:0 5px; color:#31576b; } QPushButton { padding:7px 11px; border:1px solid #becfd9; border-radius:5px; background:#f9fcfd; } QPushButton:hover { border-color:#42899d; background:#e6f4f6; } QPushButton:disabled { color:#99a9b3; } QComboBox,QLineEdit,QSpinBox { min-height:25px; padding:2px 5px; } QScrollArea { border:0; } QSlider::groove:horizontal { background:#c5d7de; height:6px; border-radius:3px; } QSlider::sub-page:horizontal { background:#268f98; border-radius:3px; } QSlider::handle:horizontal { background:#fbffff; border:2px solid #268f98; width:12px; margin:-5px 0; border-radius:6px; }')


def launch(config,q1,replay=None,model=None,archive=None):
    app = QApplication.instance() or QApplication([])
    configure_app(app)
    window = Window(config,q1,replay)
    if model is not None and replay is None:
        window.model.setCurrentIndex(window.model.findData(model))
    if archive is not None:
        window.archive.setText(str(archive));window.preview_route()
    window.show()
    app.exec()
