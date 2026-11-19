#!/usr/bin/env python3
"""Stereo Vision Studio - PySide6 + PyQtGraph client (experimental branch).

Uses the SAME proven GStreamer/multicast relay, NTP correction, 168-bit
timestamp decoder, and optional YOLO worker as the Tkinter client.

Install: python -m pip install PySide6 pyqtgraph
Run: python client/stereo_vision_client_qt.py --iface-ip 192.168.0.11
Optional YOLO: --yolo-model C:/models/best.pt --yolo-device 0
"""
from __future__ import annotations

import argparse
import collections
import statistics
import sys
import time

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from stereo_vision_client_multiclient import StreamReceiver, decode_strip, iso_utc
from yolo_detector import YoloWorker
from lan_ntp import measure_ntp_offset
from seven_segment import SevenSegmentDisplay, format_three_digits
from timing_filter import ExponentialSmoother
from theme import COLORS, THRESHOLDS, style_sheet, quality
from control_room_widgets import (
    StatusChip, SegmentedControl, TelemetryBar, KpiCard,
    DiagnosticsPanel, LatencyChart,
)

# pyqtgraph uses [row, column, channel] throughout this client.
pg.setConfigOptions(imageAxisOrder='row-major', antialias=True, background='#111925',
                    foreground='#dae8fa')


def fmt_roi(roi):
    if roi is None:
        return 'FULL FRAME'
    x1, y1, x2, y2 = roi
    return f'{x1},{y1}  {x2-x1} x {y2-y1}'


class DelayAxis(pg.AxisItem):
    """Graph axis: data stays in ms, labels switch between ms and s.

    Disable SI prefixes here: pyqtgraph may otherwise label kiloms as 'kms'.
    """
    def __init__(self, orientation='left'):
        super().__init__(orientation=orientation)
        self.unit = 'ms'
        self.enableAutoSIPrefix(False)
        self.setLabel('Delay (ms)', color='#eaf4ff', size='12pt')

    def update_for_range(self, low_ms, high_ms):
        unit = 's' if max(abs(low_ms), abs(high_ms)) >= 1000.0 else 'ms'
        if unit != self.unit:
            self.unit = unit
            self.setLabel(f'Delay ({unit})', color='#eaf4ff', size='12pt')
            self.picture = None
            self.update()

    def tickStrings(self, values, scale, spacing):
        divisor = 1000.0 if self.unit == 's' else 1.0
        step = abs(spacing / divisor)
        if step >= 10:
            decimals = 0
        elif step >= 1:
            decimals = 1
        elif step >= 0.1:
            decimals = 2
        else:
            decimals = 3
        return [f'{value / divisor:.{decimals}f}' for value in values]


class DrawViewBox(pg.ViewBox):
    """Mouse-first camera: left drag ROI, right drag pan, wheel zoom, double click reset."""
    selected = QtCore.Signal(tuple)
    reset_requested = QtCore.Signal()

    def __init__(self):
        super().__init__(enableMenu=False, lockAspect=True, invertY=True)
        self.start = None
        self.preview = QtWidgets.QGraphicsRectItem()
        self.preview.setPen(pg.mkPen(COLORS['accent'], width=3))
        self.preview.setZValue(100)
        self.preview.setBrush(pg.mkBrush(105, 201, 245, 30))
        self.preview.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
        self.addItem(self.preview, ignoreBounds=True)
        self.preview.hide()
        self.setMouseEnabled(x=True, y=True)
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)

    def mouseDragEvent(self, event, axis=None):
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            # Translate the view in image pixels so the image follows the mouse.
            # The overlay ROI remains in the original image coordinate system.
            event.accept()
            if not event.isStart():
                point = self.mapSceneToView(event.scenePos())
                previous = self.mapSceneToView(event.lastScenePos())
                self.translateBy(x=previous.x()-point.x(),
                                 y=previous.y()-point.y())
            return
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            super().mouseDragEvent(event, axis)
            return
        event.accept()
        if event.isStart():
            origin = self.mapSceneToView(event.buttonDownScenePos())
            self.start = (origin.x(), origin.y())
        if self.start is None:
            return
        current = self.mapSceneToView(event.scenePos())
        x0, y0 = self.start
        x1, y1 = current.x(), current.y()
        self.preview.setRect(QtCore.QRectF(
            QtCore.QPointF(x0, y0), QtCore.QPointF(x1, y1)).normalized())
        self.preview.show()
        if event.isFinish():
            self.preview.hide()
            self.selected.emit((x0, y0, x1, y1))
            self.start = None

    def mouseClickEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            event.accept()
            if event.double():
                self.preview.hide()
                self.start = None
                self.reset_requested.emit()
            return
        super().mouseClickEvent(event)

    def wheelEvent(self, event, axis=None):
        # The built-in ViewBox zoom uses the pointer position as the anchor.
        # Image/YOLO/ROI coordinates are unchanged by view transformations.
        super().wheelEvent(event, axis)


class CameraPanel(QtWidgets.QFrame):
    roi_changed = QtCore.Signal(str, object)
    view_requested = QtCore.Signal(str)

    def __init__(self, side, color, parent=None):
        super().__init__(parent)
        self.side = side
        self.color = color
        self.native_shape = (1920, 1080)
        self.roi_box = None
        self.bounds = None
        self._updating = False
        self._has_image = False
        self.setObjectName('cameraPanel')
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(3)
        header_card = QtWidgets.QFrame()
        header_card.setObjectName('cameraHeader')
        hdr = QtWidgets.QHBoxLayout(header_card)
        hdr.setContentsMargins(8, 3, 6, 3)
        hdr.setSpacing(7)
        self.title = QtWidgets.QLabel(f'{side} CAMERA')
        self.title.setObjectName('cameraTitle')
        self.detect_count = QtWidgets.QLabel('○ 0 detected')
        self.detect_count.setObjectName('minor')
        hdr.addWidget(self.title)
        hdr.addSpacing(12)
        hdr.addWidget(self.detect_count)
        self.info = QtWidgets.QLabel('FULL FRAME')
        self.info.setObjectName('minor')
        self.info.setToolTip(
            'Left drag: select ROI · Right drag: pan · Double left click: reset ROI · Wheel: zoom')
        hdr.addSpacing(8)
        hdr.addWidget(self.info)
        hdr.addStretch()
        # A single labeled button toggles the camera between full and split.
        self.view_toggle = QtWidgets.QPushButton('Expand view')
        self.view_toggle.setMinimumWidth(108)
        self.view_toggle.setToolTip(
            f'Expand {side.lower()} camera · shortcut {2 if side == "LEFT" else 3}')
        self.view_toggle.clicked.connect(self._toggle_view)
        hdr.addWidget(self.view_toggle)
        layout.addWidget(header_card)

        self.graphics = pg.GraphicsLayoutWidget()
        self.vbox = DrawViewBox()
        self.graphics.addItem(self.vbox)
        self.image_item = pg.ImageItem(axisOrder='row-major')
        self.vbox.addItem(self.image_item)
        self.box_items = []
        self.box_labels = []
        self.hud = pg.TextItem('WAITING FOR VIDEO', color=COLORS['text'],
                               fill=pg.mkBrush(10, 20, 31, 190))
        self.hud.setZValue(70)
        self.vbox.addItem(self.hud, ignoreBounds=True)
        self.stale_cover = QtWidgets.QGraphicsRectItem()
        self.stale_cover.setBrush(pg.mkBrush(0, 0, 0, 170))
        self.stale_cover.setPen(QtCore.Qt.PenStyle.NoPen)
        self.stale_cover.setZValue(80)
        self.vbox.addItem(self.stale_cover, ignoreBounds=True)
        self.stale_cover.hide()
        self.stale_text = pg.TextItem('NO SIGNAL', color=COLORS['bad'], anchor=(0.5,0.5))
        self.stale_text.setZValue(81)
        self.vbox.addItem(self.stale_text, ignoreBounds=True)
        self.stale_text.hide()
        self.vbox.selected.connect(self._new_selection)
        self.vbox.reset_requested.connect(self.clear_roi)
        self.graphics.setToolTip(
            'Left drag: select ROI · Right drag: pan image · '
            'Double left click: reset ROI · Mouse wheel: zoom')
        layout.addWidget(self.graphics, stretch=1)

    def _toggle_view(self):
        """Toggle this camera between exclusive and side-by-side views."""
        self.view_requested.emit('both' if self.view_toggle.property('expanded') else self.side.lower())

    def set_view_mode(self, mode):
        """Update the sole view button when the parent switches camera modes."""
        expanded = mode == self.side.lower()
        self.view_toggle.setProperty('expanded', expanded)
        label = 'Both cameras' if expanded else 'Expand view'
        if self.view_toggle.text() != label:
            self.view_toggle.setText(label)
        shortcut = '1' if expanded else ('2' if self.side == 'LEFT' else '3')
        self.view_toggle.setToolTip(
            f'{"Return to both cameras" if expanded else "Expand " + self.side.lower() + " camera"} · shortcut {shortcut}')

    def set_image(self, bgr):
        h, w = bgr.shape[:2]
        self.native_shape = (w, h)
        rgb = np.ascontiguousarray(bgr[:, :, ::-1])
        self.image_item.setImage(rgb, autoLevels=False)
        self.stale_cover.setRect(0, 0, w, h)
        self.stale_text.setPos(w/2, h/2)
        self.hud.setPos(12, 12)
        if not self._has_image:
            self._has_image = True
            self.vbox.setRange(xRange=(0, w), yRange=(0, h), padding=0.01)

    def _new_selection(self, corners):
        if not self._has_image:
            return
        x0, y0, x1, y1 = corners
        w, h = self.native_shape
        left = max(0, min(w, int(min(x0, x1))))
        top = max(0, min(h, int(min(y0, y1))))
        right = max(0, min(w, int(max(x0, x1))))
        bottom = max(0, min(h, int(max(y0, y1))))
        if right-left >= 16 and bottom-top >= 16:
            self._set_roi((left, top, right, bottom), emit=True)

    def clear_roi(self):
        """Reset YOLO crop to full image without changing the current zoom."""
        self.vbox.preview.hide()
        self.vbox.start = None
        self._set_roi(None, emit=True)

    def _set_roi(self, bounds, emit):
        """Keep a passive ROI outline, not draggable Qt resize handles."""
        self.bounds = bounds
        if self.roi_box is not None:
            self.vbox.removeItem(self.roi_box)
            self.roi_box = None
        if bounds is not None:
            x1, y1, x2, y2 = bounds
            outline = QtWidgets.QGraphicsRectItem(x1, y1, x2-x1, y2-y1)
            outline.setPen(pg.mkPen(self.color, width=3))
            outline.setBrush(pg.mkBrush(105, 201, 245, 20))
            outline.setZValue(55)
            outline.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
            self.vbox.addItem(outline, ignoreBounds=True)
            self.roi_box = outline
        self.info.setText(fmt_roi(bounds))
        if emit:
            self.roi_changed.emit(self.side, bounds)

    def set_stale(self, age_s):
        stale = age_s is None or age_s >= THRESHOLDS.stale_after_s
        self.stale_cover.setVisible(stale and self._has_image)
        self.stale_text.setVisible(stale)
        if stale:
            self.stale_text.setText('NO SIGNAL' if age_s is None else f'STALE · {age_s:.1f} s')

    def set_hud(self, fps, age_ms):
        w, h = self.native_shape
        label = f'{w} × {h}   {fps:.1f} recv FPS'
        if age_ms is not None:
            label += f'   {age_ms:.0f} ms age'
        if self.hud.textItem.toPlainText() != label:
            self.hud.setText(label)

    def set_detections(self, detections):
        selected = [d for d in detections if d.side == self.side]
        while len(self.box_items) < len(selected):
            rect = QtWidgets.QGraphicsRectItem()
            rect.setPen(pg.mkPen(COLORS['accent'], width=3))
            rect.setZValue(21)
            self.vbox.addItem(rect, ignoreBounds=True)
            self.box_items.append(rect)
            label = pg.TextItem('', color=COLORS['background'],
                                fill=pg.mkBrush(COLORS['accent']))
            label.setZValue(22)
            self.vbox.addItem(label, ignoreBounds=True)
            self.box_labels.append(label)
        for i, rect in enumerate(self.box_items):
            visible = i < len(selected)
            rect.setVisible(visible)
            self.box_labels[i].setVisible(visible)
            if visible:
                d = selected[i]
                x1,y1,x2,y2 = d.xyxy
                rect.setRect(x1,y1,max(1,x2-x1),max(1,y2-y1))
                self.box_labels[i].setText(f'{d.label} {d.confidence:.0%}')
                self.box_labels[i].setPos(x1,max(0,y1-8))
        text = f'● {len(selected)} detected' if selected else '○ 0 detected'
        if self.detect_count.text() != text:
            self.detect_count.setText(text)


class Studio(QtWidgets.QMainWindow):
    def __init__(self, args):
        super().__init__()
        self.args = args
        self.reader = None
        self.yolo = None
        self._closing = False
        self.last_count = 0
        self.valid = self.invalid = self.missing = 0
        self.last_seq = None
        self.age_samples = collections.deque(maxlen=2000)
        self.skew_samples = collections.deque(maxlen=2000)
        self.frame_arrival_times = collections.deque(maxlen=100)
        self.last_frame_monotonic = None
        self.last_metrics_update = 0.0
        self.last_chart_update = 0.0
        self.last_recv_frame = None
        self.last_stamp = None
        self.last_age_ms = None
        self.last_skew_ms = None
        self.last_yolo_status = 'Disabled'
        self.last_yolo_ms = None
        self.sync_rtt = None
        self.sync_sample_count = 0
        self.last_error_count = 0
        self.last_error_mono = 0.0
        self.last_packet_count = 0
        self.last_packet_mono = time.monotonic()
        self.packet_rate = 0.0
        # The raw, signed R-L skew remains unchanged in stats/logs.
        # The instrument uses a 1.5-second time-based EMA and updates at 4 Hz.
        self.skew_smoother = ExponentialSmoother(time_constant_seconds=1.5)
        self.last_instrument_update = 0.0
        self.timestamps = collections.deque(maxlen=300)
        self.clock_offset = args.clock_offset_ms
        self.clock_status = 'not measured'
        self.latest_stamp = None
        self.view_mode = 'both'
        self._last_overlay_id = -1
        self._last_overlay_mono = 0
        self.setWindowTitle('AI Stereo Vision Studio  |  PySide6 + PyQtGraph')
        self.setMinimumSize(960, 640)
        self._build()
        self._start_services()

    def _build(self):
        self.setStyleSheet(style_sheet())
        root = QtWidgets.QWidget()
        main = QtWidgets.QVBoxLayout(root)
        main.setContentsMargins(8, 6, 8, 6)
        main.setSpacing(5)

        header = QtWidgets.QHBoxLayout()
        brand = QtWidgets.QLabel('◉ AI STEREO VISION STUDIO')
        brand.setObjectName('appTitle')
        header.addWidget(brand)
        header.addStretch()
        self.stream_chip = StatusChip('STREAM')
        self.clock_chip = StatusChip('CLOCK')
        self.yolo_chip = StatusChip('YOLO')
        for chip in (self.stream_chip, self.clock_chip, self.yolo_chip):
            header.addWidget(chip)
        main.addLayout(header)

        self.cameras = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.cameras.setHandleWidth(9)  # Slightly more breathing room between feeds
        self.left_panel = CameraPanel('LEFT', COLORS['accent'])
        self.right_panel = CameraPanel('RIGHT', COLORS['accent'])
        self.left_panel.roi_changed.connect(self._roi_changed)
        self.right_panel.roi_changed.connect(self._roi_changed)
        self.left_panel.view_requested.connect(self.change_view)
        self.right_panel.view_requested.connect(self.change_view)
        self.cameras.addWidget(self.left_panel)
        self.cameras.addWidget(self.right_panel)
        self.cameras.setSizes([700, 700])
        self.change_view('both')
        # Give cameras all remaining vertical space after the compact telemetry.
        main.addWidget(self.cameras, stretch=1)

        self.diagnostics = DiagnosticsPanel()
        self.telemetry = self.diagnostics.telemetry
        main.addWidget(self.diagnostics)

        bottom = QtWidgets.QHBoxLayout()
        bottom.setSpacing(5)
        self.average_delay_card = KpiCard('AVERAGE DELAY', 'Last 100 valid frames',
                                          led_color=COLORS['accent'])
        self.delay_p95_card = KpiCard('DELAY P95', 'Last 100 valid frames',
                                     led_color=COLORS['accent'])
        self.skew_card = KpiCard('CAMERA SKEW R−L', 'Smoothed · τ = 1.5 s',
                                 led_color=COLORS['accent'])
        bottom.addWidget(self.average_delay_card, 2)
        bottom.addWidget(self.delay_p95_card, 2)
        bottom.addWidget(self.skew_card, 2)
        self.history = LatencyChart(DelayAxis)
        bottom.addWidget(self.history, 5)
        # On typical 1080p windows, the cameras occupy about two-thirds of
        # the available vertical space. The lower strip never expands to
        # compete with the video area.
        main.addLayout(bottom, stretch=0)

        self.setCentralWidget(root)
        # No persistent bottom footer: shortcut hints remain in control tooltips.
        self.statusBar().hide()
        for key, mode in [('1','both'),('2','left'),('3','right')]:
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(key), self)
            shortcut.activated.connect(lambda v=mode: self.change_view(v))
        shortcut = QtGui.QShortcut(QtGui.QKeySequence('F'), self)
        shortcut.activated.connect(self.toggle_fullscreen)
        shortcut = QtGui.QShortcut(QtGui.QKeySequence('Escape'), self)
        shortcut.activated.connect(self.showMaximized)
        self.health_timer = QtCore.QTimer(self)
        self.health_timer.timeout.connect(self._refresh_metrics)
        self.health_timer.start(100)

    def toggle_fullscreen(self):
        self.showMaximized() if self.isFullScreen() else self.showFullScreen()

    def change_view(self, mode):
        if mode not in ('both', 'left', 'right'):
            return
        self.view_mode = mode
        self.left_panel.setVisible(mode != 'right')
        self.right_panel.setVisible(mode != 'left')
        self.left_panel.set_view_mode(mode)
        self.right_panel.set_view_mode(mode)

    def _roi_changed(self, side, bounds):
        if self.yolo:
            self.yolo.set_roi(side, bounds)
        # ROI coordinates are already shown directly in the camera control strip.

    def _refresh_metrics(self):
        """10 Hz status/KPI cadence; raw image path remains frame-driven."""
        now = time.monotonic()
        stale_for = None if self.last_frame_monotonic is None else now-self.last_frame_monotonic
        for panel in (self.left_panel, self.right_panel):
            panel.set_stale(stale_for)
        if stale_for is None:
            self.stream_chip.display('neutral', 'WAITING')
        elif stale_for >= THRESHOLDS.stream_disconnected_after_s:
            self.stream_chip.display('bad', f'NO SIGNAL · {stale_for:.1f}s')
        elif stale_for >= THRESHOLDS.stale_after_s:
            self.stream_chip.display('warning', f'STALE · {stale_for:.1f}s')
        else:
            self.stream_chip.display('good', f'LIVE · {self.packet_rate:.0f} UDP/s')

        if self.args.no_time_sync:
            self.clock_chip.display('neutral','DISABLED')
        elif self.sync_rtt is None:
            self.clock_chip.display('warning','UNAVAILABLE')
        else:
            self.clock_chip.display('good', f'{self.clock_offset:+.1f} ms · RTT {self.sync_rtt:.2f} ms')
        if not self.yolo:
            self.yolo_chip.display('neutral', 'DISABLED')
        else:
            status = self.last_yolo_status
            state = 'bad' if 'error' in status.lower() else (
                'warning' if 'loading' in status.lower() else 'good')
            suffix = f' · {self.last_yolo_ms:.0f} ms' if self.last_yolo_ms else ''
            self.yolo_chip.display(state, status[:36] + suffix)

        recv_fps = 0.0
        if len(self.frame_arrival_times) > 1:
            dt = self.frame_arrival_times[-1] - self.frame_arrival_times[0]
            if dt > 0 and (stale_for is not None and stale_for < 1):
                recv_fps = (len(self.frame_arrival_times)-1)/dt
        for panel in (self.left_panel, self.right_panel):
            if panel._has_image:
                panel.set_hud(recv_fps, self.last_age_ms)
        self.diagnostics.update_counts(self.valid, self.invalid, self.missing)
        if self.last_stamp is not None:
            st = self.last_stamp
            self.telemetry.update_fields(
                crc=f'{self.valid}', errors=f'{self.invalid} / {self.missing}',
                left=iso_utc(st.left_utc_ms).replace(' UTC',''),
                right=iso_utc(st.right_utc_ms).replace(' UTC',''),
                age=f'{self.last_age_ms:.1f} ms',
                skew=f'{self.last_skew_ms:+.2f} ms',
                offset=f'{self.clock_offset:+.2f} ms',
                rtt=f'{self.sync_rtt:.3f} ms' if self.sync_rtt is not None else 'Unavailable')
        if self.age_samples:
            values = np.asarray(list(self.age_samples)[-100:], dtype=float)
            self.average_delay_card.set_value(float(np.mean(values)),
                    secondary=f'Avg · last {len(values)} valid frames · {np.min(values):.0f}–{np.max(values):.0f} ms')
            p95 = float(np.percentile(values, 95))
            jitter = float(np.std(np.diff(values))) if len(values)>1 else 0.0
            self.delay_p95_card.set_value(value_ms=p95,
                    secondary=f'Jitter (std Δ): {jitter:.1f} ms · {len(values)} frames')
            self.skew_card.set_value(self.skew_smoother.value,
                    secondary=f'EMA τ=1.5 s · last {self.last_skew_ms:+.2f} ms')
            if now-self.last_chart_update >= 0.1:
                self.history.update_samples(self.age_samples,self.skew_samples)
                self.last_chart_update = now

    def _start_services(self):
        if not self.args.no_time_sync:
            try:
                offset, rtt, samples = measure_ntp_offset(
                    self.args.time_server, self.args.ntp_port, self.args.ntp_samples)
                self.clock_offset += offset
                self.sync_rtt = rtt
                self.sync_sample_count = samples
                self.clock_status = f'Clock offset {offset:+.1f} ms; RTT {rtt:.2f} ms ({samples} samples)'
            except (OSError, TimeoutError, ValueError) as exc:
                self.clock_status = f'Clock sync unavailable: {exc}'
        else:
            self.clock_status = 'Clock sync disabled'
        self.reader = StreamReceiver(
            self.args.port, self.args.transport, self.args.multicast_group,
            None, self.args.iface_ip, self.args.relay_port)
        self.reader.start()
        if self.args.yolo_model:
            self.yolo = YoloWorker(self.args.yolo_model, self.args.yolo_conf,
                                   self.args.yolo_imgsz, self.args.yolo_device)
            self.yolo.start()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(50)

    def tick(self):
        if self._closing:
            return
        try:
            self.reader.check_error()
        except RuntimeError as exc:
            self.stream_chip.display('bad', f'STREAM ERROR: {exc}')
            self.timer.stop()
            return
        frame, recv_ns, frame_id = self.reader.latest()
        if frame is None or frame_id == self.last_count:
            return
        self.last_count = frame_id
        now = time.monotonic()
        self.last_frame_monotonic = now
        self.frame_arrival_times.append(now)
        self.last_recv_frame = frame
        source_h = max(1, frame.shape[0]-100)
        half = frame.shape[1]//2
        self.left_panel.set_image(frame[:source_h, :half])
        self.right_panel.set_image(frame[:source_h, half:])
        if self.yolo:
            self.yolo.submit(frame, frame_id)
            detections, status, result_id, result_mono, ms, error = self.yolo.snapshot()
            self.last_yolo_status = status
            self.last_yolo_ms = ms
            if result_mono and time.monotonic()-result_mono < 1.0:
                self.left_panel.set_detections(detections)
                self.right_panel.set_detections(detections)
            else:
                self.left_panel.set_detections(())
                self.right_panel.set_detections(())
        stamp, err = decode_strip(frame)
        if stamp is not None:
            self.valid += 1
            age = recv_ns / 1e6 - stamp.left_utc_ms - self.clock_offset
            self.age_samples.append(age)
            self.skew_samples.append(stamp.right_minus_left_us/1000.0)
            self.last_age_ms = age
            self.last_skew_ms = stamp.right_minus_left_us/1000.0
            self.last_stamp = stamp
            now = time.monotonic()
            filtered_skew_ms = self.skew_smoother.update(
                stamp.right_minus_left_us / 1000.0, now)
            # Dashboard widgets update from the 10 Hz presentation timer.
            if self.last_seq is not None:
                delta = (stamp.sequence-self.last_seq)%(1<<32)
                if 1 < delta < 10000:
                    self.missing += delta-1
            self.last_seq = stamp.sequence
            self.latest_stamp = stamp
            # Metrics/chart refresh independently at 10 Hz.
        else:
            self.invalid += 1
            self.last_error_mono = time.monotonic()
            self.diagnostics.flag_crc_error()
        if self.diagnostics.contents.isVisible():
            self.diagnostics.update_barcode(frame)
        packet_count = self.reader.relay.packets if self.reader.relay else frame_id
        elapsed = time.monotonic()-self.last_packet_mono
        if elapsed >= 1.0:
            self.packet_rate = (packet_count-self.last_packet_count)/elapsed
            self.last_packet_count = packet_count
            self.last_packet_mono = time.monotonic()

    def closeEvent(self, event):
        self._closing = True
        if hasattr(self, 'timer'):
            self.timer.stop()
        if self.yolo:
            self.yolo.stop()
        if self.reader:
            self.reader.stop()
        super().closeEvent(event)


def build_parser():
    p = argparse.ArgumentParser(description='AI Stereo Vision Studio (PySide6 + PyQtGraph)')
    p.add_argument('--port', type=int, default=5000)
    p.add_argument('--transport', choices=['multicast', 'unicast'], default='multicast')
    p.add_argument('--multicast-group', default='239.10.10.10')
    p.add_argument('--iface-ip', default=None, help='Local Ethernet IP for multicast')
    p.add_argument('--relay-port', type=int, default=15000)
    p.add_argument('--time-server', default='192.168.0.10')
    p.add_argument('--ntp-port', type=int, default=9123)
    p.add_argument('--ntp-samples', type=int, default=7)
    p.add_argument('--clock-offset-ms', type=float, default=0)
    p.add_argument('--no-time-sync', action='store_true')
    p.add_argument('--yolo-model', default=None)
    p.add_argument('--yolo-conf', type=float, default=0.35)
    p.add_argument('--yolo-imgsz', type=int, default=640)
    p.add_argument('--yolo-device', default='auto')
    p.add_argument('--self-test', action='store_true',
                   help='Test Qt image/ROI controls without using the network')
    return p


def main():
    p = build_parser()
    args = p.parse_args()
    if args.transport == 'multicast' and not args.iface_ip and not args.self_test:
        p.error('Multicast requires --iface-ip LOCAL_ETHERNET_IP')
    if not 0 < args.yolo_conf <= 1:
        p.error('YOLO confidence must be in (0,1]')
    app = QtWidgets.QApplication(sys.argv[:1])
    app.setStyle('Fusion')
    if args.self_test:
        panel = CameraPanel('LEFT', '#70e4a6')
        panel.set_image(np.zeros((480, 640, 3), dtype=np.uint8))
        panel._set_roi((30, 40, 250, 300), emit=False)
        assert panel.bounds == (30, 40, 250, 300)
        assert isinstance(panel.roi_box, QtWidgets.QGraphicsRectItem)
        assert not isinstance(panel.roi_box, pg.ROI)
        assert panel.vbox.state['mouseEnabled'] == [True, True]
        panel.vbox.reset_requested.emit()
        assert panel.bounds is None
        # Only one view button per camera, with correct split/full state.
        assert panel.view_toggle.text() == 'Expand view'
        panel.set_view_mode('left')
        assert panel.view_toggle.text() == 'Both cameras'
        panel.set_view_mode('both')
        assert panel.view_toggle.text() == 'Expand view'
        # Three-digit rendering: decimals and units automatically scale.
        for value, expected in [(12.34, ('123', 2, '', 'ms')),
                                (0.125, ('125', 0, '', 'ms')),
                                (1234, ('123', 1, '', 's'))]:
            assert format_three_digits(value) == expected, (value, format_three_digits(value))
        instrument = SevenSegmentDisplay('AVERAGE DELAY')
        instrument.set_milliseconds(12.34)
        assert instrument.digits == '123' and instrument.decimal == 2
        smoother = ExponentialSmoother(time_constant_seconds=1.5)
        assert smoother.update(0.0, 0.0) == 0.0
        midway = smoother.update(10.0, 1.5)
        assert 6.0 < midway < 6.5, midway
        assert smoother.update(10.0, 1.5) == midway
        axis = DelayAxis()
        axis.update_for_range(0, 500)
        assert axis.unit == 'ms' and axis.tickStrings([250], 1, 50) == ['250']
        axis.update_for_range(0, 2400)
        assert axis.unit == 's' and axis.tickStrings([1500], 1, 100) == ['1.50']
        chip = StatusChip('TEST')
        chip.display('good','READY')
        assert 'READY' in chip.text()
        bar = TelemetryBar()
        bar.update_fields(age='125 ms',crc='1')
        assert bar.fields['age'].value.text() == '125 ms'
        cards = KpiCard('AVERAGE DELAY', 'Last 100', led_color=COLORS['accent'])
        cards.set_value(12.3)
        assert cards.readout.digits == '123'
        p95_card = KpiCard('DELAY P95', 'Last 100', led_color=COLORS['accent'])
        p95_card.set_value(value_ms=245.0)
        assert p95_card.readout is not None and p95_card.readout.digits == '245'
        diagnostics = DiagnosticsPanel()
        assert not diagnostics.contents.isVisible()
        diagnostics.update_counts(12, 1, 2)
        assert 'Invalid 1' in diagnostics.crc_summary.text()
        assert diagnostics.telemetry.fields['crc'].name.text() == 'CRC VALID'
        chart = LatencyChart(DelayAxis)
        chart.update_samples([1.0, 2.0, 3.0], [0.1, 0.2, 0.3])
        assert chart._display_count == 3
        print('PASS: Qt control-room widgets, ROI, seven-segment, smoothing, and chart')
        return 0
    studio = Studio(args)
    studio.showMaximized()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
