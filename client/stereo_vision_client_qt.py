#!/usr/bin/env python3
"""Stereo Vision Studio - PySide6 + PyQtGraph client.

Uses the SAME proven GStreamer/multicast relay, NTP correction, 168-bit
timestamp decoder, and optional YOLO worker as the Tkinter client.

Install: python -m pip install PySide6 pyqtgraph
Run: python client/stereo_vision_client_qt.py --iface-ip 192.168.0.11
Optional YOLO: --yolo-model C:/models/best.pt --yolo-device 0
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import dataclasses
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'common'))

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from receiver import StreamReceiver
from stamp_protocol import decode_strip, iso_utc, split_stereo, video_height
from yolo_detector import Detection, YoloState, YoloWorker
from lan_ntp import measure_ntp_offset
from seven_segment import SevenSegmentDisplay, format_three_digits
from timing_filter import ExponentialSmoother
from image_adjustments import (
    ImageAdjustmentsDialog, ImageSettings, build_lut,
    apply_to_stereo_frame, load_settings, save_settings,
)
from detection_annotations import (
    PRESETS, AnnotationOptions, AnnotationPopover, format_detection_label,
    load_annotation_options, next_preset, preset_of, save_annotation_options,
)
from icons import icon, pixmap
from theme import (COLORS, THRESHOLDS, apply_theme, class_color, display_font,
                   numeric_font, quality, tint, ui_font)
from status_limits import LimitsDialog, load_thresholds, save_thresholds
from control_room_widgets import (
    AnalysisCard, StatusChip, TelemetryBar, KpiCard,
    DiagnosticsPanel, LatencyChart, DelayAxis, StateOverlay, set_state,
)

# pyqtgraph uses [row, column, channel] throughout this client.
pg.setConfigOptions(imageAxisOrder='row-major', antialias=True,
                    background=COLORS['surface'], foreground=COLORS['secondary'])

APP_MARGIN = 12
TILE_HEADER_H = 40
DASHBOARD_MIN_H = 230
DASHBOARD_MAX_H = 430
DETECTIONS_EXTRA_H = 70


def fmt_roi(roi):
    if roi is None:
        return 'Full frame'
    x1, y1, x2, y2 = roi
    return f'ROI {x2-x1} × {y2-y1}'


class DrawViewBox(pg.ViewBox):
    """Mouse-first camera: left drag ROI, right drag pan, wheel zoom, double click reset."""
    selected = QtCore.Signal(tuple)
    reset_requested = QtCore.Signal()

    def __init__(self):
        super().__init__(enableMenu=False, lockAspect=True, invertY=True)
        self.start = None
        self.preview = QtWidgets.QGraphicsRectItem()
        self.preview.setPen(pg.mkPen(COLORS['roi'], width=1.5, style=QtCore.Qt.PenStyle.DashLine))
        self.preview.setBrush(pg.mkBrush(tint(COLORS['roi'], 28)))
        self.preview.setZValue(100)
        self.preview.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
        self.addItem(self.preview, ignoreBounds=True)
        self.preview.hide()
        # Live size readout while dragging, in native camera pixels.
        self.preview_label = pg.TextItem('', color=COLORS['background'],
                                         fill=pg.mkBrush(COLORS['roi']), anchor=(0, 1))
        self.preview_label.setFont(numeric_font(12, QtGui.QFont.Weight.DemiBold))
        self.preview_label.setZValue(101)
        self.addItem(self.preview_label, ignoreBounds=True)
        self.preview_label.hide()
        self.setMouseEnabled(x=True, y=True)
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)

    def cancel_preview(self):
        self.preview.hide()
        self.preview_label.hide()
        self.start = None

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
        rect = QtCore.QRectF(QtCore.QPointF(x0, y0), QtCore.QPointF(x1, y1)).normalized()
        self.preview.setRect(rect)
        self.preview.show()
        self.preview_label.setText(f' {int(rect.width())} × {int(rect.height())} px ')
        self.preview_label.setPos(rect.left(), rect.top())
        self.preview_label.show()
        if event.isFinish():
            self.cancel_preview()
            self.selected.emit((x0, y0, x1, y1))

    def mouseClickEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            event.accept()
            if event.double():
                self.cancel_preview()
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
    HINT = ('<b>Drag</b> set ROI &nbsp;·&nbsp; <b>Right-drag</b> pan &nbsp;·&nbsp; '
            '<b>Wheel</b> zoom &nbsp;·&nbsp; <b>Double-click</b> clear ROI')
    ROI_HELP = ('YOLO region of interest, in native camera pixels.\n'
                'Drag on the image to set it; double-click to clear it.')
    HUD_HELP = 'Received/decoded stereo FPS (not sensor FPS) · latest frame delay'

    def __init__(self, side, color, parent=None):
        super().__init__(parent)
        self.side = side
        self.color = color
        self.native_shape = (1920, 1080)
        self.roi_box = None
        self.bounds = None
        self._updating = False
        self._has_image = False
        self._stale = False
        self._waiting_text = 'Waiting for the stream'
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
        self.detect_count = QtWidgets.QLabel('No objects')
        self.detect_count.setObjectName('pill')
        self.detect_count.setFont(ui_font(12))
        self.info = QtWidgets.QLabel('Full frame')
        self.info.setObjectName('pill')
        self.info.setFont(ui_font(12))
        self.info.setToolTip(self.ROI_HELP)
        self.roi_clear = QtWidgets.QToolButton()
        self.roi_clear.setIcon(icon('close', COLORS['secondary'], 12))
        self.roi_clear.setIconSize(QtCore.QSize(12, 12))
        self.roi_clear.setToolTip('Clear the ROI · YOLO uses the full frame again')
        self.roi_clear.clicked.connect(self.clear_roi)
        self.roi_clear.hide()
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
        for widget in (badge, self.title):
            hdr.addWidget(widget)
        hdr.addSpacing(4)
        hdr.addWidget(self.detect_count)
        hdr.addWidget(self.info)
        hdr.addWidget(self.roi_clear)
        hdr.addStretch()
        hdr.addWidget(self.hud_label)
        hdr.addSpacing(4)
        hdr.addWidget(self.fit_button)
        hdr.addWidget(self.view_toggle)
        layout.addWidget(header)
        self._header_widgets = (badge, self.title, self.detect_count, self.info, self.roi_clear,
                                self.hud_label, self.fit_button, self.view_toggle)

        video = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(video)
        grid.setContentsMargins(0, 0, 0, 0)
        self.graphics = pg.GraphicsLayoutWidget()
        self.graphics.setBackground(COLORS['video'])
        self.graphics.ci.setContentsMargins(0, 0, 0, 0)
        self.vbox = DrawViewBox()
        self.graphics.addItem(self.vbox)
        self.image_item = pg.ImageItem(axisOrder='row-major')
        self.vbox.addItem(self.image_item)
        self.box_items = []
        self.box_labels = []
        self.annotations = AnnotationOptions()
        self._last_detections = ()
        # Centre crosshairs keep a constant on-screen size at any zoom.
        self.center_markers = pg.ScatterPlotItem(pxMode=True)
        self.center_markers.setZValue(23)
        self.vbox.addItem(self.center_markers, ignoreBounds=True)
        self.center_markers.setVisible(False)
        # Outside-ROI shading makes it obvious what YOLO does NOT look at.
        self.roi_mask = QtWidgets.QGraphicsPathItem()
        self.roi_mask.setBrush(pg.mkBrush(0, 0, 0, 125))
        self.roi_mask.setPen(QtGui.QPen(QtCore.Qt.PenStyle.NoPen))
        self.roi_mask.setZValue(50)
        self.roi_mask.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
        self.vbox.addItem(self.roi_mask, ignoreBounds=True)
        self.roi_mask.hide()
        self.roi_tag = pg.TextItem('ROI', color=COLORS['background'],
                                   fill=pg.mkBrush(COLORS['roi']), anchor=(0, 1))
        self.roi_tag.setFont(ui_font(11, QtGui.QFont.Weight.Bold))
        self.roi_tag.setZValue(56)
        self.vbox.addItem(self.roi_tag, ignoreBounds=True)
        self.roi_tag.hide()
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
        self.vbox.selected.connect(self._new_selection)
        self.vbox.reset_requested.connect(self.clear_roi)
        self.vbox.sigRangeChanged.connect(self._range_changed)
        self.overlay.show_state('info', '◌', 'Waiting for video', self._waiting_text)
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
        """On narrow tiles drop the live HUD first, then the title (the L/R badge stays)."""
        if not self.isVisible():
            return  # unlaid-out headers report a meaningless width
        layout = self.header.layout()
        margins = layout.contentsMargins()
        available = self.header.width() - margins.left() - margins.right()
        for widget in (self.hud_label, self.title):
            widget.setVisible(True)
        for widget in (self.hud_label, self.title):
            shown = [w for w in self._header_widgets if not w.isHidden()]
            needed = (sum(w.sizeHint().width() for w in shown)
                      + layout.spacing() * (len(shown) - 1) + 8)
            if needed <= available:
                break
            widget.setVisible(False)

    def set_waiting_text(self, text):
        self._waiting_text = text
        if not self._has_image:
            self.overlay.show_state('info', '◌', 'Waiting for video', text)

    def set_detections_enabled(self, enabled):
        self.detect_count.setVisible(enabled)

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
        self.view_toggle.setIcon(icon('split' if expanded else 'expand', COLORS['secondary'], 14))
        shortcut = '1' if expanded else ('2' if self.side == 'LEFT' else '3')
        self.view_toggle.setToolTip(
            f'{"Return to both cameras" if expanded else "Expand the " + self.side.lower() + " camera"} · shortcut {shortcut}')
        self._fit_header()

    def set_image(self, bgr):
        h, w = bgr.shape[:2]
        reshaped = (w, h) != self.native_shape
        self.native_shape = (w, h)
        rgb = np.ascontiguousarray(bgr[:, :, ::-1])
        self.image_item.setImage(rgb, autoLevels=False)
        if not self._has_image or reshaped:
            self._has_image = True
            self.hud_label.setToolTip(f'{w} × {h} native resolution\n{self.HUD_HELP}')
            self.stale_cover.setRect(0, 0, w, h)
            self.fit_view()
            if self.bounds is not None:
                self._set_roi(self.bounds, emit=False)
            self.overlay.hide()

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
        whole_image = x0 <= tolerance and y0 <= tolerance and x1 >= w - tolerance and y1 >= h - tolerance
        far_out = (x1 - x0) > 1.6 * w and (y1 - y0) > 1.6 * h
        self.fit_button.setVisible(not whole_image or far_out)

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
        self.vbox.cancel_preview()
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
            outline.setPen(pg.mkPen(COLORS['roi'], width=2))
            outline.setBrush(QtGui.QBrush(QtCore.Qt.BrushStyle.NoBrush))
            outline.setZValue(55)
            outline.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
            self.vbox.addItem(outline, ignoreBounds=True)
            self.roi_box = outline
            w, h = self.native_shape
            path = QtGui.QPainterPath()
            path.setFillRule(QtCore.Qt.FillRule.OddEvenFill)
            path.addRect(0, 0, w, h)
            path.addRect(x1, y1, x2-x1, y2-y1)
            self.roi_mask.setPath(path)
            self.roi_mask.show()
            self.roi_tag.setText(f' ROI  {x2-x1} × {y2-y1} ')
            self.roi_tag.setAnchor((0, 1) if y1 > 0.04 * h else (0, 0))
            self.roi_tag.setPos(x1, y1)
            self.roi_tag.show()
        else:
            self.roi_mask.hide()
            self.roi_tag.hide()
        self.info.setText(fmt_roi(bounds))
        if bounds is not None:
            x1, y1, x2, y2 = bounds
            self.info.setToolTip(f'YOLO region of interest: {x2-x1} × {y2-y1} px at ({x1}, {y1}),\n'
                                 'in native camera pixels. Drag on the image to replace it;\n'
                                 'double-click the image or press × to clear it.')
        else:
            self.info.setToolTip(self.ROI_HELP)
        set_state(self.info, 'active' if bounds is not None else '')
        self.roi_clear.setVisible(bounds is not None)
        self._fit_header()
        if emit:
            self.roi_changed.emit(self.side, bounds)

    def set_stale(self, age_s):
        """Waiting / stalled / no-signal presentation, from seconds since the last frame."""
        t = THRESHOLDS
        stale = age_s is not None and age_s >= t.stale_after_s
        lost = age_s is not None and age_s >= t.stream_disconnected_after_s
        self._stale = stale
        self.stale_cover.setVisible(stale and self._has_image)
        if stale:
            # Detections on a frozen frame would be misleading on a moving belt.
            for item in self.box_items + self.box_labels + [self.center_markers]:
                item.setVisible(False)
        if age_s is None and not self._has_image:
            self.overlay.show_state('info', '◌', 'Waiting for video', self._waiting_text)
        elif lost:
            self.overlay.show_state('bad', '✕', 'No signal',
                                    f'Last frame {age_s:.1f} s ago · showing the last image')
        elif stale:
            self.overlay.show_state('warning', '▲', 'Stream stalled', f'Last frame {age_s:.1f} s ago')
        else:
            self.overlay.hide()

    def set_hud(self, fps, age_ms):
        label = f'{fps:.1f} fps'
        if age_ms is not None:
            label += f' · {age_ms:.0f} ms'
        previous = self.hud_label.text()
        if previous != label:
            self.hud_label.setText(label)
            if len(previous) != len(label):
                self._fit_header()

    def _data_per_pixel(self):
        """Image pixels per screen pixel at the current zoom (0 before the view exists)."""
        try:
            return self.vbox.viewPixelSize()[0]
        except (TypeError, AttributeError, ZeroDivisionError):
            return 0.0

    def set_annotation_options(self, options: AnnotationOptions):
        """Redraw the latest detections at once, without waiting for a new frame."""
        self.annotations = options
        self.set_detections(self._last_detections)

    def set_detections(self, detections):
        self._last_detections = tuple(detections)
        options = self.annotations
        selected = [] if self._stale else [d for d in detections if d.side == self.side]
        drawn = selected if options.show else []
        while len(self.box_items) < len(drawn):
            rect = QtWidgets.QGraphicsRectItem()
            rect.setZValue(21)
            rect.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
            self.vbox.addItem(rect, ignoreBounds=True)
            self.box_items.append(rect)
            label = pg.TextItem('', color=COLORS['background'],
                                fill=pg.mkBrush(COLORS['accent']), anchor=(0, 1))
            label.setFont(ui_font(12, QtGui.QFont.Weight.DemiBold))
            label.setZValue(22)
            self.vbox.addItem(label, ignoreBounds=True)
            self.box_labels.append(label)
        w, h = self.native_shape
        data_per_pixel = self._data_per_pixel()
        for i, rect in enumerate(self.box_items):
            label = self.box_labels[i]
            if i >= len(drawn):
                rect.setVisible(False)
                label.setVisible(False)
                continue
            d = drawn[i]
            color = class_color(d.label)
            x1, y1, x2, y2 = d.xyxy
            rect.setPen(pg.mkPen(color, width=2.5))
            rect.setRect(x1, y1, max(1, x2-x1), max(1, y2-y1))
            rect.setVisible(True)
            text = format_detection_label(d, options)
            label.setVisible(bool(text))
            if text:
                label.fill = pg.mkBrush(color)
                label.setText('\n'.join(f' {line} ' for line in text.split('\n')))
                # Labels keep a fixed on-screen size; if one would run past the
                # image's right edge, align it to the box's right edge instead.
                label_w = label.textItem.boundingRect().width() * data_per_pixel
                flip = x1 + label_w > w and x2 - label_w >= 0
                label.setAnchor((1 if flip else 0, 1 if y1 > 0.04 * h else 0))
                label.setPos(x2 if flip else x1, y1)
        if options.center_marker and drawn:
            self.center_markers.setData(
                pos=[d.center for d in drawn], symbol='crosshair', size=17,
                pen=pg.mkPen(COLORS['background'], width=1),
                brush=[pg.mkBrush(class_color(d.label)) for d in drawn])
            self.center_markers.setVisible(True)
        elif self.center_markers.isVisible():
            self.center_markers.setVisible(False)
        count = len(selected)
        text = 'No objects' if not count else ('1 object' if count == 1 else f'{count} objects')
        if self.detect_count.text() != text:
            self.detect_count.setText(text)
            classes = collections.Counter(d.label for d in selected)
            breakdown = '\n'.join(f'{name} × {n}' for name, n in classes.most_common())
            self.detect_count.setToolTip((breakdown or 'No detections in the latest result')
                                         + '\n\nLabel details: Labels button or shortcut L')
            set_state(self.detect_count, 'active' if count else '')
            self._fit_header()


class Studio(QtWidgets.QMainWindow):
    def __init__(self, args):
        super().__init__()
        apply_theme(QtWidgets.QApplication.instance())
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
        self.last_detection_count = 0
        self.sync_rtt = None
        self.sync_sample_count = 0
        self.last_error_count = 0
        self.last_error_mono = 0.0
        self.last_decode_error = None
        self.stream_error = None
        # (monotonic time, count) of invalid or lost frames, for the Data pill.
        self.recent_errors = collections.deque(maxlen=4096)
        self.last_packet_count = 0
        self.last_packet_mono = time.monotonic()
        self.packet_rate = 0.0
        self.recv_fps = 0.0
        # Per-workstation preferences: image adjustments and detection labels.
        self._preferences = QtCore.QSettings('AIEngineering', 'StereoVisionStudio')
        self.image_settings = load_settings(self._preferences)
        self._image_lut = build_lut(self.image_settings)
        self.image_dialog = None
        self.annotation_options = load_annotation_options(self._preferences)
        self.annotation_popover = None
        # Active OK / Watch / High limits; theme.THRESHOLDS holds only the defaults.
        self.thresholds = load_thresholds(self._preferences)
        self.last_detections = ()
        # The raw, signed R-L skew remains unchanged in stats/logs.
        # The instrument uses a 1.5-second time-based EMA.
        self.skew_smoother = ExponentialSmoother(time_constant_seconds=1.5)
        self.last_instrument_update = 0.0
        self.timestamps = collections.deque(maxlen=300)
        self.clock_offset = args.clock_offset_ms
        self.clock_status = 'not measured'
        self.last_clock_sync_mono = None
        self.clock_sync_error = None
        self._sync_future = None
        self._sync_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix='stereo-ntp')
        self.latest_stamp = None
        self.view_mode = 'both'
        self._equalize_pending = False
        self._last_overlay_id = -1
        self._last_overlay_mono = 0
        self.setWindowTitle('Stereo Vision Studio · AI Engineering')
        self.setMinimumSize(1024, 640)
        self._layout_timer = QtCore.QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.setInterval(0)
        self._layout_timer.timeout.connect(self._fit_layout)
        self._build()
        self._start_services()

    def _listen_text(self):
        a = self.args
        if a.transport == 'multicast':
            return f'Listening on {a.multicast_group}:{a.port} via {a.iface_ip}'
        return f'Listening for unicast UDP on port {a.port}'

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
        logo.setToolTip('Stereo Vision Studio · AI Engineering')
        # The brand text collapses on narrow windows so status pills keep their room.
        self.brand_text = QtWidgets.QWidget()
        brand = QtWidgets.QVBoxLayout(self.brand_text)
        brand.setSpacing(0)
        brand.setContentsMargins(4, 0, 0, 0)
        title = QtWidgets.QLabel('Stereo Vision Studio')
        title.setObjectName('brandTitle')
        title.setFont(ui_font(16, QtGui.QFont.Weight.DemiBold))
        caption = QtWidgets.QLabel('AI Engineering')
        caption.setObjectName('brandCaption')
        caption.setFont(display_font(10))
        brand.addStretch()
        brand.addWidget(title)
        brand.addWidget(caption)
        brand.addStretch()
        row.addWidget(logo)
        row.addWidget(self.brand_text)
        row.addSpacing(24)
        self.stream_chip = StatusChip('Stream', min_width=200)
        self.clock_chip = StatusChip('Clock', min_width=176)
        self.yolo_chip = StatusChip('YOLO', min_width=172)
        self.data_chip = StatusChip('Data', min_width=176)
        for chip in (self.stream_chip, self.clock_chip, self.yolo_chip, self.data_chip):
            chip.set_clickable(True)
            chip.clicked.connect(lambda: self.analysis.show_tab('diagnostics'))
            row.addWidget(chip)
        row.addStretch()
        self.labels_button = QtWidgets.QPushButton('Labels')
        self.labels_button.setIcon(icon('tag', COLORS['secondary']))
        self.image_button = QtWidgets.QPushButton('Adjust image')
        for button in (self.labels_button, self.image_button):
            button.setFont(ui_font(13, QtGui.QFont.Weight.DemiBold))
            button.setIconSize(QtCore.QSize(16, 16))
            button.setFixedHeight(32)
        self.labels_button.clicked.connect(self._toggle_annotation_popover)
        self.image_button.clicked.connect(self._toggle_image_dialog)
        if self.args.yolo_model:
            self.labels_button.setToolTip('What detection labels show: class, ID, confidence, '
                                          'centre, size · shortcut L cycles presets')
        else:
            self.labels_button.setEnabled(False)
            self.labels_button.setToolTip('Detection labels · start with --yolo-model to enable')
        row.addWidget(self.labels_button)
        self.fullscreen_button = QtWidgets.QToolButton()
        self.fullscreen_button.setIconSize(QtCore.QSize(16, 16))
        self.fullscreen_button.setFixedSize(32, 32)
        self.fullscreen_button.clicked.connect(self.toggle_fullscreen)
        row.addWidget(self.image_button)
        row.addWidget(self.fullscreen_button)
        self._set_image_button_active(self._image_lut is not None)
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
        self.left_panel = CameraPanel('LEFT', COLORS['accent'])
        self.right_panel = CameraPanel('RIGHT', COLORS['accent'])
        for panel in (self.left_panel, self.right_panel):
            panel.roi_changed.connect(self._roi_changed)
            panel.view_requested.connect(self.change_view)
            panel.set_waiting_text(self._listen_text())
            panel.set_detections_enabled(bool(self.args.yolo_model))
            panel.set_annotation_options(self.annotation_options)
            self.cameras.addWidget(panel)
        self.cameras.setSizes([700, 700])
        self.cameras.splitterMoved.connect(self._schedule_layout)
        body.addWidget(self.cameras, stretch=1)

        # Dashboard: hero delay KPI over P95/skew, beside the analysis card.
        self.dashboard = QtWidgets.QWidget()
        dash = QtWidgets.QHBoxLayout(self.dashboard)
        dash.setContentsMargins(0, 0, 0, 0)
        dash.setSpacing(APP_MARGIN)
        kpis = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(kpis)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(APP_MARGIN)
        self.average_delay_card = KpiCard('Average delay', 'Waiting for valid frames', max_digit_px=92)
        self.delay_p95_card = KpiCard('Delay P95', 'Last 100 valid frames')
        self.skew_card = KpiCard('Camera skew R−L', 'Smoothed · τ 1.5 s')
        for card in (self.average_delay_card, self.delay_p95_card, self.skew_card):
            card.badge_clicked.connect(self._edit_limits)
        grid.addWidget(self.average_delay_card, 0, 0, 1, 2)
        grid.addWidget(self.delay_p95_card, 1, 0)
        grid.addWidget(self.skew_card, 1, 1)
        grid.setRowStretch(0, 11)
        grid.setRowStretch(1, 9)
        self.analysis = AnalysisCard(DelayAxis, thresholds=self.thresholds)
        self.history = self.analysis.chart
        self.diagnostics = self.analysis.diagnostics
        self.telemetry = self.diagnostics.telemetry
        self.analysis.tab_changed.connect(self._analysis_tab_changed)
        self.analysis.limits_requested.connect(self._edit_limits)
        self.analysis.detections.set_enabled(bool(self.args.yolo_model))
        self._update_limit_tooltips()
        dash.addWidget(kpis, 30)
        dash.addWidget(self.analysis, 70)
        body.addWidget(self.dashboard, stretch=0)
        main.addWidget(self.body, stretch=1)
        self.setCentralWidget(root)
        # Dims the whole window while a modal dialog (status limits) is open.
        self._modal_dim = QtWidgets.QWidget(self)
        self._modal_dim.setObjectName('modalDim')
        self._modal_dim.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        self._modal_dim.hide()
        # No persistent footer: shortcuts are listed in tooltips and the video hint.
        self.statusBar().hide()
        self.change_view('both')

        shortcuts = [('1', lambda: self.change_view('both')),
                     ('2', lambda: self.change_view('left')),
                     ('3', lambda: self.change_view('right')),
                     ('F', self.toggle_fullscreen),
                     ('Escape', self.showMaximized),
                     ('D', self.analysis.toggle_diagnostics),
                     ('P', self.analysis.toggle_pause),
                     ('L', self._cycle_annotation_preset)]
        for key, action in shortcuts:
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(key), self)
            shortcut.activated.connect(action)
        self.health_timer = QtCore.QTimer(self)
        self.health_timer.timeout.connect(self._poll_clock_sync)
        self.health_timer.timeout.connect(self._refresh_metrics)
        self.health_timer.start(100)

    def _set_image_button_active(self, active):
        self.image_button.setProperty('active', 'true' if active else 'false')
        self.image_button.style().unpolish(self.image_button)
        self.image_button.style().polish(self.image_button)
        self.image_button.setIcon(icon('sliders', COLORS['accent'] if active else COLORS['secondary']))
        self.image_button.setToolTip(
            ('Image adjustments are ACTIVE on this workstation.\n' if active else '') +
            'Client-only exposure, brightness, contrast and white balance for the '
            'displayed video and YOLO input')

    def _toggle_image_dialog(self):
        """Show or hide translucent, non-blocking adjustment controls."""
        first_show = self.image_dialog is None
        if first_show:
            self.image_dialog = ImageAdjustmentsDialog(self.image_settings, self)
            self.image_dialog.changed.connect(self._image_settings_changed)
        if self.image_dialog.isVisible():
            self.image_dialog.hide()
            return
        self.image_dialog.show()
        self.image_dialog.raise_()
        # Position once; preserve the operator's dragged position thereafter.
        if first_show:
            self._place_under(self.image_dialog, self.image_button)

    def _place_under(self, popup, button):
        """Open a floating panel under its button, kept fully inside window and screen."""
        popup.adjustSize()
        size = popup.size()
        anchor = button.mapToGlobal(QtCore.QPoint(button.width(), button.height() + 6))
        bounds = self.frameGeometry()
        screen = self.screen()
        if screen is not None:
            bounds = bounds.intersected(screen.availableGeometry())
        x = min(anchor.x() - size.width() + 16, bounds.right() - size.width())
        y = min(anchor.y() - 10, bounds.bottom() - size.height())
        popup.move(max(bounds.left(), x), max(bounds.top(), y))

    def _toggle_annotation_popover(self):
        """Detection label options; a popup that closes on any outside click or Esc."""
        if self.annotation_popover is None:
            self.annotation_popover = AnnotationPopover(self.annotation_options, self)
            self.annotation_popover.changed.connect(self._apply_annotation_options)
        if self.annotation_popover.isVisible():
            self.annotation_popover.close()
            return
        self.annotation_popover.set_options(self.annotation_options)
        self._place_under(self.annotation_popover, self.labels_button)
        self.annotation_popover.show()

    def _apply_annotation_options(self, options, save=True):
        self.annotation_options = options
        for panel in (self.left_panel, self.right_panel):
            panel.set_annotation_options(options)
        if save:
            save_annotation_options(self._preferences, options)

    def _cycle_annotation_preset(self):
        """Shortcut L: Minimal → Standard → Detailed, with brief on-screen feedback."""
        if not self.yolo:
            return
        name = next_preset(self.annotation_options)
        self._apply_annotation_options(PRESETS[name])
        if self.annotation_popover is not None and self.annotation_popover.isVisible():
            self.annotation_popover.set_options(self.annotation_options)
        button = self.labels_button
        QtWidgets.QToolTip.showText(button.mapToGlobal(QtCore.QPoint(0, button.height() + 4)),
                                    f'Labels: {name.title()}', button, QtCore.QRect(), 1500)

    def _image_settings_changed(self, settings: ImageSettings):
        """Rebuild tiny per-channel lookup tables only on slider changes."""
        self.image_settings = settings
        self._image_lut = build_lut(settings)
        save_settings(self._preferences, settings)
        self._set_image_button_active(self._image_lut is not None)

    def toggle_fullscreen(self):
        self.showMaximized() if self.isFullScreen() else self.showFullScreen()

    def _update_fullscreen_button(self):
        full = self.isFullScreen()
        self.fullscreen_button.setIcon(icon('exit_fullscreen' if full else 'fullscreen',
                                            COLORS['secondary']))
        self.fullscreen_button.setToolTip(('Exit full screen · Esc' if full
                                           else 'Full screen · shortcut F'))

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
        # Re-split evenly once the re-shown panel has been laid out.
        self._equalize_pending = mode == 'both'
        self.left_panel.set_view_mode(mode)
        self.right_panel.set_view_mode(mode)
        # The Detections tab lists the expanded camera, so it exists only in single view.
        self.analysis.set_detections_side(None if mode == 'both' else mode)
        self._schedule_layout()

    def _schedule_layout(self, *args):
        self._layout_timer.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_app_bar()
        self._schedule_layout()

    def _fit_app_bar(self):
        """Narrow windows: hide the brand text first, then show the actions as icons."""
        layout = self.app_bar.layout()
        for show_brand, button_text in ((True, True), (False, True), (False, False)):
            self.brand_text.setVisible(show_brand)
            for button, text in ((self.labels_button, 'Labels'), (self.image_button, 'Adjust image')):
                button.setText(text if button_text else '')
            layout.invalidate()
            if layout.minimumSize().width() <= self.width():
                break

    def _fit_layout(self):
        """Give the cameras the height their aspect ratio can use and the rest
        to the dashboard, instead of letterboxing the video."""
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
        # The detections table needs a few more rows than the chart to be useful.
        minimum = DASHBOARD_MIN_H + (DETECTIONS_EXTRA_H if self.analysis.current == 'detections' else 0)
        dash_h = int(max(minimum, min(DASHBOARD_MAX_H, 0.45 * inner_h, inner_h - tile_h)))
        if self.dashboard.height() != dash_h or self.dashboard.minimumHeight() != dash_h:
            self.dashboard.setFixedHeight(dash_h)

    def _roi_changed(self, side, bounds):
        if self.yolo:
            self.yolo.set_roi(side, bounds)
        # ROI coordinates are already shown directly in the camera header.

    def _analysis_tab_changed(self, tab):
        self._schedule_layout()  # the Detections tab asks for a taller dashboard
        if tab == 'diagnostics':
            self._refresh_diagnostics(time.monotonic())
            if self.last_recv_frame is not None:
                self.diagnostics.update_barcode(self.last_recv_frame)
        elif tab == 'detections':
            self._refresh_detections(self._stream_frozen(time.monotonic()))
        else:
            self.last_chart_update = 0.0

    def _stream_frozen(self, now):
        return (self.last_frame_monotonic is not None
                and now - self.last_frame_monotonic >= self.thresholds.stale_after_s)

    def _refresh_detections(self, frozen):
        self.analysis.detections.set_detections(self.last_detections, stale=frozen)

    def _kpi_values(self):
        """(mean, p95, jitter, samples) over the last 100 valid frame delays, or None."""
        if not self.age_samples:
            return None
        values = np.asarray(list(self.age_samples)[-100:], dtype=float)
        jitter = float(np.std(np.diff(values))) if len(values) > 1 else 0.0
        return float(np.mean(values)), float(np.percentile(values, 95)), jitter, values

    def _update_limit_tooltips(self):
        t = self.thresholds
        delay = (f'OK below {t.delay_good_below_ms:g} ms · Watch below {t.delay_warn_below_ms:g} ms · '
                 'High at or above.')
        edit = '\nClick the status badge to edit the limits.'
        self.average_delay_card.setToolTip(
            'Mean NTP-corrected frame delay over the last 100 valid frames.\n' + delay + edit)
        self.delay_p95_card.setToolTip(
            '95th percentile of the last 100 valid frame delays; jitter is the standard '
            'deviation of consecutive differences.\n' + delay + edit)
        self.skew_card.setToolTip(
            'Right-minus-left camera timestamp, exponentially smoothed (τ = 1.5 s).\n'
            f'|skew| OK below {t.skew_good_below_ms:g} ms · Watch below {t.skew_warn_below_ms:g} ms · '
            'High at or above.' + edit)

    def _edit_limits(self):
        """Modal OK / Watch / High editor over a dimmed window; video keeps updating."""
        kpi = self._kpi_values()
        current = {'average': kpi[0] if kpi else None, 'p95': kpi[1] if kpi else None,
                   'skew': self.skew_smoother.value}
        dialog = LimitsDialog(self.thresholds, current, self)
        dialog.adjustSize()
        frame = self.frameGeometry()
        dialog.move(frame.center() - dialog.rect().center())
        self._modal_dim.setGeometry(self.rect())
        self._modal_dim.raise_()
        self._modal_dim.show()
        try:
            accepted = dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted
        finally:
            self._modal_dim.hide()
        if accepted:
            self._apply_thresholds(dialog.thresholds)

    def _apply_thresholds(self, thresholds, save=True):
        self.thresholds = thresholds
        self.analysis.set_thresholds(thresholds)
        self._update_limit_tooltips()
        if save:
            save_thresholds(self._preferences, thresholds)
        self._refresh_metrics()

    def _refresh_metrics(self):
        """10 Hz status/KPI cadence; raw image path remains frame-driven."""
        now = time.monotonic()
        stale_for = None if self.last_frame_monotonic is None else now-self.last_frame_monotonic
        for panel in (self.left_panel, self.right_panel):
            panel.set_stale(stale_for)
        recv_fps = 0.0
        if len(self.frame_arrival_times) > 1 and stale_for is not None and stale_for < 1:
            span = self.frame_arrival_times[-1] - self.frame_arrival_times[0]
            if span > 0:
                recv_fps = (len(self.frame_arrival_times)-1)/span
        self.recv_fps = recv_fps
        self._refresh_stream_chip(stale_for, recv_fps)
        self._refresh_clock_chip(now)
        self._refresh_yolo_chip()
        self._refresh_data_chip(now)
        for panel in (self.left_panel, self.right_panel):
            if panel._has_image:
                panel.set_hud(recv_fps, self.last_age_ms)
        self.diagnostics.update_counts(self.valid, self.invalid, self.missing)
        if self.diagnostics.contents.isVisible():
            self._refresh_diagnostics(now)
        frozen = stale_for is not None and stale_for >= self.thresholds.stale_after_s
        if self.analysis.current == 'detections':
            self._refresh_detections(frozen)
        self._refresh_kpis(now, frozen)

    def _refresh_stream_chip(self, stale_for, fps):
        t = self.thresholds
        detail = (f'{self._listen_text()}\n'
                  f'{self.packet_rate:.0f} UDP packets/s · {self.last_count:,} frames decoded')
        if self.stream_error:
            self.stream_chip.display('bad', 'Error', f'{self.stream_error}\n\n{detail}')
        elif stale_for is None:
            self.stream_chip.display('neutral', 'Waiting', detail)
        elif stale_for >= t.stream_disconnected_after_s:
            self.stream_chip.display('bad', f'No signal · {stale_for:.0f} s', detail)
        elif stale_for >= t.stale_after_s:
            self.stream_chip.display('warning', f'Stalled · {stale_for:.1f} s', detail)
        else:
            self.stream_chip.display('good', f'Live · {fps:.1f} fps', detail)

    def _refresh_clock_chip(self, now):
        a = self.args
        server = f'Time server {a.time_server}:{a.ntp_port}'
        if a.no_time_sync:
            self.clock_chip.display('neutral', 'Off', 'LAN clock sync disabled (--no-time-sync)')
        elif self.sync_rtt is None:
            if self._sync_future is not None:
                self.clock_chip.display('info', 'Syncing…', server)
            else:
                self.clock_chip.display('warning', 'Unavailable',
                                        f'{self.clock_sync_error or "No SNTP response yet"}\n{server}')
        else:
            age = now - (self.last_clock_sync_mono or now)
            detail = (f'Client − server offset {self.clock_offset:+.2f} ms '
                      f'(includes manual {a.clock_offset_ms:+.2f} ms)\n'
                      f'RTT {self.sync_rtt:.2f} ms · {self.sync_sample_count} samples · '
                      f'last sync {age:.0f} s ago\n{server}')
            if age > 15.0:
                self.clock_chip.display('warning', f'Stale · {self.clock_offset:+.1f} ms',
                                        'Last correction retained.\n' + detail)
            else:
                self.clock_chip.display('good', f'{self.clock_offset:+.1f} ms', detail)

    def _refresh_yolo_chip(self):
        if not self.yolo:
            self.yolo_chip.display('neutral', 'Off', 'Start with --yolo-model to enable detection')
            return
        a = self.args
        status = self.last_yolo_status
        detail = (f'{status}\nModel {a.yolo_model} · confidence {a.yolo_conf:g} · '
                  f'imgsz {a.yolo_imgsz} · device {a.yolo_device}')
        state = getattr(self.yolo, 'state', YoloState.READY)
        if state is YoloState.LOADING:
            self.yolo_chip.display('info', 'Loading…', detail)
        elif state is YoloState.ERROR:
            self.yolo_chip.display('bad', 'Error', detail)
        elif 'error' in status.lower():
            # Transient per-frame failure; the worker keeps running.
            self.yolo_chip.display('warning', 'Retrying', detail)
        elif self.last_yolo_ms:
            self.yolo_chip.display('good', f'{self.last_yolo_ms:.0f} ms · {self.last_detection_count} obj',
                                   detail)
        else:
            self.yolo_chip.display('good', 'Ready', detail)

    def _refresh_data_chip(self, now):
        window = self.thresholds.recent_error_window_s
        while self.recent_errors and now - self.recent_errors[0][0] > window:
            self.recent_errors.popleft()
        recent = sum(count for _, count in self.recent_errors)
        detail = (f'Since start: {self.valid:,} CRC-valid · {self.invalid:,} invalid · '
                  f'{self.missing:,} lost frames\nClick for Diagnostics (shortcut D)')
        if not self.valid and not self.invalid:
            self.data_chip.display('neutral', 'Waiting', detail)
        elif recent:
            self.data_chip.display('warning' if recent < 10 else 'bad',
                                   f'{recent} error{"s" if recent != 1 else ""} · {window:.0f} s',
                                   detail)
        else:
            self.data_chip.display('good', 'CRC OK', detail)

    def _refresh_kpis(self, now, frozen=False):
        """KPI readings coloured by this workstation's limits; greyed as 'Stale'
        while no frames arrive, so an old green reading never looks live."""
        t = self.thresholds
        kpi = self._kpi_values()
        if kpi is not None:
            mean, p95, jitter, values = kpi
            self.average_delay_card.set_value(
                mean, state='stale' if frozen else quality(mean, t.delay_good_below_ms, t.delay_warn_below_ms),
                secondary=f'Last {len(values)} valid frames · {np.min(values):.0f}–{np.max(values):.0f} ms')
            self.delay_p95_card.set_value(
                p95, state='stale' if frozen else quality(p95, t.delay_good_below_ms, t.delay_warn_below_ms),
                secondary=f'Jitter {jitter:.1f} ms (σ of Δ)')
        if self.skew_smoother.value is not None and self.last_skew_ms is not None:
            skew = self.skew_smoother.value
            self.skew_card.set_value(
                skew, state='stale' if frozen else quality(skew, t.skew_good_below_ms, t.skew_warn_below_ms),
                secondary=f'EMA τ 1.5 s · last {self.last_skew_ms:+.2f} ms')
        if ((self.age_samples or self.skew_samples) and self.analysis.current in AnalysisCard.CHART_TABS
                and now - self.last_chart_update >= 0.1):
            self.history.update_samples(self.age_samples, self.skew_samples)
            self.last_chart_update = now

    def _refresh_diagnostics(self, now):
        a = self.args
        if self.last_clock_sync_mono is not None:
            sync = f'{now - self.last_clock_sync_mono:.1f} s ago'
        else:
            sync = 'Disabled' if a.no_time_sync else '—'
        if not self.yolo:
            yolo = 'Off'
        elif self.last_yolo_ms:
            yolo = f'{self.last_yolo_ms:.0f} ms · {self.last_detection_count} obj'
        else:
            yolo = getattr(self.yolo, 'state', YoloState.READY).value.title()
        fields = {
            'crc': f'{self.valid:,}',
            'errors': (f'{self.invalid:,} / {self.missing:,}',
                       'warning' if self.invalid or self.missing else ''),
            'offset': f'{self.clock_offset:+.2f} ms',
            'rtt': f'{self.sync_rtt:.3f} ms' if self.sync_rtt is not None else 'Unavailable',
            'sync': sync,
            'packets': f'{self.packet_rate:,.0f}',
            'yolo': yolo,
        }
        stamp = self.last_stamp
        if stamp is not None:
            fields.update(
                left=iso_utc(stamp.left_utc_ms).replace(' UTC', ''),
                right=iso_utc(stamp.right_utc_ms).replace(' UTC', ''),
                sequence=f'{stamp.sequence:,}',
                age=f'{self.last_age_ms:.1f} ms',
                skew=f'{self.last_skew_ms:+.2f} ms')
        self.telemetry.update_fields(**fields)

    def _schedule_clock_sync(self):
        """Schedule a fresh absolute client-minus-server correction off the GUI thread."""
        if self._closing or self.args.no_time_sync:
            return
        if self._sync_future is not None:
            # An outstanding measurement may take longer than five seconds.
            # Do not queue overlapping or unbounded SNTP calls.
            return
        self._sync_future = self._sync_executor.submit(
            measure_ntp_offset,
            self.args.time_server, self.args.ntp_port, self.args.ntp_samples)

    def _poll_clock_sync(self):
        """Consume completed NTP samples on the GUI thread without blocking video."""
        future = self._sync_future
        if future is None or not future.done():
            return
        self._sync_future = None
        try:
            offset_ms, rtt_ms, count = future.result()
        except (OSError, TimeoutError, ValueError) as exc:
            self.clock_sync_error = str(exc)
            self.clock_status = f'Clock sync unavailable: {exc}'
            # Preserve the last successful correction; show stale after 15 s.
            return
        if self._closing:
            return
        previous_offset = self.clock_offset
        # Recalculate from the original user-supplied manual correction every
        # time. Adding to the prior value would accumulate a false drift.
        self.clock_offset = self.args.clock_offset_ms + offset_ms
        self.sync_rtt = rtt_ms
        self.sync_sample_count = count
        self.last_clock_sync_mono = time.monotonic()
        self.clock_sync_error = None
        self.clock_status = (
            f'Clock offset {offset_ms:+.2f} ms; '
            f'RTT {rtt_ms:.2f} ms ({count} samples); resync every 5 s')
        # Existing samples were calculated against the previous correction.
        # Re-express them against the new one instead of discarding them: the
        # KPIs keep a single clock basis and the history chart keeps sliding
        # rather than restarting at every resync.
        delta = self.clock_offset - previous_offset
        if delta:
            shifted = [age - delta for age in self.age_samples]
            self.age_samples.clear()
            self.age_samples.extend(shifted)  # same deque: the chart keeps its reference
            if self.last_age_ms is not None:
                self.last_age_ms -= delta

    def _start_services(self):
        if self.args.no_time_sync:
            self.clock_status = 'Clock sync disabled'
        else:
            self.clock_status = 'Clock sync in progress'
            self._schedule_clock_sync()
            self.clock_sync_timer = QtCore.QTimer(self)
            self.clock_sync_timer.setInterval(5000)
            self.clock_sync_timer.timeout.connect(self._schedule_clock_sync)
            self.clock_sync_timer.start()
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
            # Keep the message: the 10 Hz refresh would otherwise replace it
            # with a generic stale/no-signal state.
            self.stream_error = str(exc)
            self.timer.stop()
            self._refresh_metrics()
            return
        frame, recv_ns, frame_id = self.reader.latest()
        if frame is None or frame_id == self.last_count:
            return
        first_frame = self.last_frame_monotonic is None
        self.last_count = frame_id
        now = time.monotonic()
        self.last_frame_monotonic = now
        self.frame_arrival_times.append(now)
        self.last_recv_frame = frame
        source_h = video_height(frame.shape[0])
        # The raw source is retained for timestamp/CRC decoding and Diagnostics.
        # Client-specific LUT adjustments affect BOTH the preview and YOLO input.
        adjusted_frame = apply_to_stereo_frame(frame, self._image_lut, source_h)
        left_view, right_view = split_stereo(adjusted_frame)
        shape_before = self.left_panel.native_shape
        self.left_panel.set_image(left_view)
        self.right_panel.set_image(right_view)
        if self.yolo:
            self.yolo.submit(adjusted_frame, frame_id)
            detections, status, result_id, result_mono, ms, error = self.yolo.snapshot()
            self.last_yolo_status = status
            self.last_yolo_ms = ms
            if not (result_mono and time.monotonic()-result_mono < 1.0):
                detections = ()  # stale results are hidden everywhere
            self.last_detections = tuple(detections)
            self.last_detection_count = len(detections)
            self.left_panel.set_detections(detections)
            self.right_panel.set_detections(detections)
        stamp, err = decode_strip(frame)
        diagnostics_open = self.diagnostics.contents.isVisible()
        if stamp is not None:
            self.valid += 1
            age = recv_ns / 1e6 - stamp.left_utc_ms - self.clock_offset
            self.age_samples.append(age)
            self.skew_samples.append(stamp.right_minus_left_us/1000.0)
            self.last_age_ms = age
            self.last_skew_ms = stamp.right_minus_left_us/1000.0
            self.last_stamp = stamp
            self.skew_smoother.update(stamp.right_minus_left_us / 1000.0, now)
            # Dashboard widgets update from the 10 Hz presentation timer.
            if self.last_seq is not None:
                delta = (stamp.sequence-self.last_seq)%(1<<32)
                if 1 < delta < 10000:
                    self.missing += delta-1
                    self.recent_errors.append((now, delta-1))
            self.last_seq = stamp.sequence
            self.latest_stamp = stamp
            self.last_decode_error = None
            if diagnostics_open:
                self.diagnostics.set_decode_result(True)
                self.diagnostics.barcode.set_values({
                    'magic': 'A55A', 'version': '1',
                    'left': iso_utc(stamp.left_utc_ms).replace(' UTC', ''),
                    'sequence': f'{stamp.sequence}',
                    'skew': f'{stamp.right_minus_left_us:+d}',
                    'crc': '✓ valid'}, True)
        else:
            self.invalid += 1
            self.last_error_mono = time.monotonic()
            self.last_decode_error = err or 'unknown decode error'
            self.recent_errors.append((self.last_error_mono, 1))
            self.diagnostics.flag_crc_error(self.last_decode_error)
            if diagnostics_open:
                self.diagnostics.barcode.set_values({'crc': '✕ failed'}, False)
        if diagnostics_open:
            # Barcode and telemetry grid always describe the same frame.
            self.diagnostics.update_barcode(frame)
            self._refresh_diagnostics(now)
        packet_count = self.reader.relay.packets if self.reader.relay else frame_id
        elapsed = time.monotonic()-self.last_packet_mono
        if elapsed >= 1.0:
            self.packet_rate = (packet_count-self.last_packet_count)/elapsed
            self.last_packet_count = packet_count
            self.last_packet_mono = time.monotonic()
        if first_frame or shape_before != self.left_panel.native_shape:
            self._schedule_layout()

    def closeEvent(self, event):
        self._closing = True
        if hasattr(self, 'timer'):
            self.timer.stop()
        if hasattr(self, 'health_timer'):
            self.health_timer.stop()
        if hasattr(self, 'clock_sync_timer'):
            self.clock_sync_timer.stop()
        if self.image_dialog is not None:
            self.image_dialog.close()
        self._sync_executor.shutdown(wait=False, cancel_futures=True)
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


def self_test():
    panel = CameraPanel('LEFT', '#70e4a6')
    panel.set_stale(None)
    assert panel.overlay.isVisibleTo(panel) and 'Waiting' in panel.overlay.title.text()
    panel.set_image(np.zeros((480, 640, 3), dtype=np.uint8))
    assert not panel.overlay.isVisibleTo(panel)
    panel._set_roi((30, 40, 250, 300), emit=False)
    assert panel.bounds == (30, 40, 250, 300)
    assert isinstance(panel.roi_box, QtWidgets.QGraphicsRectItem)
    assert not isinstance(panel.roi_box, pg.ROI)
    assert panel.roi_mask.isVisible() and panel.roi_clear.isVisibleTo(panel)
    assert panel.info.text() == fmt_roi((30, 40, 250, 300))
    assert panel.vbox.state['mouseEnabled'] == [True, True]
    panel.vbox.reset_requested.emit()
    assert panel.bounds is None and not panel.roi_mask.isVisible()
    panel.set_stale(THRESHOLDS.stream_disconnected_after_s + 1)
    assert panel.overlay.title.text() == 'No signal' and panel.stale_cover.isVisible()
    panel.set_stale(0.0)
    assert not panel.overlay.isVisibleTo(panel)
    # Only one view button per camera, with correct split/full state.
    assert panel.view_toggle.text() == 'Expand view'
    panel.set_view_mode('left')
    assert panel.view_toggle.text() == 'Both cameras'
    panel.set_view_mode('both')
    assert panel.view_toggle.text() == 'Expand view'
    assert class_color('box') == class_color('box')
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
    chip.display('good', 'READY')
    assert 'READY' in chip.text()
    bar = TelemetryBar()
    bar.update_fields(age='125 ms', crc='1')
    assert bar.fields['age'].value.text() == '125 ms'
    cards = KpiCard('AVERAGE DELAY', 'Last 100', led_color=COLORS['accent'])
    cards.set_value(12.3)
    assert cards.readout.digits == '123'
    cards.set_value(400.0, state=quality(400.0, THRESHOLDS.delay_good_below_ms,
                                         THRESHOLDS.delay_warn_below_ms))
    assert cards.badge.text().endswith('High') and cards.readout.color == QtGui.QColor(COLORS['bad'])
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
    analysis = AnalysisCard(DelayAxis)
    analysis.show_tab('diagnostics')
    assert analysis.stack.currentWidget() is analysis.diagnostics
    analysis.toggle_diagnostics()
    assert analysis.current == 'delay' and analysis.stack.currentWidget() is analysis.chart
    analysis.show_tab('skew')
    assert analysis.chart.mode == 'skew' and analysis.chart.bands['warn_lo'].isVisible()
    import types
    # Repeated SNTP corrections must be absolute, never cumulative.
    dummy_args = types.SimpleNamespace(clock_offset_ms=3.0, no_time_sync=False)
    history = collections.deque([10.0, 20.0], maxlen=2000)
    dummy = types.SimpleNamespace(
        _sync_future=concurrent.futures.Future(), _closing=False,
        args=dummy_args, clock_offset=3.0, age_samples=history, last_age_ms=20.0,
        sync_rtt=None, sync_sample_count=0, last_clock_sync_mono=None,
        clock_sync_error=None, clock_status='')
    dummy._sync_future.set_result((11.0, 0.3, 7))
    Studio._poll_clock_sync(dummy)
    assert dummy.clock_offset == 14.0
    # History is re-based on the new correction (not cleared), in the same deque.
    assert list(dummy.age_samples) == [-1.0, 9.0] and dummy.last_age_ms == 9.0
    assert dummy.age_samples is history
    dummy._sync_future = concurrent.futures.Future()
    dummy._sync_future.set_result((12.0, 0.2, 7))
    Studio._poll_clock_sync(dummy)
    assert dummy.clock_offset == 15.0, 'NTP offset must not accumulate'
    assert list(dummy.age_samples) == [-2.0, 8.0]
    # The history chart is a strip chart: the axis spans the window from the
    # first frame, and once full the data slides instead of restarting.
    strip = LatencyChart(DelayAxis)
    samples = collections.deque((float(v) for v in range(10)), maxlen=2000)
    strip.update_samples(samples, samples)
    assert strip.plot.getViewBox().viewRange()[0][0] < -98, 'axis spans the whole window'
    samples.extend(float(v) for v in range(10, 150))
    strip.update_samples(samples, samples)
    oldest = strip._ydata[0]
    samples.append(150.0)
    strip.update_samples(samples, samples)
    assert strip._display_count == 100
    assert strip._ydata[0] == oldest + 1 and strip._ydata[99] == 150.0, 'slides by one frame'
    assert strip.plot.getViewBox().viewRange()[0][0] < -98
    # Pixel adjustments must affect video but never modify embedded CRC.
    raw = np.zeros((120, 16, 3), dtype=np.uint8)
    raw[:20] = 80
    raw[20:] = 231
    config = ImageSettings(exposure=80, brightness=25,
                           contrast=15, temperature=20, tint=0)
    table = build_lut(config)
    assert table is not None and table.shape == (256, 1, 3)
    corrected = apply_to_stereo_frame(raw, table, 20)
    assert not np.array_equal(corrected[:20], raw[:20])
    assert np.array_equal(corrected[20:], raw[20:])
    assert np.array_equal(raw[:20], np.full((20, 16, 3), 80, np.uint8))
    assert apply_to_stereo_frame(raw, build_lut(ImageSettings()), 20) is raw
    control = ImageAdjustmentsDialog(ImageSettings())
    assert isinstance(control.drag_header, QtWidgets.QWidget)
    assert control.drag_header.cursor().shape() == QtCore.Qt.CursorShape.OpenHandCursor
    assert control.settings.neutral()
    control._sliders['exposure'].setValue(50)
    assert control.settings.exposure == 50
    control.reset_values()
    assert control.settings.neutral()
    control._sliders['tint'].setValue(-30)
    control._reset_slider('tint')
    assert control.settings.tint == 0
    # Detection label options: text per preset, custom mixes, drawing and hiding.
    det = Detection('LEFT', 'box', 0.94, (100.0, 50.0, 300.0, 190.0), 2)
    assert det.center == (200.0, 120.0)
    assert format_detection_label(det, PRESETS['minimal']) == 'box'
    assert format_detection_label(det, PRESETS['standard']) == 'box · 94%'
    assert format_detection_label(det, PRESETS['detailed']) == 'box · id 2 · 94%\n(200, 120) · 200×140 px'
    assert format_detection_label(det, AnnotationOptions(class_name=False, confidence=False)) == ''
    assert preset_of(PRESETS['detailed']) == 'detailed'
    assert preset_of(dataclasses.replace(PRESETS['standard'], class_id=True)) is None
    assert next_preset(PRESETS['detailed']) == 'minimal'
    panel.set_annotation_options(PRESETS['detailed'])
    panel.set_detections([det])
    assert '(200, 120)' in panel.box_labels[0].textItem.toPlainText()
    assert panel.box_items[0].isVisible() and panel.center_markers.isVisible()
    panel.set_annotation_options(dataclasses.replace(PRESETS['detailed'], show=False))
    assert not panel.box_items[0].isVisible() and not panel.center_markers.isVisible()
    assert panel.detect_count.text() == '1 object', 'counts keep updating while hidden'
    popover = AnnotationPopover(PRESETS['standard'])
    emitted = []
    popover.changed.connect(emitted.append)
    popover._switches['class_id'].setChecked(True)
    assert emitted[-1].class_id and popover.presets.current is None
    assert popover.custom_note.isVisibleTo(popover)
    popover.presets.select('minimal')
    assert emitted[-1] == PRESETS['minimal'] and not popover._switches['confidence'].isChecked()
    # Status limits: OK stays below Watch, defaults restore, settings round-trip, bands follow.
    limits = LimitsDialog(THRESHOLDS, {'average': 124.0, 'p95': 138.0, 'skew': 3.6})
    ok_spin, watch_spin, *_ = limits._spins['delay']
    ok_spin.setValue(320.0)
    assert watch_spin.value() > ok_spin.value(), 'raising OK past Watch pushes Watch up'
    watch_spin.setValue(100.0)
    assert ok_spin.value() < watch_spin.value(), 'lowering Watch below OK pushes OK down'
    assert 'High' in limits._now['delay'].text(), 'preview reclassifies the current reading'
    limits.restore_defaults()
    assert limits.thresholds == THRESHOLDS
    import os
    import tempfile
    custom = dataclasses.replace(THRESHOLDS, delay_good_below_ms=200.0, delay_warn_below_ms=400.0)
    with tempfile.TemporaryDirectory() as folder:
        store = QtCore.QSettings(os.path.join(folder, 'limits.ini'), QtCore.QSettings.Format.IniFormat)
        save_thresholds(store, custom)
        assert load_thresholds(store) == custom
        store.setValue('limits/skew_good_below_ms', 50.0)  # now above Watch: invalid pair
        assert load_thresholds(store).skew_good_below_ms == THRESHOLDS.skew_good_below_ms
        store.sync()
        del store
    analysis.show_tab('delay')
    analysis.set_thresholds(custom)
    assert analysis.chart.bands['warn_hi'].getRegion() == (200.0, 400.0)
    assert '≥ 200 ms' in analysis.legend.text()
    # Detections tab: single-camera view only, rows for that camera, frozen while paused.
    assert not analysis.tabs.buttons['detections'].isVisibleTo(analysis)
    analysis.set_detections_side('left')
    assert analysis.tabs.buttons['detections'].isVisibleTo(analysis)
    analysis.show_tab('detections')
    assert analysis.stack.currentWidget() is analysis.detections
    table = analysis.detections.table
    other = Detection('RIGHT', 'can', 0.5, (0.0, 0.0, 10.0, 10.0), 1)
    analysis.detections.set_detections([det, other])
    assert table.rowCount() == 1 and table.item(0, 3).text() == '200'
    assert 'box\t2\t94%\t200\t120\t200\t140' in table.copy_rows()
    analysis.toggle_pause()
    analysis.detections.set_detections([])
    assert table.rowCount() == 1, 'a paused table keeps its rows'
    analysis.toggle_pause()
    analysis.set_detections_side(None)
    assert analysis.current == 'delay' and not analysis.tabs.buttons['detections'].isVisibleTo(analysis)
    print('PASS: Qt control-room, ROI, timing, image adjustments, detection labels, '
          'status limits and detections table')
    return 0


def main():
    p = build_parser()
    args = p.parse_args()
    if args.transport == 'multicast' and not args.iface_ip and not args.self_test:
        p.error('Multicast requires --iface-ip LOCAL_ETHERNET_IP')
    if not 0 < args.yolo_conf <= 1:
        p.error('YOLO confidence must be in (0,1]')
    app = QtWidgets.QApplication(sys.argv[:1])
    apply_theme(app)
    if args.self_test:
        return self_test()
    studio = Studio(args)
    studio.showMaximized()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
