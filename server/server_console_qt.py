"""Stereo Vision Server console - PySide6 + PyQtGraph operator window.

Same visual language as the Stereo Vision Studio client: it reuses the client's
theme, icons, status pills, KPI cards, strip chart, barcode view and overlays
(client/theme.py, client/control_room_widgets.py, ...).

The console is a read-only view of ServerEngine. Capture, compositing, NVENC
encoding and UDP sending run on the engine thread, so nothing here can delay
the stream; the preview refreshes at most at --preview-fps.
"""
from __future__ import annotations

import datetime as dt
import signal
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _folder in ('common', 'client'):
    if str(_ROOT / _folder) not in sys.path:
        sys.path.insert(0, str(_ROOT / _folder))

import numpy as np  # noqa: E402
from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
import pyqtgraph as pg  # noqa: E402

from stamp_protocol import FOOTER_H, decode_strip, extract_strip, iso_utc  # noqa: E402
from model_library import format_size  # noqa: E402
from seven_segment import format_three_digits  # noqa: E402
from icons import icon, pixmap  # noqa: E402
from theme import (COLORS, THRESHOLDS, apply_theme, display_font, numeric_font,  # noqa: E402
                   quality, ui_font)
from control_room_widgets import (  # noqa: E402
    AnalysisCard, BarcodeView, DelayAxis, KpiCard, LatencyChart, StateOverlay,
    StatusChip, TelemetryBar, overline, set_state,
)

pg.setConfigOptions(imageAxisOrder='row-major', antialias=True,
                    background=COLORS['surface'], foreground=COLORS['secondary'])

APP_MARGIN = 12
TILE_HEADER_H = 40
DASHBOARD_MIN_H = 230
DASHBOARD_MAX_H = 430
WINDOW_TITLE = 'Stereo Vision Server · AI Engineering'


def mode_text(mode):
    """('yuy2', 640, 480, 30) -> 'YUY2 640×480 · 30 fps'."""
    if not mode:
        return 'No capture mode'
    fmt, w, h, rate = mode
    return f'{fmt.upper()} {w}×{h} · {rate if rate is not None else "auto"} fps'


def fmt_bytes(count):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if count < 1024 or unit == 'GB':
            return f'{count:.0f} {unit}' if unit == 'B' else f'{count:.1f} {unit}'
        count /= 1024.0
    return f'{count:.1f} GB'


def rate_state(fps, target):
    if fps is None or not target:
        return 'neutral'
    if fps >= 0.9 * target:
        return 'good'
    if fps >= 0.5 * target:
        return 'warning'
    return 'bad'


def camera_condition(phase, error, cam, thresholds=THRESHOLDS):
    """(state, mark, title, subtitle) for one camera tile, or None when live."""
    device = f'Device {cam["index"]}'
    if phase == 'failed':
        return 'bad', '✕', 'Server stopped', error or 'See the event log'
    if phase in ('starting', 'negotiating') and cam['since_frame'] is None:
        return 'info', '◌', 'Opening camera', f'{device} · negotiating a capture mode'
    if cam['recovering']:
        return 'bad', '✕', 'Recovering camera', f'{device} · reopening and trying capture modes'
    if not cam['open']:
        return 'bad', '✕', 'No signal', f'{device} not delivering · retrying every 8 s'
    since = cam['since_frame']
    if since is None:
        return 'info', '◌', 'Waiting for frames', device
    if since >= thresholds.stream_disconnected_after_s:
        return 'bad', '✕', 'No signal', f'Last frame {since:.1f} s ago · showing the last image'
    if since >= thresholds.stale_after_s:
        return 'warning', '▲', 'Camera stalled', f'Last frame {since:.1f} s ago'
    return None


class PreviewViewBox(pg.ViewBox):
    """Drag pans, wheel zooms, double-click fits the whole image."""
    fit_requested = QtCore.Signal()

    def __init__(self):
        super().__init__(lockAspect=True, invertY=True, enableMenu=False)
        self.setMouseMode(self.PanMode)

    def mouseClickEvent(self, event):
        if event.double() and event.button() == QtCore.Qt.MouseButton.LeftButton:
            event.accept()
            self.fit_requested.emit()
            return
        super().mouseClickEvent(event)


class ServerCameraPanel(QtWidgets.QFrame):
    """One camera of the OUTGOING stereo frame, as every client will receive it."""
    view_requested = QtCore.Signal(str)
    HINT = '<b>Drag</b> pan &nbsp;·&nbsp; <b>Wheel</b> zoom &nbsp;·&nbsp; <b>Double-click</b> fit'
    HUD_HELP = 'Measured camera frame rate (appsink callbacks) · source age of the last sent frame'

    def __init__(self, side, parent=None):
        super().__init__(parent)
        self.side = side
        self.native_shape = (1920, 1080)
        self._has_image = False
        self._stale = False
        self.setObjectName('cameraTile')
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        header = QtWidgets.QFrame()
        header.setObjectName('tileHeader')
        header.setFixedHeight(TILE_HEADER_H)
        self.header = header
        hdr = QtWidgets.QHBoxLayout(header)
        hdr.setContentsMargins(12, 0, 8, 0)
        hdr.setSpacing(8)
        badge = QtWidgets.QLabel('L' if side == 'LEFT' else 'R')
        badge.setObjectName('sideBadge')
        badge.setFont(ui_font(11, QtGui.QFont.Weight.Bold))
        self.title = QtWidgets.QLabel(f'{side.title()} camera')
        self.title.setObjectName('tileTitle')
        self.title.setFont(ui_font(14, QtGui.QFont.Weight.DemiBold))
        self.device = QtWidgets.QLabel('Device —')
        self.device.setObjectName('pill')
        self.device.setFont(ui_font(12))
        self.device.setToolTip('DirectShow device index (--left / --right)')
        self.mode = QtWidgets.QLabel('Negotiating…')
        self.mode.setObjectName('pill')
        self.mode.setFont(ui_font(12))
        self.hud_label = QtWidgets.QLabel('')
        self.hud_label.setObjectName('hud')
        self.hud_label.setFont(numeric_font(12))
        self.hud_label.setToolTip(self.HUD_HELP)
        self.fit_button = QtWidgets.QToolButton()
        self.fit_button.setIcon(icon('fit', COLORS['secondary']))
        self.fit_button.setIconSize(QtCore.QSize(16, 16))
        self.fit_button.setToolTip('Fit the whole image (reset zoom and pan)')
        self.fit_button.clicked.connect(self.fit_view)
        self.fit_button.hide()
        self.view_toggle = QtWidgets.QPushButton('Expand view')
        self.view_toggle.setFont(ui_font(12, QtGui.QFont.Weight.DemiBold))
        self.view_toggle.setIconSize(QtCore.QSize(14, 14))
        self.view_toggle.setFixedHeight(28)
        self.view_toggle.setMinimumWidth(122)
        self.view_toggle.clicked.connect(self._toggle_view)
        hdr.addWidget(badge)
        hdr.addWidget(self.title)
        hdr.addSpacing(4)
        hdr.addWidget(self.device)
        hdr.addWidget(self.mode)
        hdr.addStretch()
        hdr.addWidget(self.hud_label)
        hdr.addSpacing(4)
        hdr.addWidget(self.fit_button)
        hdr.addWidget(self.view_toggle)
        layout.addWidget(header)
        self._header_widgets = (badge, self.title, self.device, self.mode, self.hud_label,
                                self.fit_button, self.view_toggle)

        video = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(video)
        grid.setContentsMargins(0, 0, 0, 0)
        self.graphics = pg.GraphicsLayoutWidget()
        self.graphics.setBackground(COLORS['video'])
        self.graphics.ci.setContentsMargins(0, 0, 0, 0)
        self.vbox = PreviewViewBox()
        self.graphics.addItem(self.vbox)
        self.image_item = pg.ImageItem(axisOrder='row-major')
        self.vbox.addItem(self.image_item)
        self.stale_cover = QtWidgets.QGraphicsRectItem()
        self.stale_cover.setBrush(pg.mkBrush(0, 0, 0, 150))
        self.stale_cover.setPen(QtGui.QPen(QtCore.Qt.PenStyle.NoPen))
        self.stale_cover.setZValue(80)
        self.vbox.addItem(self.stale_cover, ignoreBounds=True)
        self.stale_cover.hide()
        grid.addWidget(self.graphics, 0, 0)
        self.overlay = StateOverlay()
        grid.addWidget(self.overlay, 0, 0, QtCore.Qt.AlignmentFlag.AlignCenter)
        hint_holder = QtWidgets.QWidget()
        hint_holder.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        hint_layout = QtWidgets.QVBoxLayout(hint_holder)
        hint_layout.setContentsMargins(12, 0, 12, 12)
        hint_layout.addStretch()
        self.hint = QtWidgets.QLabel(self.HINT)
        self.hint.setObjectName('hint')
        self.hint.setFont(ui_font(12))
        hint_layout.addWidget(self.hint, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)
        self.hint.hide()
        grid.addWidget(hint_holder, 0, 0)
        layout.addWidget(video, stretch=1)

        self._hint_timer = QtCore.QTimer(self)
        self._hint_timer.setSingleShot(True)
        self._hint_timer.setInterval(4000)
        self._hint_timer.timeout.connect(self.hint.hide)
        self.graphics.installEventFilter(self)
        self.vbox.fit_requested.connect(self.fit_view)
        self.vbox.sigRangeChanged.connect(self._range_changed)
        self.overlay.show_state('info', '◌', 'Opening camera', 'Negotiating a capture mode')
        self.set_view_mode('both')

    def eventFilter(self, obj, event):
        if obj is self.graphics:
            if event.type() == QtCore.QEvent.Type.Enter and self._has_image:
                self.hint.show()
                self._hint_timer.start()
            elif event.type() == QtCore.QEvent.Type.Leave:
                self.hint.hide()
        return False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_header()

    def showEvent(self, event):
        super().showEvent(event)
        QtCore.QTimer.singleShot(0, self._fit_header)

    def _fit_header(self):
        """On narrow tiles drop the HUD, then the capture mode, then the title."""
        if not self.isVisible():
            return
        layout = self.header.layout()
        margins = layout.contentsMargins()
        available = self.header.width() - margins.left() - margins.right()
        optional = (self.hud_label, self.mode, self.title)
        for widget in optional:
            widget.setVisible(True)
        for widget in optional:
            shown = [w for w in self._header_widgets if not w.isHidden()]
            needed = (sum(w.sizeHint().width() for w in shown)
                      + layout.spacing() * (len(shown) - 1) + 12)
            if needed <= available:
                break
            widget.setVisible(False)

    def _toggle_view(self):
        self.view_requested.emit('both' if self.view_toggle.property('expanded') else self.side.lower())

    def set_view_mode(self, mode):
        expanded = mode == self.side.lower()
        self.view_toggle.setProperty('expanded', expanded)
        label = 'Both cameras' if expanded else 'Expand view'
        if self.view_toggle.text() != label:
            self.view_toggle.setText(label)
        self.view_toggle.setIcon(icon('split' if expanded else 'expand', COLORS['secondary'], 14))
        shortcut = '1' if expanded else ('2' if self.side == 'LEFT' else '3')
        self.view_toggle.setToolTip(
            f'{"Return to both cameras" if expanded else "Expand the " + self.side.lower() + " camera"}'
            f' · shortcut {shortcut}')
        self._fit_header()

    def set_camera_info(self, cam, matched):
        device = f'Device {cam["index"]}'
        if self.device.text() != device:
            self.device.setText(device)
        text = mode_text(cam['mode']) if cam['mode'] else 'Negotiating…'
        if self.mode.text() != text:
            self.mode.setText(text)
            self._fit_header()
        if matched is None:
            pairing = 'Stereo negotiation in progress.'
        elif matched:
            pairing = 'MATCHED: both cameras use this native capture mode.'
        else:
            pairing = 'INDEPENDENT fallback: the cameras use different modes (may add skew).'
        self.mode.setToolTip(f'Native capture mode; output is scaled to the stream size.\n{pairing}')
        set_state(self.mode, 'active' if cam['mode'] else '')

    def set_image(self, bgr):
        h, w = bgr.shape[:2]
        reshaped = (w, h) != self.native_shape
        self.native_shape = (w, h)
        rgb = np.ascontiguousarray(bgr[:, :, ::-1])
        self.image_item.setImage(rgb, autoLevels=False, levels=(0, 255))
        if not self._has_image or reshaped:
            self._has_image = True
            self.stale_cover.setRect(0, 0, w, h)
            self.fit_view()
        return reshaped

    def fit_view(self):
        w, h = self.native_shape
        self.vbox.setRange(xRange=(0, w), yRange=(0, h), padding=0.01)
        self.fit_button.hide()

    def _range_changed(self, *args):
        if not self._has_image:
            return
        (x0, x1), (y0, y1) = self.vbox.viewRange()
        w, h = self.native_shape
        tolerance = 0.03 * max(w, h)
        whole = x0 <= tolerance and y0 <= tolerance and x1 >= w - tolerance and y1 >= h - tolerance
        far_out = (x1 - x0) > 1.6 * w and (y1 - y0) > 1.6 * h
        self.fit_button.setVisible(not whole or far_out)

    def set_condition(self, condition):
        """None = live; otherwise (state, mark, title, subtitle) over a dimmed last image."""
        stale = condition is not None and self._has_image
        self._stale = stale
        self.stale_cover.setVisible(stale)
        if condition is None:
            self.overlay.hide()
        else:
            self.overlay.show_state(*condition)

    def set_hud(self, fps, age_ms):
        label = f'{fps:.1f} fps' if fps is not None else ''
        if age_ms is not None:
            label += f' · {age_ms:.0f} ms'
        previous = self.hud_label.text()
        if previous != label:
            self.hud_label.setText(label)
            if len(previous) != len(label):
                self._fit_header()


class ServerChart(LatencyChart):
    """The client's strip chart, with 'delay' meaning source age at send time.

    Source ages are a few ms, so the y-axis starts small instead of at the
    client's 500 ms network-delay envelope; it still expands to show the
    watch/high bands as soon as values approach them.
    """
    MODES = dict(LatencyChart.MODES)
    MODES['delay'] = dict(LatencyChart.MODES['delay'], axis='Source age')
    INITIAL_DELAY_TOP = 60.0

    def __init__(self, axis_cls, parent=None, thresholds=THRESHOLDS):
        super().__init__(axis_cls, parent, thresholds)
        self.placeholder.setText('Waiting for the first stereo pair…')

    def _delay_min_top(self):
        return 40.0


class ServerTelemetry(TelemetryBar):
    LABELS = (('sequence', 'PAIR SEQUENCE'), ('left', 'LEFT UTC'), ('right', 'RIGHT UTC'),
              ('skew', 'LAST SKEW R−L'), ('age_l', 'LEFT SOURCE AGE'), ('age_r', 'RIGHT SOURCE AGE'),
              ('destination', 'DESTINATION'), ('udp', 'UDP RATE'), ('sent', 'UDP SENT'),
              ('encoder', 'ENCODER'), ('output', 'OUTPUT FRAME'), ('ntp', 'SNTP REQUESTS'))


class ServerDiagnostics(QtWidgets.QWidget):
    """Outgoing timestamp strip (self-decoded), stream details and the event log."""
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        self.contents = QtWidgets.QWidget()
        scroll.setWidget(self.contents)
        layout = QtWidgets.QVBoxLayout(self.contents)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)
        top = QtWidgets.QHBoxLayout()
        self.summary = QtWidgets.QLabel('Pair —  ·  — fps out  ·  — Mbps')
        self.summary.setObjectName('secondary')
        self.summary.setFont(numeric_font(13))
        self.check = QtWidgets.QLabel('○ Waiting for the first stereo pair')
        self.check.setObjectName('stateBadge')
        self.check.setFont(ui_font(12, QtGui.QFont.Weight.DemiBold))
        self.check.setToolTip('The server decodes its own outgoing strip with the same decoder '
                              'the clients use (common/stamp_protocol.py).')
        top.addWidget(self.summary)
        top.addStretch()
        top.addWidget(self.check)
        layout.addLayout(top)
        self.barcode = BarcodeView()
        layout.addWidget(self.barcode)
        self.telemetry = ServerTelemetry()
        layout.addWidget(self.telemetry)
        layout.addWidget(overline('Event log'))
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(400)
        self.log.setFont(numeric_font(12))
        self.log.setMinimumHeight(110)
        self.log.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.log.setStyleSheet(
            f"QPlainTextEdit {{ background-color:{COLORS['background']}; color:{COLORS['secondary']};"
            f" border:1px solid {COLORS['border']}; border-radius:8px; padding:6px; }}")
        layout.addWidget(self.log, 1)
        self._event_count = 0

    def set_check(self, ok, message=''):
        text = '● Outgoing strip decodes · CRC valid' if ok else f'✕ Self-check failed: {message}'
        if self.check.text() != text:
            self.check.setText(text)
        set_state(self.check, 'good' if ok else 'bad')

    def update_strip(self, frame):
        strip = extract_strip(frame)
        if strip is None:
            return
        rgb = np.ascontiguousarray(strip[:, :, ::-1])
        height, width = rgb.shape[:2]
        image = QtGui.QImage(rgb.data, width, height, width * 3,
                             QtGui.QImage.Format.Format_RGB888).copy()
        self.barcode.set_strip(image)

    def add_events(self, events, event_count):
        """Append only events not shown yet (the engine keeps the last 400)."""
        new = event_count - self._event_count
        if new <= 0:
            return
        for stamp, message in events[-min(new, len(events)):]:
            clock = dt.datetime.fromtimestamp(stamp).strftime('%H:%M:%S')
            self.log.appendPlainText(f'{clock}  {message}')
        self._event_count = event_count


class ModelsPanel(QtWidgets.QWidget):
    """The YOLO model library this server shares with the clients."""
    COLUMNS = ('Model', 'Classes', 'Size', 'Added', 'Downloads')
    ADD_HINT = 'python tools/model_library.py add best.pt --name NAME'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.folder = None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 10)
        layout.setSpacing(6)
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(10)
        self.caption = QtWidgets.QLabel('Model library')
        self.caption.setObjectName('secondary')
        self.caption.setFont(ui_font(12, QtGui.QFont.Weight.DemiBold))
        self.addresses = QtWidgets.QLabel('')
        self.addresses.setObjectName('caption')
        self.addresses.setFont(numeric_font(12))
        self.addresses.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.open_button = QtWidgets.QPushButton('Open folder')
        self.open_button.setObjectName('flat')
        self.open_button.setToolTip('Open the library folder. Copy .pt / .onnx files in, or run\n'
                                    + self.ADD_HINT)
        self.open_button.clicked.connect(self._open_folder)
        top.addWidget(self.caption)
        top.addStretch()
        top.addWidget(self.addresses)
        top.addWidget(self.open_button)
        layout.addLayout(top)
        self.table = QtWidgets.QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.horizontalHeader().setFont(display_font(11))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setFont(ui_font(13))
        self.placeholder = QtWidgets.QLabel()
        self.placeholder.setObjectName('caption')
        self.placeholder.setFont(ui_font(13))
        self.placeholder.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.views = QtWidgets.QStackedWidget()
        self.views.addWidget(self.table)
        self.views.addWidget(self.placeholder)
        layout.addWidget(self.views, 1)
        self._shown = None

    def _open_folder(self):
        if self.folder:
            Path(self.folder).mkdir(parents=True, exist_ok=True)
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(self.folder))

    def update_status(self, status):
        if status is None:
            self.open_button.hide()
            self.addresses.setText('')
            self._show_placeholder('The model library is off (--no-library).')
            return
        self.folder = status['folder']
        cap = f"cap {status['rate_mbps']:g} Mbit/s" if status['rate_mbps'] > 0 else 'no speed cap'
        self.addresses.setText('  ·  '.join(status['urls']) + f'  ·  {cap}')
        self.addresses.setToolTip('Clients use one of these with --model-library. Prefer the '
                                  'address on the network that is NOT carrying the video.')
        models = status['models']
        count = len(models)
        downloads = status['downloads']
        busy = f"  ·  sending {len(status['active'])}" if status['active'] else ''
        uploads = ('uploads on' + (f" ({', '.join(status['upload_on'])})" if status['upload_on'] else '')
                   if status['uploads_enabled'] else 'uploads off')
        self.caption.setText(f"{count} model{'s' if count != 1 else ''}  ·  "
                             f"{downloads} download{'s' if downloads != 1 else ''}{busy}  ·  {uploads}")
        self.caption.setToolTip('Uploads: ' + status['upload_summary'] +
                                f"\n{status['uploads']} received, {status['rejected_uploads']} refused")
        if status['error']:
            self._show_placeholder(f"Library unavailable: {status['error']}")
            return
        if not models:
            self._show_placeholder(f"No models yet. Copy trained .pt or .onnx files into "
                                   f"{status['folder']}, or run  {self.ADD_HINT}")
            return
        key = tuple((m.sha256, status['per_model'].get(m.sha256, 0)) for m in models)
        if key != self._shown:
            self._shown = key
            self.table.setRowCount(count)
            for r, m in enumerate(models):
                classes = ', '.join(m.classes) if m.classes else 'unknown'
                cells = (m.name, classes, format_size(m.size), m.added,
                         str(status['per_model'].get(m.sha256, 0)))
                for c, text in enumerate(cells):
                    item = QtWidgets.QTableWidgetItem(text)
                    if c == 0:
                        item.setFont(ui_font(13, QtGui.QFont.Weight.DemiBold))
                        item.setToolTip(f'{m.file}\nSHA-256 {m.sha256}'
                                        + (f'\n{m.description}' if m.description else ''))
                    elif c == 1:
                        item.setToolTip(classes)
                    if c in (2, 4):
                        item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight
                                              | QtCore.Qt.AlignmentFlag.AlignVCenter)
                    self.table.setItem(r, c, item)
        self.views.setCurrentWidget(self.table)

    def _show_placeholder(self, message):
        self._shown = None
        self.placeholder.setText(message)
        self.views.setCurrentWidget(self.placeholder)


class ServerAnalysisCard(AnalysisCard):
    TABS = (('skew', 'Camera skew', 'Right-minus-left capture time of each published pair · raw samples'),
            ('delay', 'Source age', 'Age of the older camera frame when the pair is encoded and sent'),
            ('diagnostics', 'Stream', 'Outgoing timestamp strip, encoder, network and event log · shortcut D'),
            ('library', 'Models', 'Trained YOLO models this server shares with the clients · shortcut M'))
    CHART_CLS = ServerChart
    DIAGNOSTICS_CLS = ServerDiagnostics
    FIRST_TAB = 'skew'

    def __init__(self, parent=None):
        super().__init__(DelayAxis, parent)
        self.library = ModelsPanel()
        self.stack.addWidget(self.library)
        self.limits_button.hide()
        self.legend.setToolTip('Provisional status limits (client/theme.py)')

    def show_tab(self, key, emit=True):
        if key == 'library':
            if key == self.current:
                return
            self.current = key
            if self.tabs.current != key:
                self.tabs.select(key, emit=False)
            self.stack.setCurrentWidget(self.library)
            for widget in (self.window, self.window_caption, self.limits_button, self.pause, self.legend):
                widget.setVisible(False)
            if emit:
                self.tab_changed.emit(key)
            return
        super().show_tab(key, emit)
        self.limits_button.hide()  # the server shows the default limits read-only


class RateCard(KpiCard):
    """KPI card whose seven-segment reading is frames per second, not milliseconds."""
    def set_rate(self, fps, secondary=None, state=None):
        readout = self.readout
        if fps is None:
            formatted = ('---', None, '', 'fps')
        elif fps < 0.1:
            formatted = ('000', 1, '', 'fps')
        else:
            digits, decimal, sign, _ = format_three_digits(fps)
            formatted = (digits, decimal, sign, 'fps')
        if formatted != (readout.digits, readout.decimal, readout.sign, readout.unit):
            readout.digits, readout.decimal, readout.sign, readout.unit = formatted
            readout.update()
        if state is not None:
            self.set_quality(state)
        if secondary is not None and secondary != self.secondary.text():
            self.secondary.setText(secondary)


class ServerConsole(QtWidgets.QMainWindow):
    def __init__(self, engine, args, library=None):
        super().__init__()
        apply_theme(QtWidgets.QApplication.instance())
        self.engine = engine
        self.args = args
        self.library = library
        self._library_status = None
        self._library_polled = 0.0
        self.thresholds = THRESHOLDS
        self.view_mode = 'both'
        self._equalize_pending = False
        self._shown_frame = 0
        self._last_frame = None
        self._closing = False
        self.setWindowTitle(WINDOW_TITLE)
        self.setWindowIcon(QtGui.QIcon(pixmap('logo', COLORS['accent'], 32)))
        self.setMinimumSize(1024, 640)
        self._layout_timer = QtCore.QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.setInterval(0)
        self._layout_timer.timeout.connect(self._fit_layout)
        self._build()
        self.frame_timer = QtCore.QTimer(self)
        self.frame_timer.timeout.connect(self._refresh_frame)
        self.frame_timer.start(max(15, int(1000 / max(1.0, args.preview_fps) / 2)))
        self.status_timer = QtCore.QTimer(self)
        self.status_timer.timeout.connect(self.refresh_status)
        self.status_timer.start(100)
        self.refresh_status()

    # ---- construction ---------------------------------------------------------
    def _build_app_bar(self):
        bar = QtWidgets.QFrame()
        bar.setObjectName('appBar')
        bar.setFixedHeight(56)
        self.app_bar = bar
        row = QtWidgets.QHBoxLayout(bar)
        row.setContentsMargins(16, 0, 12, 0)
        row.setSpacing(8)
        logo = QtWidgets.QLabel()
        logo.setPixmap(pixmap('logo', COLORS['accent'], 26))
        logo.setToolTip(WINDOW_TITLE)
        self.brand_text = QtWidgets.QWidget()
        brand = QtWidgets.QVBoxLayout(self.brand_text)
        brand.setSpacing(0)
        brand.setContentsMargins(4, 0, 0, 0)
        title = QtWidgets.QLabel('Stereo Vision Server')
        title.setObjectName('brandTitle')
        title.setFont(ui_font(16, QtGui.QFont.Weight.DemiBold))
        caption = QtWidgets.QLabel('AI Engineering · camera source')
        caption.setObjectName('brandCaption')
        caption.setFont(display_font(10))
        brand.addStretch()
        brand.addWidget(title)
        brand.addWidget(caption)
        brand.addStretch()
        row.addWidget(logo)
        row.addWidget(self.brand_text)
        row.addSpacing(24)
        self.cameras_chip = StatusChip('Cameras', min_width=190)
        self.stream_chip = StatusChip('Stream', min_width=180)
        self.clock_chip = StatusChip('Clock', min_width=168)
        self.pairing_chip = StatusChip('Pairing', min_width=168)
        self.library_chip = StatusChip('Library', min_width=160)
        for chip in (self.cameras_chip, self.stream_chip, self.clock_chip, self.pairing_chip):
            chip.set_clickable(True)
            chip.clicked.connect(lambda: self.analysis.show_tab('diagnostics'))
            row.addWidget(chip)
        self.library_chip.set_clickable(True)
        self.library_chip.clicked.connect(lambda: self.analysis.show_tab('library'))
        row.addWidget(self.library_chip)
        row.addStretch()
        self.snapshot_button = QtWidgets.QPushButton('Snapshot')
        self.snapshot_button.setIcon(icon('camera', COLORS['secondary']))
        self.snapshot_button.setFont(ui_font(13, QtGui.QFont.Weight.DemiBold))
        self.snapshot_button.setIconSize(QtCore.QSize(16, 16))
        self.snapshot_button.setFixedHeight(32)
        self.snapshot_button.setToolTip('Save the full-resolution outgoing frame as PNG '
                                        '(current folder) · shortcut S')
        self.snapshot_button.clicked.connect(self.save_snapshot)
        row.addWidget(self.snapshot_button)
        self.fullscreen_button = QtWidgets.QToolButton()
        self.fullscreen_button.setIconSize(QtCore.QSize(16, 16))
        self.fullscreen_button.setFixedSize(32, 32)
        self.fullscreen_button.clicked.connect(self.toggle_fullscreen)
        row.addWidget(self.fullscreen_button)
        self._update_fullscreen_button()
        return bar

    def _build(self):
        root = QtWidgets.QWidget()
        root.setObjectName('central')
        main = QtWidgets.QVBoxLayout(root)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        main.addWidget(self._build_app_bar())

        self.body = QtWidgets.QWidget()
        body = QtWidgets.QVBoxLayout(self.body)
        body.setContentsMargins(APP_MARGIN, APP_MARGIN, APP_MARGIN, APP_MARGIN)
        body.setSpacing(APP_MARGIN)
        self.cameras = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.cameras.setHandleWidth(APP_MARGIN)
        self.cameras.setChildrenCollapsible(False)
        self.left_panel = ServerCameraPanel('LEFT')
        self.right_panel = ServerCameraPanel('RIGHT')
        aspect = (self.args.width, self.args.height)
        for panel in (self.left_panel, self.right_panel):
            panel.native_shape = aspect
            panel.view_requested.connect(self.change_view)
            self.cameras.addWidget(panel)
        self.cameras.setSizes([700, 700])
        self.cameras.splitterMoved.connect(self._schedule_layout)
        body.addWidget(self.cameras, stretch=1)

        self.dashboard = QtWidgets.QWidget()
        dash = QtWidgets.QHBoxLayout(self.dashboard)
        dash.setContentsMargins(0, 0, 0, 0)
        dash.setSpacing(APP_MARGIN)
        kpis = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(kpis)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(APP_MARGIN)
        self.skew_card = KpiCard('Camera skew R−L', 'Smoothed · τ 1.5 s', max_digit_px=92)
        self.rate_card = RateCard('Output rate', f'Target {self.args.fps} fps')
        self.age_card = KpiCard('Source age', 'Older frame at send · last 100')
        t = self.thresholds
        tips = {
            self.skew_card: ('Right-minus-left capture timestamp of the published pairs, '
                             'exponentially smoothed (τ = 1.5 s).\n'
                             f'|skew| OK below {t.skew_good_below_ms:g} ms · Watch below '
                             f'{t.skew_warn_below_ms:g} ms · High at or above.'),
            self.rate_card: ('Stereo pairs encoded and sent per second.\n'
                             'OK at 90 % of --fps or more · Watch from 50 % · High below.'),
            self.age_card: ('Mean age of the older camera frame when each pair is composed and '
                            'sent (capture-to-send latency on this PC), last 100 pairs.\n'
                            f'OK below {t.delay_good_below_ms:g} ms · Watch below '
                            f'{t.delay_warn_below_ms:g} ms · High at or above.'),
        }
        for card, tip in tips.items():
            card.setToolTip(tip)
            # Limits are read-only on the server: the badge is a status label here.
            card.badge.setEnabled(False)
            card.badge.setToolTip(tip)
        grid.addWidget(self.skew_card, 0, 0, 1, 2)
        grid.addWidget(self.rate_card, 1, 0)
        grid.addWidget(self.age_card, 1, 1)
        grid.setRowStretch(0, 11)
        grid.setRowStretch(1, 9)
        self.analysis = ServerAnalysisCard()
        self.chart = self.analysis.chart
        self.diagnostics = self.analysis.diagnostics
        self.analysis.tab_changed.connect(self._tab_changed)
        dash.addWidget(kpis, 30)
        dash.addWidget(self.analysis, 70)
        body.addWidget(self.dashboard, stretch=0)
        main.addWidget(self.body, stretch=1)
        self.setCentralWidget(root)
        self.statusBar().hide()
        initial = {'fit': 'both', 'left': 'left', 'right': 'right'}.get(self.args.preview_mode, 'both')
        self.change_view(initial)

        shortcuts = [('1', lambda: self.change_view('both')),
                     ('2', lambda: self.change_view('left')),
                     ('3', lambda: self.change_view('right')),
                     ('F', self.toggle_fullscreen),
                     ('Escape', self.showMaximized),
                     ('D', self.analysis.toggle_diagnostics),
                     ('P', self.analysis.toggle_pause),
                     ('M', lambda: self.analysis.show_tab('library')),
                     ('S', self.save_snapshot)]
        for key, action in shortcuts:
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(key), self)
            shortcut.activated.connect(action)

    # ---- view handling --------------------------------------------------------
    def toggle_fullscreen(self):
        self.showMaximized() if self.isFullScreen() else self.showFullScreen()

    def _update_fullscreen_button(self):
        full = self.isFullScreen()
        self.fullscreen_button.setIcon(icon('exit_fullscreen' if full else 'fullscreen', COLORS['secondary']))
        self.fullscreen_button.setToolTip('Exit full screen · Esc' if full else 'Full screen · shortcut F')

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.Type.WindowStateChange and hasattr(self, 'fullscreen_button'):
            self._update_fullscreen_button()

    def change_view(self, mode):
        if mode not in ('both', 'left', 'right'):
            return
        self.view_mode = mode
        self.left_panel.setVisible(mode != 'right')
        self.right_panel.setVisible(mode != 'left')
        self._equalize_pending = mode == 'both'
        self.left_panel.set_view_mode(mode)
        self.right_panel.set_view_mode(mode)
        self._schedule_layout()

    def _schedule_layout(self, *args):
        self._layout_timer.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_app_bar()
        self._schedule_layout()

    def _fit_app_bar(self):
        """Narrow windows: hide the brand text first, then show Snapshot as an icon."""
        layout = self.app_bar.layout()
        for show_brand, button_text in ((True, True), (False, True), (False, False)):
            self.brand_text.setVisible(show_brand)
            self.snapshot_button.setText('Snapshot' if button_text else '')
            layout.invalidate()
            if layout.minimumSize().width() <= self.width():
                break

    def _fit_layout(self):
        """Cameras get the height their aspect ratio can use; the dashboard gets the rest."""
        inner_w = self.body.width() - 2 * APP_MARGIN
        inner_h = self.body.height() - 3 * APP_MARGIN
        if inner_w <= 0 or inner_h <= 0:
            return
        visible = [p for p in (self.left_panel, self.right_panel) if p.isVisibleTo(self)]
        if not visible:
            return
        if len(visible) == 2 and self._equalize_pending:
            self._equalize_pending = False
            half = max(1, (self.cameras.width() - self.cameras.handleWidth()) // 2)
            self.cameras.setSizes([half, half])
        if len(visible) == 2:
            sizes = self.cameras.sizes()
            tile_w = max(sizes) if sum(sizes) > 0 else (inner_w - APP_MARGIN) / 2
        else:
            tile_w = inner_w
        w, h = max(visible, key=lambda p: p.width()).native_shape
        tile_h = (tile_w - 2) * h / max(1, w) + TILE_HEADER_H + 2
        dash_h = int(max(DASHBOARD_MIN_H, min(DASHBOARD_MAX_H, 0.45 * inner_h, inner_h - tile_h)))
        if self.dashboard.height() != dash_h or self.dashboard.minimumHeight() != dash_h:
            self.dashboard.setFixedHeight(dash_h)

    def _tab_changed(self, tab):
        if tab == 'diagnostics' and self._last_frame is not None:
            self._refresh_diagnostics(self.engine.status())
        elif tab == 'library':
            self._refresh_library(force=True)

    # ---- actions --------------------------------------------------------------
    def save_snapshot(self):
        path = self.engine.save_snapshot()
        message = f'Saved {Path(path).name}' if path else 'No outgoing frame yet'
        button = self.snapshot_button
        QtWidgets.QToolTip.showText(button.mapToGlobal(QtCore.QPoint(0, button.height() + 4)),
                                    message, button, QtCore.QRect(), 2500)
        return path

    # ---- refresh --------------------------------------------------------------
    def _refresh_frame(self):
        """Show the newest outgoing frame (engine copies it at most --preview-fps)."""
        if self._closing:
            return
        seq, frame = self.engine.latest_frame()
        if frame is None or seq == self._shown_frame:
            return
        self._shown_frame = seq
        self._last_frame = frame
        w, h = self.args.width, self.args.height
        h = min(h, frame.shape[0] - FOOTER_H)
        w = min(w, frame.shape[1] // 2)
        changed = self.left_panel.set_image(frame[:h, :w])
        changed |= self.right_panel.set_image(frame[:h, w:2 * w])
        if changed:
            self._schedule_layout()

    def refresh_status(self):
        if self._closing:
            return
        s = self.engine.status()
        cams = s['cameras']
        for panel, side in ((self.left_panel, 'left'), (self.right_panel, 'right')):
            cam = cams[side]
            panel.set_camera_info(cam, s['matched'])
            panel.set_condition(camera_condition(s['phase'], s['error'], cam, self.thresholds))
            pair = s['last_pair']
            age = pair[f'age_{side[0]}'] if pair else None
            panel.set_hud(cam['fps'] if cam['since_frame'] is not None else None, age)
        self._refresh_chips(s)
        self._refresh_kpis(s)
        if self.analysis.current in ('skew', 'delay'):
            self.chart.update_samples(s['ages'], s['skews'])
        self.diagnostics.add_events(s['events'], s['event_count'])
        if self.analysis.current == 'diagnostics':
            self._refresh_diagnostics(s)
        self._refresh_library()

    def _refresh_library(self, force=False):
        """Library state at 2 Hz (it re-reads the folder, so changes show without a restart)."""
        now = time.monotonic()
        if not force and now - self._library_polled < 0.5:
            return
        self._library_polled = now
        status = self.library.status() if self.library is not None else None
        self._library_status = status
        if self.analysis.current == 'library':
            self.analysis.library.update_status(status)
        chip = self.library_chip
        if status is None:
            chip.display('neutral', 'Off', 'Started with --no-library')
            return
        urls = '\n'.join(status['urls'])
        cap = f"{status['rate_mbps']:g} Mbit/s cap" if status['rate_mbps'] > 0 else 'no speed cap'
        detail = (f"Clients: --model-library <one of>\n{urls}\n"
                  f"Folder {status['folder']} · {cap}\n"
                  f"{status['downloads']} downloads · {format_size(status['bytes_sent'])} sent\n"
                  f"Uploads: {status['upload_summary']}")
        count = len(status['models'])
        if status['error'] or not status['running']:
            chip.display('bad', 'Unavailable', (status['error'] or 'Not running') + '\n' + detail)
        elif status['active']:
            lines = '\n'.join(f'{name} → {ip}: {done * 100 // max(1, size)} %'
                               for ip, name, done, size in status['active'])
            chip.display('info', f"Sending {len(status['active'])}", lines + '\n' + detail)
        elif not count:
            chip.display('neutral', 'Empty', 'No models yet. Add one with\n'
                         + ModelsPanel.ADD_HINT + '\n' + detail)
        else:
            chip.display('good', f"{count} model{'s' if count != 1 else ''}", detail)

    def _stalled(self, s):
        return s['since_send'] is None or s['since_send'] >= self.thresholds.stale_after_s

    def _refresh_chips(self, s):
        phase = s['phase']
        cams = s['cameras']
        lines = []
        for side in ('left', 'right'):
            cam = cams[side]
            since = cam['since_frame']
            last = 'no frame yet' if since is None else f'last frame {since * 1000:.0f} ms ago'
            lines.append(f'{side.title()}: device {cam["index"]} · {mode_text(cam["mode"])} · '
                         f'{cam["fps"]:.1f} fps · {last}')
        detail = '\n'.join(lines)
        conditions = {side: camera_condition(phase, s['error'], cams[side], self.thresholds)
                      for side in ('left', 'right')}
        if phase == 'failed':
            self.cameras_chip.display('bad', 'Stopped', detail)
        elif phase in ('starting', 'negotiating'):
            self.cameras_chip.display('info', 'Negotiating', detail)
        elif all(c is None for c in conditions.values()):
            self.cameras_chip.display('good', 'Both live', detail)
        else:
            worst = max(conditions.items(), key=lambda kv: {None: 0, 'info': 1, 'warning': 2,
                                                            'bad': 3}[kv[1][0] if kv[1] else None])
            side, cond = worst
            other = conditions['right' if side == 'left' else 'left']
            word = {'warning': 'stalled', 'bad': 'lost', 'info': 'waiting'}[cond[0]]
            message = f'Both {word}' if other is not None and other[0] == cond[0] else f'{side.title()} {word}'
            self.cameras_chip.display(cond[0], message, detail)

        dests = s['destinations']
        target = ', '.join(f'{h}:{p}' for h, p in dests[:4]) + (f' (+{len(dests) - 4})' if len(dests) > 4 else '')
        detail = (f'{s["transport"].title()} → {target}\n'
                  f'NVENC H.264 · {s["bitrate_kbps"]} kbps target · '
                  f'{2 * s["width"]}×{s["height"] + FOOTER_H} at {s["target_fps"]} fps\n'
                  f'Sent {fmt_bytes(s["udp_bytes"])} in total')
        rate = s['udp_rate_mbps']
        if phase == 'failed':
            self.stream_chip.display('bad', 'Stopped', (s['error'] or '') + '\n' + detail)
        elif phase in ('starting', 'negotiating'):
            self.stream_chip.display('info', 'Starting', detail)
        elif s['since_send'] is None or s['since_send'] >= self.thresholds.stream_disconnected_after_s:
            self.stream_chip.display('bad', 'Not sending', 'No stereo pair: a camera has no frame.\n' + detail)
        elif self._stalled(s):
            self.stream_chip.display('warning', 'Stalled', detail)
        elif rate is None:
            self.stream_chip.display('good', 'Sending', detail)
        elif rate <= 0 and (s['running_for'] or 0) > 3:
            self.stream_chip.display('warning', 'No UDP output', 'The UDP sink reports no bytes sent.\n' + detail)
        else:
            self.stream_chip.display('good', f'{rate:.1f} Mbps', detail)

        ntp = s['ntp']
        if not ntp['enabled']:
            self.clock_chip.display('neutral', 'Off', 'Started with --no-ntp: clients cannot measure '
                                                      'their offset to this PC.')
        elif ntp['running']:
            self.clock_chip.display('good', f'Serving :{ntp["port"]}',
                                    f'LAN SNTP on UDP {ntp["port"]} · {ntp["requests"]:,} requests answered\n'
                                    "Reference: this PC's Windows clock (not disciplined to UTC)")
        elif ntp['error']:
            self.clock_chip.display('bad', 'Unavailable', f'LAN SNTP could not start: {ntp["error"]}')
        else:
            self.clock_chip.display('neutral', 'Starting', 'LAN SNTP clock service is starting')

        matched = s['matched']
        if matched is None:
            state = 'info' if phase in ('starting', 'negotiating') else 'neutral'
            self.pairing_chip.display(state, 'Negotiating' if state == 'info' else 'Unknown',
                                      'Looking for one capture mode both cameras support')
        elif matched:
            self.pairing_chip.display('good', 'Matched modes',
                                      'Both cameras run the same native capture mode.')
        else:
            self.pairing_chip.display('warning', 'Independent',
                                      'No shared capture mode: each camera uses its own fallback, '
                                      'which may increase camera skew.')

    def _refresh_kpis(self, s):
        t = self.thresholds
        stalled = self._stalled(s) or s['phase'] != 'running'
        skew = s['skew_smoothed']
        pair = s['last_pair']
        last = f'Last {pair["skew_ms"]:+.1f} ms · smoothed τ 1.5 s' if pair else 'Smoothed · τ 1.5 s'
        state = 'stale' if (stalled and skew is not None) else quality(skew, t.skew_good_below_ms,
                                                                        t.skew_warn_below_ms)
        self.skew_card.set_value(skew, secondary=last, state=state)

        fps = s['output_fps'] if s['phase'] == 'running' else None
        cams = s['cameras']
        camera_rates = f'cameras {cams["left"]["fps"]:.0f} / {cams["right"]["fps"]:.0f} fps'
        state = 'stale' if (stalled and s['frame_number']) else rate_state(fps, s['target_fps'])
        self.rate_card.set_rate(fps if s['frame_number'] else None,
                                secondary=f'Target {s["target_fps"]} · {camera_rates}', state=state)

        ages = s['ages'][-100:]
        mean_age = float(np.mean(ages)) if ages else None
        state = 'stale' if (stalled and ages) else quality(mean_age, t.delay_good_below_ms,
                                                           t.delay_warn_below_ms)
        p95 = f'P95 {np.percentile(ages, 95):.0f} ms · last {len(ages)}' if ages else 'Older frame at send · last 100'
        self.age_card.set_value(mean_age, secondary=p95, state=state)

    def _refresh_diagnostics(self, s):
        d = self.diagnostics
        pair = s['last_pair']
        rate = s['udp_rate_mbps']
        summary = (f'Pair {s["frame_number"]:,}  ·  {s["output_fps"]:.1f} fps out  ·  '
                   f'{rate:.2f} Mbps' if rate is not None else
                   f'Pair {s["frame_number"]:,}  ·  {s["output_fps"]:.1f} fps out')
        if d.summary.text() != summary:
            d.summary.setText(summary)
        frame = self._last_frame
        if frame is not None:
            d.update_strip(frame)
            stamp, err = decode_strip(frame)
            if stamp is not None:
                d.set_check(True)
                d.barcode.set_values({
                    'magic': 'A55A', 'version': '1',
                    'left': iso_utc(stamp.left_utc_ms).replace(' UTC', ''),
                    'sequence': f'{stamp.sequence}', 'skew': f'{stamp.right_minus_left_us:+d}',
                    'crc': '✓ valid'}, True)
            else:
                d.set_check(False, err or 'unknown error')
                d.barcode.set_values({'crc': '✕ failed'}, False)
        dests = s['destinations']
        destination = f'{dests[0][0]}:{dests[0][1]}' + (f' +{len(dests) - 1}' if len(dests) > 1 else '')
        fields = {
            'destination': f'{s["transport"]} {destination}',
            'udp': f'{rate:.2f} Mbps' if rate is not None else '—',
            'sent': fmt_bytes(s['udp_bytes']),
            'encoder': f'NVENC {s["bitrate_kbps"]} kbps',
            'output': f'{2 * s["width"]}×{s["height"] + FOOTER_H} @ {s["target_fps"]}',
            'ntp': (f'{s["ntp"]["requests"]:,}' if s['ntp']['running']
                    else ('off' if not s['ntp']['enabled'] else 'unavailable')),
        }
        if pair:
            t = self.thresholds
            fields.update({
                'sequence': f'{pair["sequence"]:,}',
                'left': iso_utc(pair['left_ns'] // 1_000_000),
                'right': iso_utc(pair['right_ns'] // 1_000_000),
                'skew': (f'{pair["skew_ms"]:+.2f} ms',
                         quality(pair['skew_ms'], t.skew_good_below_ms, t.skew_warn_below_ms)),
                'age_l': f'{pair["age_l"]:.1f} ms',
                'age_r': f'{pair["age_r"]:.1f} ms',
            })
        d.telemetry.update_fields(**fields)

    def closeEvent(self, event):
        self._closing = True
        self.frame_timer.stop()
        self.status_timer.stop()
        super().closeEvent(event)


def run_console(engine, args, library=None):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName('Stereo Vision Server')
    window = ServerConsole(engine, args, library)
    window.showMaximized()
    # Ctrl+C in the terminal closes the console (the 10 Hz timer lets Python see it).
    # If the engine stops on an error the window stays open, so the operator can read
    # why in the Stream / Cameras pills and the event log.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    app.exec()
    window._closing = True


# ---- self-test ----------------------------------------------------------------
class FakeEngine:
    """Synthetic engine for --self-test: composes real frames with the real strip encoder."""
    def __init__(self, args):
        import stereo_vision_server_multiclient as server
        self.server = server
        self.args = args
        self.phase = 'negotiating'
        self.error = None
        self.matched = None
        self.frame_number = 0
        self.preview = None
        self.events = [(time.time(), 'Starting cameras...')]
        self.since_send = None
        self.since_frame = None
        self.skew_ms = 1.5
        self.ages = []
        self.skews = []
        self.saved = []

    def publish(self, count=1, skew_ms=None):
        if skew_ms is not None:
            self.skew_ms = skew_ms
        w, h = self.args.width, self.args.height
        for _ in range(count):
            self.frame_number += 1
            now = time.time_ns()
            left = np.full((h, w, 3), (40, 120, 60), np.uint8)
            right = np.full((h, w, 3), (60, 60, 140), np.uint8)
            snap_l = self.server.Snapshot(left, now - 8_000_000, self.frame_number)
            snap_r = self.server.Snapshot(right, now - 8_000_000 + int(self.skew_ms * 1e6), self.frame_number)
            frame, age_l, age_r, delta = self.server.compose(snap_l, snap_r, self.frame_number, w, h)
            self.preview = frame.copy()
            self.ages.append(max(age_l, age_r))
            self.skews.append(delta / 1000)
            self.last_pair = {'sequence': self.frame_number, 'left_ns': snap_l.captured_ns,
                              'right_ns': snap_r.captured_ns, 'age_l': age_l, 'age_r': age_r,
                              'skew_ms': delta / 1000, 'left_count': 1, 'right_count': 1}
        self.since_send = 0.01
        self.since_frame = 0.01

    def latest_frame(self):
        return self.frame_number, self.preview

    def status(self):
        cam = lambda i: {'index': i, 'mode': ('yuy2', 640, 480, 30) if self.matched is not None else None,  # noqa: E731
                         'mode_label': None, 'since_frame': self.since_frame, 'recovering': False,
                         'open': True, 'frames': self.frame_number, 'fps': 30.0}
        return {'phase': self.phase, 'error': self.error, 'matched': self.matched,
                'frame_number': self.frame_number,
                'last_pair': getattr(self, 'last_pair', None), 'since_send': self.since_send,
                'output_fps': 10.0 if self.frame_number else 0.0, 'target_fps': self.args.fps,
                'ages': list(self.ages), 'skews': list(self.skews),
                'skew_smoothed': self.skews[-1] if self.skews else None,
                'cameras': {'left': cam(self.args.left), 'right': cam(self.args.right)},
                'udp_bytes': 123456, 'udp_rate_mbps': 3.2, 'running_for': 10.0,
                'transport': 'multicast', 'destinations': [('239.10.10.10', 5000)],
                'bitrate_kbps': 3000, 'width': self.args.width, 'height': self.args.height,
                'ntp': {'enabled': True, 'port': 9123, 'running': True, 'requests': 42, 'error': None},
                'event_count': len(self.events), 'events': list(self.events)}

    def save_snapshot(self, directory='.'):
        if self.preview is None:
            return None
        path = f'stereo_snapshot_{self.frame_number:07d}.png'
        self.saved.append(path)
        return path

    def stop(self):
        pass


def self_test():
    """Build the console against FakeEngine and check states, KPIs, chart and diagnostics."""
    import stereo_vision_server_multiclient as server
    args = server.build_parser().parse_args(['--width', '640', '--height', '480', '--fps', '10'])
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    engine = FakeEngine(args)
    import tempfile
    from model_library import LibraryStore, ModelLibraryServer
    library_dir = tempfile.TemporaryDirectory()
    library = ModelLibraryServer(LibraryStore(library_dir.name), '127.0.0.1', 0, 30, log=lambda m: None)
    library.start()
    window = ServerConsole(engine, args, library)
    window.resize(1366, 768)
    window.show()

    def pump(seconds=0.15):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    checks = []

    def check(name, condition):
        checks.append((name, bool(condition)))

    pump()
    window.refresh_status()
    check('negotiating: stream chip is info', window.stream_chip.state == 'info')
    check('negotiating: camera overlay shown', window.left_panel.overlay.isVisibleTo(window.left_panel))
    check('negotiating: pairing chip says Negotiating', window.pairing_chip.message == 'Negotiating')
    check('no Detections tab or Limits button',
          'detections' not in window.analysis.tabs.buttons and window.analysis.limits_button.isHidden())
    check('first tab is Camera skew', window.analysis.current == 'skew')

    engine.phase, engine.matched = 'running', True
    engine.events.append((time.time(), 'Stereo negotiated MATCHED capture mode: YUY2 640x480 at 30 FPS'))
    engine.publish(30)
    window._refresh_frame()
    window.refresh_status()
    pump()
    check('running: both cameras live', window.cameras_chip.state == 'good'
          and window.cameras_chip.message == 'Both live')
    check('running: stream chip shows Mbps', window.stream_chip.message == '3.2 Mbps')
    check('running: clock chip serving', window.clock_chip.state == 'good')
    check('running: pairing matched', window.pairing_chip.state == 'good')
    check('running: overlays hidden', not window.left_panel.overlay.isVisibleTo(window.left_panel))
    check('image split into camera halves', window.left_panel.native_shape == (640, 480)
          and window.right_panel.native_shape == (640, 480))
    check('skew 1.5 ms is OK', window.skew_card.state == 'good')
    check('rate card reads fps', window.rate_card.readout.unit == 'fps'
          and window.rate_card.readout.digits == '100' and window.rate_card.state == 'good')
    check('source age card OK', window.age_card.state == 'good')
    check('chart has samples', window.chart._display_count == 30)

    engine.publish(5, skew_ms=20.0)
    window.refresh_status()
    check('skew 20 ms is High', window.skew_card.state == 'bad')
    window.analysis.show_tab('delay')
    window.refresh_status()
    check('source-age chart axis', window.chart.axis.quantity == 'Source age'
          and window.chart.high_envelope < 150)

    window.analysis.show_tab('diagnostics')
    window._refresh_frame()
    window.refresh_status()
    pump()
    d = window.diagnostics
    check('outgoing strip self-check passes', 'CRC valid' in d.check.text())
    check('barcode sequence matches', d.barcode.values.get('sequence') == str(engine.frame_number))
    check('telemetry sequence', d.telemetry.fields['sequence'].value.text() == f'{engine.frame_number:,}')
    check('event log filled', 'MATCHED' in d.log.toPlainText())

    engine.since_send = engine.since_frame = 1.0
    window.refresh_status()
    check('stall: KPIs stale', window.skew_card.state == 'stale' and window.age_card.state == 'stale')
    check('stall: camera overlay', window.right_panel.overlay.title.text() == 'Camera stalled')
    check('stall: stream chip warning', window.stream_chip.state == 'warning')
    engine.since_send = engine.since_frame = 4.0
    window.refresh_status()
    check('lost: No signal', window.left_panel.overlay.title.text() == 'No signal'
          and window.stream_chip.message == 'Not sending')

    window._refresh_library(force=True)
    check('library: empty pill', window.library_chip.message == 'Empty')
    Path(library_dir.name, 'belt-v3.pt').write_bytes(b'x' * 2048)
    window.analysis.show_tab('library')
    window._refresh_library(force=True)
    table = window.analysis.library.table
    check('library: dropped file listed', window.library_chip.message == '1 model'
          and table.rowCount() == 1 and table.item(0, 0).text() == 'belt-v3')
    check('library: addresses shown', ':' in window.analysis.library.addresses.text())
    check('library tab hides chart controls', window.analysis.window.isHidden())
    window.analysis.show_tab('skew')
    library.stop()

    window.change_view('right')
    pump()
    check('view 3 hides left', window.left_panel.isHidden() and not window.right_panel.isHidden())
    window.change_view('both')
    check('snapshot returns a path', window.save_snapshot() is not None)
    engine.phase, engine.error = 'failed', 'UDP publisher: test failure'
    window.refresh_status()
    check('failure shown', window.stream_chip.state == 'bad' and
          window.left_panel.overlay.title.text() == 'Server stopped')
    window.close()
    library_dir.cleanup()

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f'  {"PASS" if ok else "FAIL"}  {name}')
    if failed:
        print(f'Server console self-test FAILED ({len(failed)} of {len(checks)})')
        return 1
    print(f'Server console self-test passed ({len(checks)} checks)')
    return 0


if __name__ == '__main__':
    raise SystemExit(self_test())
