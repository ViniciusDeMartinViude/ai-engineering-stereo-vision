"""Reusable lightweight Qt monitoring widgets (no heavy dependencies)."""
from __future__ import annotations

import math
from PySide6 import QtCore, QtGui, QtWidgets
import numpy as np
import pyqtgraph as pg
from theme import COLORS, FONT_NUMERIC, FONT_UI, FONTS, THRESHOLDS
from seven_segment import SevenSegmentDisplay


def set_state(widget, state):
    """Repolish QSS only when a semantic status changes."""
    if widget.property('state') == state:
        return
    widget.setProperty('state', state)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class StatusChip(QtWidgets.QLabel):
    """Text + shape/label conveys a state without relying solely on color."""
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.title = label
        self.setObjectName('statusChip')
        self.setText(f'○ {label}: WAITING')
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred,
                           QtWidgets.QSizePolicy.Policy.Fixed)
        set_state(self, 'neutral')

    def display(self, state: str, message: str):
        mark = {'good':'●', 'warning':'▲', 'bad':'✕',
                'info':'◆', 'neutral':'○'}.get(state, '○')
        value = f'{mark} {self.title}: {message}'
        if self.text() != value:
            self.setText(value)
        set_state(self, state)


class SegmentedControl(QtWidgets.QWidget):
    """Exclusive view selector that retains original 1/2/3 shortcuts."""
    selected = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(2)
        self.group = QtWidgets.QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        for mode, text, shortcut in [('both','Both cameras','1'),
                                     ('left','Left camera','2'),
                                     ('right','Right camera','3')]:
            button = QtWidgets.QPushButton(text)
            button.setCheckable(True)
            button.setToolTip(f'View {text.lower()} · shortcut {shortcut}')
            button.setProperty('active', 'false')
            self.group.addButton(button)
            self.buttons[mode] = button
            layout.addWidget(button)
            button.clicked.connect(lambda checked=False, m=mode: self.select(m))
        self.select('both', emit=False)

    def select(self, mode: str, emit=True):
        if mode not in self.buttons:
            return
        for name, button in self.buttons.items():
            new = name == mode
            if button.isChecked() != new:
                button.setChecked(new)
            state = 'true' if new else 'false'
            if button.property('active') != state:
                button.setProperty('active', state)
                button.style().unpolish(button)
                button.style().polish(button)
        if emit:
            self.selected.emit(mode)


class MetricField(QtWidgets.QWidget):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        box = QtWidgets.QVBoxLayout(self)
        box.setContentsMargins(4,2,4,2)
        box.setSpacing(1)
        self.name = QtWidgets.QLabel(label)
        self.name.setObjectName('metricLabel')
        self.value = QtWidgets.QLabel('—')
        self.value.setObjectName('metricValue')
        box.addWidget(self.name)
        box.addWidget(self.value)
        self.value.setMinimumWidth(65)
        self.value.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)

    def update_value(self, text: str):
        if self.value.text() != text:
            self.value.setText(text)


class TelemetryBar(QtWidgets.QFrame):
    """Aligned groups replacing the dense one-line telemetry string."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('controlCard')
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(8,5,8,5)
        row.setSpacing(6)
        labels = [('crc','CRC VALID'),('errors','INVALID / LOST'),
                  ('left','LEFT UTC'),('right','RIGHT UTC'),
                  ('age','LAST DELAY'),('skew','LAST SKEW R−L'),
                  ('offset','CLOCK OFFSET'),('rtt','CLOCK RTT')]
        self.fields = {}
        for i, (key,label) in enumerate(labels):
            field = MetricField(label)
            self.fields[key] = field
            row.addWidget(field, 1 if key not in ('left','right') else 2)
            if i in (1,3,5):
                sep = QtWidgets.QFrame()
                sep.setFrameShape(QtWidgets.QFrame.Shape.VLine)
                sep.setProperty('separator', True)
                row.addWidget(sep)

    def update_fields(self, **values):
        for key, value in values.items():
            if key in self.fields:
                self.fields[key].update_value(str(value))


class KpiCard(QtWidgets.QFrame):
    """Reusable metric card with clear label, window and secondary value."""
    def __init__(self, title: str, subtitle: str = '', led_color=None, parent=None):
        super().__init__(parent)
        self.setObjectName('instrumentCard')
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6,3,6,3)
        layout.setSpacing(1)
        self.setMinimumHeight(110)
        self.setMaximumHeight(145)
        if led_color:
            self.readout = SevenSegmentDisplay(title, led_color)
            self.readout.setMinimumSize(150,84)
            self.readout.setMaximumHeight(105)
            layout.addWidget(self.readout, 1)
            self.label = None
        else:
            self.readout = None
            label = QtWidgets.QLabel(title)
            label.setObjectName('metricLabel')
            layout.addWidget(label)
            self.label = QtWidgets.QLabel('—')
            self.label.setObjectName('metricValue')
            self.label.setProperty('largeNumber', True)
            layout.addWidget(self.label, 1)
        self.secondary = QtWidgets.QLabel(subtitle)
        self.secondary.setObjectName('secondary')
        self.secondary.setWordWrap(True)
        layout.addWidget(self.secondary)

    def set_value(self, value_ms=None, text=None, secondary=None):
        if self.readout is not None:
            self.readout.set_milliseconds(value_ms)
        elif text is not None and self.label.text() != text:
            self.label.setText(text)
        if secondary is not None and secondary != self.secondary.text():
            self.secondary.setText(secondary)


class DiagnosticsPanel(QtWidgets.QFrame):
    """Collapsible source barcode, always retained for CRC troubleshooting."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('controlCard')
        main = QtWidgets.QVBoxLayout(self)
        main.setContentsMargins(8,5,8,5)
        main.setSpacing(3)
        self.toggle = QtWidgets.QPushButton('▸ Diagnostics: 168-bit UTC timestamp + CRC16')
        self.toggle.setCheckable(True)
        self.toggle.setToolTip('Toggle the original embedded barcode (not modified)')
        self.toggle.toggled.connect(self._expand)
        main.addWidget(self.toggle)
        self.contents = QtWidgets.QWidget()
        inner = QtWidgets.QVBoxLayout(self.contents)
        inner.setContentsMargins(0,0,0,0)
        self.barcode = QtWidgets.QLabel('Waiting for timestamp data')
        self.barcode.setFixedHeight(30)
        self.barcode.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft |
                                  QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.note = QtWidgets.QLabel('Original 168-bit source pixels; no recoding')
        self.note.setObjectName('minor')
        inner.addWidget(self.barcode)
        inner.addWidget(self.note)
        main.addWidget(self.contents)
        self.contents.hide()

    def _expand(self, checked: bool):
        self.contents.setVisible(checked)
        self.toggle.setText(('▾' if checked else '▸') +
                            ' Diagnostics: 168-bit UTC timestamp + CRC16')

    def flag_crc_error(self):
        self.toggle.setText('▲ Diagnostics: CRC error — expand to inspect')
        self.toggle.setToolTip('Invalid barcode/CRC detected')

    def update_barcode(self, frame):
        if not self.contents.isVisible():
            return
        y = frame.shape[0] - 27
        bars = frame[y:y+16, 12:12+168*6, ::-1]
        if bars.shape != (16,1008,3):
            return
        # Barcode: only one contiguous image copy when panel is expanded.
        arr = np.ascontiguousarray(bars)
        image = QtGui.QImage(arr.data, 1008,16,1008*3,
                             QtGui.QImage.Format.Format_RGB888).copy()
        target = min(1008, max(100, self.barcode.width()-10))
        self.barcode.setPixmap(QtGui.QPixmap.fromImage(image).scaled(
            target, 28, QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
            QtCore.Qt.TransformationMode.FastTransformation))


class LatencyChart(QtWidgets.QFrame):
    """Delay/skew history with stable scale, crosshair and bounded updates."""
    def __init__(self, axis_cls, parent=None):
        super().__init__(parent)
        self.setObjectName('controlCard')
        self.samples_limit = 100
        self.paused = False
        self.mode = 'delay'
        self.high_envelope = 500.0
        main = QtWidgets.QVBoxLayout(self)
        main.setContentsMargins(6,3,6,3)
        main.setSpacing(2)
        self.setMinimumHeight(110)
        self.setMaximumHeight(145)
        header = QtWidgets.QHBoxLayout()
        self.title = QtWidgets.QLabel('LATENCY HISTORY')
        self.title.setObjectName('sectionTitle')
        header.addWidget(self.title)
        self.delay_button = QtWidgets.QPushButton('Delay')
        self.skew_button = QtWidgets.QPushButton('Skew')
        self.delay_button.setCheckable(True)
        self.skew_button.setCheckable(True)
        self.delay_button.setChecked(True)
        self.delay_button.clicked.connect(lambda: self.set_mode('delay'))
        self.skew_button.clicked.connect(lambda: self.set_mode('skew'))
        header.addWidget(self.delay_button)
        header.addWidget(self.skew_button)
        self.window = QtWidgets.QComboBox()
        for n in (100,500,2000):
            self.window.addItem(f'{n} frames', n)
        self.window.currentIndexChanged.connect(self._set_window)
        header.addWidget(self.window)
        self.pause = QtWidgets.QPushButton('Pause')
        self.pause.setCheckable(True)
        self.pause.toggled.connect(self._set_pause)
        header.addWidget(self.pause)
        main.addLayout(header)

        self.axis = axis_cls('left')
        self.plot = pg.PlotWidget(axisItems={'left':self.axis})
        self.plot.setMinimumHeight(62)
        self.plot.getAxis('left').setWidth(70)
        self.plot.getAxis('left').setTickFont(QtGui.QFont(FONT_UI, 10))
        self.plot.setLabel('bottom', 'Frames ago', color=COLORS['secondary'])
        self.plot.showGrid(x=True,y=True,alpha=.18)
        self.plot.setMouseEnabled(x=False,y=False)
        self.line = self.plot.plot(pen=pg.mkPen(COLORS['accent'], width=2))
        self.line.setClipToView(True)
        self.line.setDownsampling(auto=True)
        self.mean_line = pg.InfiniteLine(angle=0, pen=pg.mkPen(COLORS['info'],
                                                            style=QtCore.Qt.PenStyle.DashLine))
        self.target_line = pg.InfiniteLine(angle=0, pen=pg.mkPen(COLORS['warning'],
                                                               style=QtCore.Qt.PenStyle.DotLine))
        self.plot.addItem(self.mean_line)
        self.plot.addItem(self.target_line)
        self.crosshair = pg.InfiniteLine(angle=90, movable=False,
                                         pen=pg.mkPen(COLORS['secondary'], width=1))
        self.plot.addItem(self.crosshair)
        self.crosshair.hide()
        self.hover_info = QtWidgets.QLabel('—')
        self.hover_info.setObjectName('minor')
        main.addWidget(self.plot, stretch=1)
        main.addWidget(self.hover_info)
        self.plot.scene().sigMouseMoved.connect(self._mouse_moved)
        self._xdata = np.empty(2000, dtype=float)
        self._ydata = np.empty(2000, dtype=float)
        self._display_count = 0
        self._last_mean = 0.0
        self.plot.getViewBox().sigYRangeChanged.connect(self._axis_changed)
        self.set_mode('delay')

    def _axis_changed(self, *args):
        if self.mode == 'delay':
            lo, hi = self.plot.getViewBox().viewRange()[1]
            self.axis.update_for_range(lo, hi)

    def _set_window(self, index):
        self.samples_limit = int(self.window.currentData())
        self.high_envelope = 500.0
        self._display_count = 0

    def _set_pause(self, paused):
        self.paused = paused
        self.pause.setText('Resume' if paused else 'Pause')

    def set_mode(self, mode):
        self.mode = mode
        self.delay_button.setChecked(mode=='delay')
        self.skew_button.setChecked(mode=='skew')
        self.title.setText('LATENCY HISTORY' if mode=='delay' else 'CAMERA SKEW HISTORY')
        self.high_envelope = 500.0 if mode=='delay' else 30.0
        self._display_count = 0
        self.axis.enableAutoSIPrefix(False)
        if mode == 'delay':
            self.axis.setLabel('Delay (ms)',color=COLORS['text'],size='11pt')
            self.axis.update_for_range(0, self.high_envelope)
            self.target_line.setValue(THRESHOLDS.delay_good_below_ms)
        else:
            self.axis.unit = 'ms'
            self.axis.setLabel('Skew (ms)',color=COLORS['text'],size='11pt')
            self.target_line.setValue(THRESHOLDS.skew_warn_below_ms)

    def update_samples(self, delays, skews):
        if self.paused:
            return
        values = delays if self.mode=='delay' else skews
        if not values:
            return
        count = min(self.samples_limit,len(values))
        samples = list(values)[-count:]
        self._xdata[:count] = np.arange(-count+1,1,dtype=float)
        self._ydata[:count] = samples
        self._display_count = count
        self.line.setData(self._xdata[:count], self._ydata[:count])
        self._last_mean = float(np.mean(self._ydata[:count]))
        self.mean_line.setValue(self._last_mean)
        vmin = float(np.min(self._ydata[:count]))
        vmax = float(np.max(self._ydata[:count]))
        floor = 0.0 if self.mode=='delay' else min(0,vmin*1.15)
        desired_top = max(vmax*1.2, THRESHOLDS.delay_good_below_ms*1.15) if self.mode=='delay' else max(20.0,vmax*1.3)
        # Fast expansion, slow reduction to prevent visual jumping.
        self.high_envelope = max(desired_top, self.high_envelope*.985)
        self.plot.setYRange(floor, max(floor+1, self.high_envelope), padding=0)
        self._axis_changed()

    def _mouse_moved(self, pos):
        if not self._display_count:
            return
        view = self.plot.getViewBox()
        if not self.plot.sceneBoundingRect().contains(pos):
            return
        point = view.mapSceneToView(pos)
        i = int(round(point.x() + self._display_count-1))
        if i < 0 or i >= self._display_count:
            return
        self.crosshair.setPos(self._xdata[i])
        self.crosshair.show()
        self.hover_info.setText(
            f'{abs(int(self._xdata[i]))} frames ago · {self._ydata[i]:.2f} ms '
            f'· mean {self._last_mean:.2f} ms')
