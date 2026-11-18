#!/usr/bin/env python3
"""Stereo Vision Studio - PySide6 + PyQtGraph client (experimental branch).

Uses the SAME proven GStreamer/multicast relay, NTP correction, 168-bit
timestamp decoder, and optional YOLO worker as the Tkinter client.

Install: python -m pip install PySide6 pyqtgraph
Run: python client/stereo_vision_client_qt.py --iface-ip 192.168.0.11
Optional YOLO: --yolo-model C:\models\best.pt --yolo-device 0
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

# pyqtgraph uses [row, column, channel] throughout this client.
pg.setConfigOptions(imageAxisOrder='row-major', antialias=True, background='#111925',
                    foreground='#dae8fa')


def fmt_roi(roi):
    if roi is None:
        return 'FULL FRAME'
    x1, y1, x2, y2 = roi
    return f'{x1},{y1}  {x2-x1} x {y2-y1}'


class DrawViewBox(pg.ViewBox):
    """Interactive mouse drag to select a new ROI in camera pixel coordinates."""
    selected = QtCore.Signal(tuple)

    def __init__(self):
        super().__init__(enableMenu=False, lockAspect=True, invertY=True)
        self.drawing = False
        self.start = None
        self.preview = QtWidgets.QGraphicsRectItem()
        self.preview.setPen(pg.mkPen('#ffcb6b', width=3))
        self.preview.setBrush(pg.mkBrush(255, 200, 80, 25))
        self.addItem(self.preview, ignoreBounds=True)
        self.preview.hide()

    def begin_roi(self):
        self.drawing = True
        self.start = None
        self.preview.hide()
        self.setMouseEnabled(x=False, y=False)

    def end_roi(self):
        self.drawing = False
        self.start = None
        self.preview.hide()
        self.setMouseEnabled(x=True, y=True)

    def mouseDragEvent(self, event, axis=None):
        if not self.drawing or event.button() != QtCore.Qt.MouseButton.LeftButton:
            super().mouseDragEvent(event, axis)
            return
        event.accept()
        p = self.mapSceneToView(event.scenePos())
        if event.isStart():
            sp = self.mapSceneToView(event.buttonDownScenePos())
            self.start = (sp.x(), sp.y())
        if self.start is None:
            return
        x0, y0 = self.start
        x1, y1 = p.x(), p.y()
        self.preview.setRect(QtCore.QRectF(QtCore.QPointF(x0, y0),
                                           QtCore.QPointF(x1, y1)).normalized())
        self.preview.show()
        if event.isFinish():
            self.selected.emit((x0, y0, x1, y1))
            self.end_roi()


class CameraPanel(QtWidgets.QFrame):
    roi_changed = QtCore.Signal(str, object)

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
        layout.setContentsMargins(10, 8, 10, 8)
        hdr = QtWidgets.QHBoxLayout()
        self.title = QtWidgets.QLabel(f'{side} CAMERA')
        self.title.setObjectName('cameraTitle')
        self.detect_count = QtWidgets.QLabel('0 detections')
        self.detect_count.setObjectName('minor')
        hdr.addWidget(self.title)
        hdr.addStretch()
        hdr.addWidget(self.detect_count)
        layout.addLayout(hdr)

        self.graphics = pg.GraphicsLayoutWidget()
        self.vbox = DrawViewBox()
        self.graphics.addItem(self.vbox)
        self.image_item = pg.ImageItem(axisOrder='row-major')
        self.vbox.addItem(self.image_item)
        self.box_items = []
        self.vbox.selected.connect(self._new_selection)
        layout.addWidget(self.graphics, stretch=1)

        controls = QtWidgets.QHBoxLayout()
        self.pick = QtWidgets.QPushButton(f'Select {side} ROI')
        self.pick.clicked.connect(self.start_selection)
        self.reset = QtWidgets.QPushButton('Reset ROI')
        self.reset.clicked.connect(self.clear_roi)
        self.info = QtWidgets.QLabel('FULL FRAME')
        self.info.setObjectName('minor')
        controls.addWidget(self.pick)
        controls.addWidget(self.reset)
        controls.addWidget(self.info, stretch=1)
        layout.addLayout(controls)

    def set_image(self, bgr):
        h, w = bgr.shape[:2]
        self.native_shape = (w, h)
        rgb = np.ascontiguousarray(bgr[:, :, ::-1])
        self.image_item.setImage(rgb, autoLevels=False)
        if not self._has_image:
            self._has_image = True
            self.vbox.setRange(xRange=(0, w), yRange=(0, h), padding=0.01)

    def start_selection(self):
        if not self._has_image:
            return
        self.pick.setText('Drag on image...')
        self.vbox.begin_roi()

    def _new_selection(self, corners):
        self.pick.setText(f'Select {self.side} ROI')
        x0, y0, x1, y1 = corners
        w, h = self.native_shape
        left = max(0, min(w, int(min(x0, x1))))
        top = max(0, min(h, int(min(y0, y1))))
        right = max(0, min(w, int(max(x0, x1))))
        bottom = max(0, min(h, int(max(y0, y1))))
        if right-left >= 16 and bottom-top >= 16:
            self._set_roi((left, top, right, bottom), emit=True)

    def clear_roi(self):
        self.vbox.end_roi()
        self.pick.setText(f'Select {self.side} ROI')
        self._set_roi(None, emit=True)

    def _set_roi(self, bounds, emit):
        self.bounds = bounds
        if self.roi_box is not None:
            self.vbox.removeItem(self.roi_box)
            self.roi_box = None
        if bounds is not None:
            x1, y1, x2, y2 = bounds
            self.roi_box = pg.RectROI((x1, y1), (x2-x1, y2-y1),
                                      pen=pg.mkPen(self.color, width=3),
                                      movable=True, rotatable=False,
                                      resizable=True,
                                      sideScalers=False)
            self.roi_box.addScaleHandle((1, 1), (0, 0))
            self.roi_box.addScaleHandle((0, 0), (1, 1))
            self.vbox.addItem(self.roi_box)
            self.roi_box.sigRegionChangeFinished.connect(self._roi_dragged)
        self.info.setText(fmt_roi(bounds))
        if emit:
            self.roi_changed.emit(self.side, bounds)

    def _roi_dragged(self):
        if self.roi_box is None:
            return
        pos = self.roi_box.pos()
        size = self.roi_box.size()
        w, h = self.native_shape
        x1 = max(0, min(w, int(round(pos.x()))))
        y1 = max(0, min(h, int(round(pos.y()))))
        x2 = max(0, min(w, int(round(pos.x()+size.x()))))
        y2 = max(0, min(h, int(round(pos.y()+size.y()))))
        if x2-x1 < 16 or y2-y1 < 16:
            return
        self.bounds = (x1, y1, x2, y2)
        self.info.setText(fmt_roi(self.bounds))
        self.roi_changed.emit(self.side, self.bounds)

    def set_detections(self, detections):
        for item in self.box_items:
            self.vbox.removeItem(item)
        self.box_items = []
        count = 0
        for det in detections:
            if det.side != self.side:
                continue
            count += 1
            x1, y1, x2, y2 = det.xyxy
            rect = QtWidgets.QGraphicsRectItem(x1, y1, max(1, x2-x1), max(1, y2-y1))
            rect.setPen(pg.mkPen(self.color, width=3))
            self.vbox.addItem(rect, ignoreBounds=True)
            self.box_items.append(rect)
            lbl = pg.TextItem(f'{det.label} {det.confidence:.0%}',
                              color='#101820', fill=pg.mkBrush(self.color),
                              border=pg.mkPen(self.color))
            lbl.setPos(x1, max(0, y1-8))
            self.vbox.addItem(lbl, ignoreBounds=True)
            self.box_items.append(lbl)
        self.detect_count.setText(f'{count} detections')


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
        self.age_samples = collections.deque(maxlen=300)
        self.timestamps = collections.deque(maxlen=300)
        self.clock_offset = args.clock_offset_ms
        self.clock_status = 'not measured'
        self.latest_stamp = None
        self.view_mode = 'both'
        self._last_overlay_id = -1
        self._last_overlay_mono = 0
        self.setWindowTitle('AI Stereo Vision Studio  |  PySide6 + PyQtGraph')
        self.setMinimumSize(960, 650)
        self._build()
        self._start_services()

    def _build(self):
        self.setStyleSheet("""
          QMainWindow, QWidget { background:#101723; color:#e7f0fa;
                                font-family:'Segoe UI'; font-size:13px; }
          QFrame#cameraPanel { background:#182434; border:1px solid #324254;
                               border-radius:10px; }
          QLabel#cameraTitle { color:#92cbff; font-weight:700; font-size:15px; }
          QLabel#minor { color:#9bb0c6; font-size:11px; }
          QPushButton { background:#283a51; color:#edf6ff; border:1px solid #4e6987;
                        padding:7px 12px; border-radius:6px; }
          QPushButton:hover { background:#345779; }
          QLabel#status { color:#b0f1cb; font-weight:600; }
        """)
        root = QtWidgets.QWidget()
        main = QtWidgets.QVBoxLayout(root)
        main.setContentsMargins(12, 10, 12, 10)
        main.setSpacing(8)
        header = QtWidgets.QHBoxLayout()
        brand = QtWidgets.QLabel('◉  AI STEREO VISION STUDIO')
        brand.setStyleSheet('font-size:20px; font-weight:700; color:#eaf6ff;')
        header.addWidget(brand)
        header.addStretch()
        self.stream_status = QtWidgets.QLabel('CONNECTING')
        self.stream_status.setObjectName('status')
        header.addWidget(self.stream_status)
        main.addLayout(header)

        toolbar = QtWidgets.QHBoxLayout()
        self.both_btn = QtWidgets.QPushButton('Both Cameras [1]')
        self.left_btn = QtWidgets.QPushButton('Left [2]')
        self.right_btn = QtWidgets.QPushButton('Right [3]')
        for button, mode in ((self.both_btn,'both'),(self.left_btn,'left'),
                             (self.right_btn,'right')):
            button.clicked.connect(lambda checked=False, v=mode: self.change_view(v))
            toolbar.addWidget(button)
        self.yolo_text = QtWidgets.QLabel('YOLO disabled')
        self.yolo_text.setObjectName('minor')
        toolbar.addStretch()
        toolbar.addWidget(self.yolo_text)
        main.addLayout(toolbar)

        self.cameras = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.left_panel = CameraPanel('LEFT', '#70e4a6')
        self.right_panel = CameraPanel('RIGHT', '#ff9fc3')
        self.left_panel.roi_changed.connect(self._roi_changed)
        self.right_panel.roi_changed.connect(self._roi_changed)
        self.cameras.addWidget(self.left_panel)
        self.cameras.addWidget(self.right_panel)
        self.cameras.setSizes([700,700])
        main.addWidget(self.cameras, stretch=7)

        telemetry = QtWidgets.QFrame()
        telemetry.setObjectName('cameraPanel')
        telemetry_layout = QtWidgets.QVBoxLayout(telemetry)
        self.stats = QtWidgets.QLabel('Waiting for video / clock alignment...')
        self.stats.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        telemetry_layout.addWidget(self.stats)
        self.binary = QtWidgets.QLabel()
        self.binary.setFixedHeight(31)
        self.binary.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft |
                                 QtCore.Qt.AlignmentFlag.AlignVCenter)
        telemetry_layout.addWidget(self.binary)
        self.bar_label = QtWidgets.QLabel('168-bit binary UTC timestamp + CRC16 (original source pixels)')
        self.bar_label.setObjectName('minor')
        telemetry_layout.addWidget(self.bar_label)
        main.addWidget(telemetry, stretch=0)

        self.chart = pg.PlotWidget()
        self.chart.setMinimumHeight(120)
        self.chart.setMaximumHeight(170)
        self.chart.setLabel('left', 'Frame age', units='ms')
        self.chart.setLabel('bottom', 'Recent decoded frames')
        self.chart.showGrid(x=True, y=True, alpha=0.15)
        self.chart.addLegend(offset=(10, 10))
        self.age_curve = self.chart.plot(pen=pg.mkPen('#ffd56d', width=2),
                                         name='Corrected frame age (ms)')
        main.addWidget(self.chart, stretch=1)
        self.setCentralWidget(root)
        self.statusBar().showMessage('1 Both  ·  2 Left  ·  3 Right  ·  F Fullscreen  ·  Esc Windowed')

        for key, mode in (('1','both'),('2','left'),('3','right')):
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(key), self)
            shortcut.activated.connect(lambda v=mode: self.change_view(v))
        shortcut = QtGui.QShortcut(QtGui.QKeySequence('F'), self)
        shortcut.activated.connect(self.toggle_fullscreen)
        escape = QtGui.QShortcut(QtGui.QKeySequence('Escape'), self)
        escape.activated.connect(self.showMaximized)

    def toggle_fullscreen(self):
        self.showMaximized() if self.isFullScreen() else self.showFullScreen()

    def change_view(self, mode):
        self.view_mode = mode
        self.left_panel.setVisible(mode != 'right')
        self.right_panel.setVisible(mode != 'left')

    def _roi_changed(self, side, bounds):
        if self.yolo:
            self.yolo.set_roi(side, bounds)
        self.statusBar().showMessage(f'{side} ROI: {fmt_roi(bounds)}', 5000)

    def _start_services(self):
        if not self.args.no_time_sync:
            try:
                offset, rtt, samples = measure_ntp_offset(
                    self.args.time_server, self.args.ntp_port, self.args.ntp_samples)
                self.clock_offset += offset
                self.clock_status = f'Clock +{offset:+.1f} ms; RTT {rtt:.1f} ms ({samples} samples)'
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
            self.stream_status.setText(f'STREAM ERROR: {exc}')
            self.timer.stop()
            return
        frame, recv_ns, frame_id = self.reader.latest()
        if frame is None or frame_id == self.last_count:
            return
        self.last_count = frame_id
        source_h = max(1, frame.shape[0]-100)
        half = frame.shape[1]//2
        self.left_panel.set_image(frame[:source_h, :half])
        self.right_panel.set_image(frame[:source_h, half:])
        if self.yolo:
            self.yolo.submit(frame, frame_id)
            detections, status, result_id, result_mono, ms, error = self.yolo.snapshot()
            self.yolo_text.setText(status[:110])
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
            if self.last_seq is not None:
                delta = (stamp.sequence-self.last_seq)%(1<<32)
                if 1 < delta < 10000:
                    self.missing += delta-1
            self.last_seq = stamp.sequence
            self.latest_stamp = stamp
            self.stats.setText(
                f'CRC OK {self.valid} | Invalid {self.invalid} | Skipped {self.missing}'
                f'    •    LEFT {iso_utc(stamp.left_utc_ms)}'
                f'    •    RIGHT {iso_utc(stamp.right_utc_ms)}'
                f'    •    Age {age:.0f} ms'
                f'    •    Skew {stamp.right_minus_left_us/1000:+.1f} ms'
                f'    •    {self.clock_status}')
            self.age_curve.setData(np.arange(len(self.age_samples)),
                                   np.array(self.age_samples))
        else:
            self.invalid += 1
            self.stats.setText(f'Invalid embedded timestamp: {err}  |  CRC OK {self.valid}')
        y = frame.shape[0]-27
        bars = frame[y:y+16, 12:12+168*6, ::-1]
        if bars.shape == (16, 1008, 3):
            # Copy to avoid lifetime / stride issues; scale only the UI.
            arr = np.ascontiguousarray(bars)
            qimage = QtGui.QImage(arr.data, 1008, 16, 1008*3,
                                  QtGui.QImage.Format.Format_RGB888).copy()
            pixmap = QtGui.QPixmap.fromImage(qimage).scaled(
                min(max(1008, self.binary.width()-10), 1800), 28,
                QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
                QtCore.Qt.TransformationMode.FastTransformation)
            self.binary.setPixmap(pixmap)
        self.stream_status.setText(
            f'● MULTICAST  {self.reader.relay.packets if self.reader.relay else frame_id} UDP'
        )

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
    return p


def main():
    p = build_parser()
    args = p.parse_args()
    if args.transport == 'multicast' and not args.iface_ip:
        p.error('Multicast requires --iface-ip LOCAL_ETHERNET_IP')
    if not 0 < args.yolo_conf <= 1:
        p.error('YOLO confidence must be in (0,1]')
    app = QtWidgets.QApplication(sys.argv[:1])
    app.setStyle('Fusion')
    studio = Studio(args)
    studio.showMaximized()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
