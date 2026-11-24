"""Reusable control-room widgets: status pills, KPI tiles, history chart, diagnostics.

Lightweight (Qt + PyQtGraph only) and driven by the 10 Hz presentation timer.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets
import numpy as np
import pyqtgraph as pg

from icons import icon
from seven_segment import SevenSegmentDisplay
from stamp_protocol import extract_strip
from theme import (COLORS, STATE_COLORS, STATE_MARKS, STATE_WORDS, THRESHOLDS,
                   class_color, display_font, numeric_font, tint, ui_font)


def set_state(widget, state):
    """Repolish QSS only when a semantic status changes."""
    if widget.property('state') == state:
        return
    widget.setProperty('state', state)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def overline(text, parent=None, size=11):
    label = QtWidgets.QLabel(text, parent)
    label.setObjectName('overline')
    label.setFont(display_font(size))
    return label


class StatusChip(QtWidgets.QAbstractButton):
    """App-bar status pill: state mark + title + short message.

    State is carried by the mark shape and the message text as well as by
    colour. Details live in the tooltip so the pill stays short and its width
    does not jitter as values change.
    """
    def __init__(self, label: str, parent=None, min_width=150):
        super().__init__(parent)
        self.title = label
        self.state = 'neutral'
        self.message = 'Waiting'
        self._min_width = min_width
        self._clickable = False
        self._title_font = display_font(11)
        self._message_font = ui_font(13, QtGui.QFont.Weight.DemiBold)
        self.setText(f'{label}: {self.message}')
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed,
                           QtWidgets.QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_Hover, True)

    def set_clickable(self, clickable=True):
        self._clickable = clickable
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor if clickable
                       else QtCore.Qt.CursorShape.ArrowCursor)

    def display(self, state: str, message: str, detail: str | None = None):
        changed = (state, message) != (self.state, self.message)
        self.state, self.message = state, message
        text = f'{self.title}: {message}'
        if self.text() != text:
            self.setText(text)
        if detail is not None and detail != self.toolTip():
            self.setToolTip(detail)
        if changed:
            self.updateGeometry()
            self.update()

    def sizeHint(self):
        title_w = QtGui.QFontMetrics(self._title_font).horizontalAdvance(self.title.upper())
        message_w = QtGui.QFontMetrics(self._message_font).horizontalAdvance(self.message)
        return QtCore.QSize(max(self._min_width, 28 + title_w + 8 + message_w + 14), 30)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2
        color = QtGui.QColor(STATE_COLORS.get(self.state, COLORS['neutral']))
        neutral = self.state == 'neutral'
        hovered = self._clickable and self.underMouse()
        p.setPen(QtGui.QPen(QtGui.QColor(COLORS['border']) if neutral else tint(color, 110), 1))
        p.setBrush(QtGui.QColor(COLORS['hover'] if hovered else COLORS['raised']))
        p.drawRoundedRect(rect, radius, radius)
        if not neutral:
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(tint(color, 30))
            p.drawRoundedRect(rect, radius, radius)
        self._paint_mark(p, 15.0, rect.center().y(), color)
        title = self.title.upper()
        x = 27.0
        p.setFont(self._title_font)
        p.setPen(QtGui.QColor(COLORS['secondary']))
        title_w = QtGui.QFontMetrics(self._title_font).horizontalAdvance(title)
        p.drawText(QtCore.QRectF(x, 0, title_w + 4, self.height()),
                   QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignLeft, title)
        x += title_w + 8
        p.setFont(self._message_font)
        p.setPen(QtGui.QColor(COLORS['text']))
        room = max(10, int(self.width() - x - 12))
        message = QtGui.QFontMetrics(self._message_font).elidedText(
            self.message, QtCore.Qt.TextElideMode.ElideRight, room)
        p.drawText(QtCore.QRectF(x, 0, room, self.height()),
                   QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignLeft, message)
        p.end()

    def _paint_mark(self, p, cx, cy, color):
        p.save()
        if self.state == 'good':
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(QtCore.QPointF(cx, cy), 4, 4)
        elif self.state == 'warning':
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(cx, cy - 4.5), QtCore.QPointF(cx + 5, cy + 4),
                                           QtCore.QPointF(cx - 5, cy + 4)]))
        elif self.state == 'bad':
            pen = QtGui.QPen(color, 2.2)
            pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(QtCore.QPointF(cx - 3.5, cy - 3.5), QtCore.QPointF(cx + 3.5, cy + 3.5))
            p.drawLine(QtCore.QPointF(cx + 3.5, cy - 3.5), QtCore.QPointF(cx - 3.5, cy + 3.5))
        elif self.state == 'info':
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(cx, cy - 4.5), QtCore.QPointF(cx + 4.5, cy),
                                           QtCore.QPointF(cx, cy + 4.5), QtCore.QPointF(cx - 4.5, cy)]))
        else:
            p.setPen(QtGui.QPen(color, 1.6))
            p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            p.drawEllipse(QtCore.QPointF(cx, cy), 3.5, 3.5)
        p.restore()


class ToggleSwitch(QtWidgets.QCheckBox):
    """A QCheckBox drawn as an on/off switch (same signals and API)."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

    def sizeHint(self):
        metrics = self.fontMetrics()
        return QtCore.QSize(50 + metrics.horizontalAdvance(self.text()), max(24, metrics.height() + 6))

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        enabled = self.isEnabled()
        track = QtCore.QRectF(0.5, (self.height() - 20) / 2 + 0.5, 38, 20)
        p.setPen(QtCore.Qt.PenStyle.NoPen if on else QtGui.QPen(QtGui.QColor(COLORS["border_strong"]), 1))
        if on:
            p.setBrush(QtGui.QColor(COLORS["accent"] if enabled else COLORS["border_strong"]))
        else:
            p.setBrush(QtGui.QColor(COLORS["raised"]))
        p.drawRoundedRect(track, 10, 10)
        knob_x = track.right() - 17 if on else track.left() + 3
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(COLORS["on_accent"] if on else
                                (COLORS["secondary"] if enabled else COLORS["border_strong"])))
        p.drawEllipse(QtCore.QRectF(knob_x, track.top() + 3, 14, 14))
        p.setPen(QtGui.QColor(COLORS["text"] if enabled else COLORS["muted"]))
        p.setFont(self.font())
        p.drawText(QtCore.QRectF(50, 0, self.width() - 50, self.height()),
                   QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignLeft, self.text())
        p.end()


class SegmentedControl(QtWidgets.QFrame):
    """Exclusive option selector used for tabs, view modes and sample windows."""
    selected = QtCore.Signal(str)
    VIEW_OPTIONS = (('both', 'Both cameras', 'View both cameras · shortcut 1'),
                    ('left', 'Left camera', 'View the left camera · shortcut 2'),
                    ('right', 'Right camera', 'View the right camera · shortcut 3'))

    def __init__(self, options=VIEW_OPTIONS, parent=None):
        super().__init__(parent)
        self.setObjectName('segmented')
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        self.group = QtWidgets.QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        self.current = None
        for key, text, tooltip in options:
            button = QtWidgets.QPushButton(text)
            button.setObjectName('segment')
            button.setCheckable(True)
            button.setToolTip(tooltip)
            button.setFont(ui_font(12, QtGui.QFont.Weight.DemiBold))
            button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            # Never squeeze a label below its text width.
            button.setSizePolicy(QtWidgets.QSizePolicy.Policy.Minimum,
                                 QtWidgets.QSizePolicy.Policy.Fixed)
            self.group.addButton(button)
            self.buttons[key] = button
            layout.addWidget(button)
            button.clicked.connect(lambda checked=False, k=key: self.select(k))
        if options:
            self.select(options[0][0], emit=False)

    def select(self, key: str, emit=True):
        if key not in self.buttons:
            return
        self.current = key
        if not self.buttons[key].isChecked():
            self.buttons[key].setChecked(True)
        if emit:
            self.selected.emit(key)

    def clear_selection(self):
        """Show no option as selected (e.g. a custom mix that matches no preset)."""
        self.current = None
        self.group.setExclusive(False)
        for button in self.buttons.values():
            button.setChecked(False)
        self.group.setExclusive(True)


class MetricField(QtWidgets.QWidget):
    """Overline label above a monospaced value."""
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        box = QtWidgets.QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(2)
        self.name = QtWidgets.QLabel(label)
        self.name.setObjectName('metricLabel')
        self.name.setFont(display_font(10.5))
        self.value = QtWidgets.QLabel('—')
        self.value.setObjectName('metricValue')
        self.value.setFont(numeric_font(14))
        self.value.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(self.name)
        box.addWidget(self.value)

    def update_value(self, text: str, state: str | None = None):
        if self.value.text() != text:
            self.value.setText(text)
        if state is not None:
            set_state(self.value, state)


class TelemetryBar(QtWidgets.QFrame):
    """Aligned grid of raw diagnostic values (label above value)."""
    LABELS = (('crc', 'CRC VALID'), ('errors', 'INVALID / LOST'), ('sequence', 'SEQUENCE'),
              ('left', 'LEFT UTC'), ('right', 'RIGHT UTC'), ('age', 'LAST DELAY'),
              ('skew', 'LAST SKEW R−L'), ('offset', 'CLOCK OFFSET'), ('rtt', 'CLOCK RTT'),
              ('sync', 'LAST SYNC'), ('packets', 'UDP PACKETS / S'), ('yolo', 'YOLO'))

    def __init__(self, parent=None, columns=6):
        super().__init__(parent)
        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)
        self.fields = {}
        for i, (key, label) in enumerate(self.LABELS):
            field = MetricField(label)
            self.fields[key] = field
            grid.addWidget(field, i // columns, i % columns)
        for column in range(columns):
            grid.setColumnStretch(column, 1)

    def update_fields(self, **values):
        for key, value in values.items():
            if key not in self.fields:
                continue
            if isinstance(value, tuple):
                self.fields[key].update_value(str(value[0]), value[1])
            else:
                self.fields[key].update_value(str(value))


class KpiCard(QtWidgets.QFrame):
    """Metric tile: overline title, state badge, seven-segment reading, context line."""
    def __init__(self, title: str, subtitle: str = '', led_color=None, parent=None,
                 help_text: str | None = None, max_digit_px: int = 64):
        super().__init__(parent)
        self.setObjectName('kpiCard')
        self.state = None
        self.neutral_color = led_color or COLORS['neutral']
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(6)
        self.name = overline(title)
        # The badge doubles as the entry point for editing the OK/Watch/High limits.
        self.badge = QtWidgets.QPushButton()
        self.badge.setObjectName('stateBadge')
        self.badge.setFont(display_font(11))
        self.badge.setFlat(True)
        self.badge.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.badge.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.badge.setToolTip('Edit the OK / Watch / High limits')
        self.badge_clicked = self.badge.clicked
        header.addWidget(self.name)
        header.addStretch()
        header.addWidget(self.badge)
        layout.addLayout(header)
        self.readout = SevenSegmentDisplay(title, self.neutral_color, show_title=False)
        # Digits grow with the card up to a readable cap, then stay put.
        self.readout.max_scale = max_digit_px / 70.0
        layout.addWidget(self.readout, 1)
        self.label = None
        self.secondary = QtWidgets.QLabel(subtitle)
        self.secondary.setObjectName('caption')
        self.secondary.setFont(ui_font(12))
        layout.addWidget(self.secondary)
        if help_text:
            self.setToolTip(help_text)
        self.set_quality('neutral')

    def set_value(self, value_ms=None, text=None, secondary=None, state=None):
        self.readout.set_milliseconds(value_ms)
        if state is not None:
            self.set_quality(state)
        if secondary is not None and secondary != self.secondary.text():
            self.secondary.setText(secondary)

    def set_quality(self, state: str):
        if state == self.state:
            return
        self.state = state
        color = self.neutral_color if state == 'neutral' else STATE_COLORS.get(state, self.neutral_color)
        self.readout.set_color(color)
        self.badge.setText(f'{STATE_MARKS.get(state, "○")} {STATE_WORDS.get(state, "")}')
        set_state(self.badge, state)


class BarcodeView(QtWidgets.QWidget):
    """The original 168-bit strip, enlarged with nearest-neighbour and labelled by field."""
    FIELDS = (('magic', 'Magic', 16), ('version', 'Ver', 8), ('left', 'Left UTC', 64),
              ('sequence', 'Sequence', 32), ('skew', 'Skew R−L µs', 32), ('crc', 'CRC-16', 16))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = None
        self.values = {}
        self.ok = None
        self._label_font = display_font(10)
        self._value_font = numeric_font(12)
        self.setFixedHeight(84)
        self.setToolTip('Original 6×16-pixel cells from the received frame (black = 1). '
                        'Fields left to right: magic, version, left UTC ms, sequence, '
                        'right-minus-left skew, CRC-16.')

    def set_strip(self, image: QtGui.QImage):
        self.image = image
        self.update()

    def set_values(self, values: dict, ok: bool):
        if values != self.values or ok != self.ok:
            self.values, self.ok = dict(values), ok
            self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        width = self.width()
        strip = QtCore.QRectF(0, 0, width, 30)
        if self.image is None:
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            p.setPen(QtGui.QColor(COLORS['border']))
            p.setBrush(QtGui.QColor(COLORS['raised']))
            p.drawRoundedRect(strip.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
            p.setPen(QtGui.QColor(COLORS['muted']))
            p.setFont(ui_font(12))
            p.drawText(strip, QtCore.Qt.AlignmentFlag.AlignCenter, 'Waiting for timestamp data')
        else:
            # Nearest-neighbour keeps every bit cell crisp.
            p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, False)
            p.drawImage(strip, self.image)
        x = 0.0
        y = 36.0
        for key, name, bits in self.FIELDS:
            field_w = width * bits / 168.0
            p.setPen(QtGui.QPen(QtGui.QColor(COLORS['border_strong']), 1))
            p.drawLine(QtCore.QPointF(x + 2, y), QtCore.QPointF(x + field_w - 2, y))
            p.drawLine(QtCore.QPointF(x + 2, y - 3), QtCore.QPointF(x + 2, y + 2))
            p.drawLine(QtCore.QPointF(x + field_w - 2, y - 3), QtCore.QPointF(x + field_w - 2, y + 2))
            box = QtCore.QRectF(x + 2, y + 4, field_w - 4, 16)
            p.setFont(self._label_font)
            p.setPen(QtGui.QColor(COLORS['muted']))
            label = QtGui.QFontMetrics(self._label_font).elidedText(
                name.upper(), QtCore.Qt.TextElideMode.ElideRight, int(box.width()))
            p.drawText(box, QtCore.Qt.AlignmentFlag.AlignCenter, label)
            value = self.values.get(key, '—')
            color = COLORS['text']
            if key == 'crc' and self.ok is not None:
                color = COLORS['good'] if self.ok else COLORS['bad']
            p.setFont(self._value_font)
            p.setPen(QtGui.QColor(color))
            value_box = QtCore.QRectF(x + 2, y + 21, field_w - 4, 20)
            value = QtGui.QFontMetrics(self._value_font).elidedText(
                str(value), QtCore.Qt.TextElideMode.ElideRight, int(value_box.width()))
            p.drawText(value_box, QtCore.Qt.AlignmentFlag.AlignCenter, value)
            x += field_w
        p.end()


class DiagnosticsPanel(QtWidgets.QWidget):
    """Decoder evidence: integrity counters, annotated barcode and raw telemetry."""
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
        self.crc_summary = QtWidgets.QLabel('Valid —  ·  Invalid —  ·  Lost —')
        self.crc_summary.setObjectName('secondary')
        self.crc_summary.setFont(numeric_font(13))
        self.decode_status = QtWidgets.QLabel('○ Waiting for the first frame')
        self.decode_status.setObjectName('stateBadge')
        self.decode_status.setFont(ui_font(12, QtGui.QFont.Weight.DemiBold))
        top.addWidget(self.crc_summary)
        top.addStretch()
        top.addWidget(self.decode_status)
        layout.addLayout(top)
        self.barcode = BarcodeView()
        layout.addWidget(self.barcode)
        self.telemetry = TelemetryBar()
        layout.addWidget(self.telemetry)
        layout.addStretch()
        self.crc_error = False

    def update_counts(self, valid, invalid, lost):
        summary = f'Valid {valid:,}  ·  Invalid {invalid:,}  ·  Lost {lost:,}'
        if self.crc_summary.text() != summary:
            self.crc_summary.setText(summary)

    def flag_crc_error(self, message='CRC error in the latest frame'):
        self.crc_error = True
        self.set_decode_result(False, message)

    def set_decode_result(self, ok: bool, message: str = ''):
        text = '● Latest frame decoded · CRC valid' if ok else f'✕ {message}'
        if self.decode_status.text() != text:
            self.decode_status.setText(text)
        set_state(self.decode_status, 'good' if ok else 'bad')

    def update_barcode(self, frame):
        if not self.contents.isVisible():
            return
        strip = extract_strip(frame)
        if strip is None:
            return
        rgb = np.ascontiguousarray(strip[:, :, ::-1])
        height, width = rgb.shape[:2]
        image = QtGui.QImage(rgb.data, width, height, width * 3,
                             QtGui.QImage.Format.Format_RGB888).copy()
        self.barcode.set_strip(image)


class StateOverlay(QtWidgets.QFrame):
    """Centred card over the video for the waiting / stalled / no-signal states."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('overlayCard')
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(18, 14, 24, 14)
        row.setSpacing(14)
        self.mark = QtWidgets.QLabel('○')
        self.mark.setObjectName('overlayMark')
        self.mark.setFont(ui_font(26, QtGui.QFont.Weight.DemiBold))
        text = QtWidgets.QVBoxLayout()
        text.setSpacing(2)
        self.title = QtWidgets.QLabel()
        self.title.setObjectName('overlayTitle')
        self.title.setFont(ui_font(16, QtGui.QFont.Weight.DemiBold))
        self.subtitle = QtWidgets.QLabel()
        self.subtitle.setObjectName('overlaySubtitle')
        self.subtitle.setFont(ui_font(12))
        text.addWidget(self.title)
        text.addWidget(self.subtitle)
        row.addWidget(self.mark)
        row.addLayout(text)

    def show_state(self, state, mark, title, subtitle):
        if self.mark.text() != mark:
            self.mark.setText(mark)
        if self.title.text() != title:
            self.title.setText(title)
        if self.subtitle.text() != subtitle:
            self.subtitle.setText(subtitle)
        set_state(self.mark, state)
        if not self.isVisibleTo(self.parentWidget()):
            self.show()


class DelayAxis(pg.AxisItem):
    """Graph axis: data stays in ms, labels switch between ms and s.

    Disable SI prefixes here: pyqtgraph may otherwise label kiloms as 'kms'.
    ``quantity`` is the axis title ('Delay', 'Skew', 'Source age', ...).
    """
    def __init__(self, orientation='left'):
        super().__init__(orientation=orientation)
        self.unit = 'ms'
        self.quantity = 'Delay'
        self.enableAutoSIPrefix(False)
        self.setLabel('Delay (ms)', color=COLORS['secondary'], size='10pt')

    def set_quantity(self, quantity):
        self.quantity = quantity
        self.setLabel(f'{quantity} ({self.unit})', color=COLORS['secondary'], size='10pt')

    def update_for_range(self, low_ms, high_ms):
        unit = 's' if max(abs(low_ms), abs(high_ms)) >= 1000.0 else 'ms'
        if unit != self.unit:
            self.unit = unit
            self.setLabel(f'{self.quantity} ({unit})', color=COLORS['secondary'], size='10pt')
            self.picture = None
            self.update()

    def tickSpacing(self, minVal, maxVal, size):
        # One tick level, so every label uses the same format (no alternating
        # '20' / '25.0'): the finest standard level whose labels stay at least
        # ~40 px apart at the axis' current height.
        levels = super().tickSpacing(minVal, maxVal, size)
        span = abs(maxVal - minVal)
        chosen = levels[:1]
        for spacing, offset in levels:  # coarse to fine
            if span and size * spacing / span >= 40:
                chosen = [(spacing, offset)]
        return chosen

    def tickStrings(self, values, scale, spacing):
        divisor = 1000.0 if self.unit == 's' else 1.0
        step = abs(spacing / divisor)
        if step >= 10:
            decimals = 0
        elif step >= 1:
            decimals = 0 if all(float(v / divisor).is_integer() for v in values) else 1
        elif step >= 0.1:
            decimals = 2
        else:
            decimals = 3
        return [f'{value / divisor:.{decimals}f}' for value in values]


class FramesAgoAxis(pg.AxisItem):
    """Bottom axis: samples sit at x = -n, labelled as 'n frames ago' (whole frames)."""
    def tickSpacing(self, minVal, maxVal, size):
        levels = super().tickSpacing(minVal, maxVal, size)
        return [(max(1.0, spacing), offset) for spacing, offset in (levels[1:2] or levels[:1])]

    def tickStrings(self, values, scale, spacing):
        return [f'{abs(value):g}' for value in values]


def _fade_brush(color, top_alpha=70):
    """Area fill that fades from the line towards the baseline."""
    gradient = QtGui.QLinearGradient(0, 0, 0, 1)
    gradient.setCoordinateMode(QtGui.QGradient.CoordinateMode.ObjectBoundingMode)
    gradient.setColorAt(0.0, tint(color, 4))
    gradient.setColorAt(1.0, tint(color, top_alpha))
    return QtGui.QBrush(gradient)


class LatencyChart(QtWidgets.QWidget):
    """Delay/skew history: raw samples, mean, provisional threshold bands, hover readout."""
    MODES = {
        'delay': {'color': 'accent', 'axis': 'Delay',  # theme tokens, looked up when drawn
                  'mean': 'mean {value:.0f} ms', 'value': '{:.1f} ms'},
        'skew': {'color': 'skew', 'axis': 'Skew',
                 'mean': 'mean {value:+.1f} ms', 'value': '{:+.2f} ms'},
    }

    INITIAL_DELAY_TOP = 500.0  # starting y-range of the delay chart, in ms

    def __init__(self, axis_cls, parent=None, thresholds=THRESHOLDS):
        super().__init__(parent)
        self.samples_limit = 100
        self.paused = False
        self.mode = 'delay'
        self.high_envelope = self.INITIAL_DELAY_TOP
        self.thresholds = thresholds
        self._sources = ((), ())
        self._last_mouse = None
        main = QtWidgets.QVBoxLayout(self)
        main.setContentsMargins(4, 6, 12, 4)
        self.axis = axis_cls('left')
        self.plot = pg.PlotWidget(axisItems={'left': self.axis, 'bottom': FramesAgoAxis('bottom')})
        self.plot.setBackground(COLORS['surface'])
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        for name in ('left', 'bottom'):
            axis = self.plot.getAxis(name)
            axis.setPen(pg.mkPen(COLORS['border_strong']))
            axis.setTextPen(pg.mkPen(COLORS['secondary']))
            axis.setTickFont(ui_font(11))
        self.axis.setWidth(64)
        self.plot.setLabel('bottom', 'Frames ago', color=COLORS['muted'], size='9pt')
        self.plot.showGrid(x=False, y=True, alpha=0.10)
        self.plot.setMouseEnabled(x=False, y=False)
        self.bands = {}
        for name, color in (('warn_hi', 'warning'), ('bad_hi', 'bad'),
                            ('warn_lo', 'warning'), ('bad_lo', 'bad')):
            band = pg.LinearRegionItem(values=(0, 1), orientation='horizontal', movable=False,
                                       brush=pg.mkBrush(tint(COLORS[color], 10)),
                                       pen=pg.mkPen(tint(COLORS[color], 110), width=1,
                                                    style=QtCore.Qt.PenStyle.DotLine))
            band.setZValue(-20)
            self.plot.addItem(band, ignoreBounds=True)
            self.bands[name] = band
        self.zero_line = pg.InfiniteLine(angle=0, pos=0, pen=pg.mkPen(COLORS['border_strong']))
        self.plot.addItem(self.zero_line, ignoreBounds=True)
        self.line = self.plot.plot(pen=pg.mkPen(COLORS['accent'], width=2))
        self.line.setClipToView(True)
        self.line.setDownsampling(auto=True)
        self.line.setFillLevel(0)
        self.mean_line = pg.InfiniteLine(
            angle=0, pen=pg.mkPen(COLORS['secondary'], width=1, style=QtCore.Qt.PenStyle.DashLine),
            label='mean {value:.0f} ms',
            labelOpts={'position': 0.985, 'color': COLORS['secondary'],
                       'anchors': [(1, 1), (1, 1)], 'fill': tint(COLORS['surface'], 210)})
        self.mean_line.label.setFont(ui_font(11, QtGui.QFont.Weight.DemiBold))
        self.plot.addItem(self.mean_line, ignoreBounds=True)
        self.mean_line.hide()
        self.crosshair = pg.InfiniteLine(angle=90, movable=False,
                                         pen=pg.mkPen(COLORS['border_strong'], width=1))
        self.plot.addItem(self.crosshair, ignoreBounds=True)
        self.crosshair.hide()
        self.marker = pg.ScatterPlotItem(size=9, pen=pg.mkPen(COLORS['surface'], width=2))
        self.marker.setZValue(30)
        self.plot.addItem(self.marker, ignoreBounds=True)
        self.hover_label = pg.TextItem('', color=COLORS['text'], fill=pg.mkBrush(COLORS['raised']),
                                       border=pg.mkPen(COLORS['border_strong']), anchor=(0, 0))
        self.hover_label.setFont(numeric_font(12))
        self.hover_label.setZValue(40)
        self.plot.addItem(self.hover_label, ignoreBounds=True)
        self.hover_label.hide()
        self.placeholder = pg.TextItem('Waiting for valid frames…', color=COLORS['muted'],
                                       anchor=(0.5, 0.5))
        self.placeholder.setFont(ui_font(13))
        self.plot.addItem(self.placeholder, ignoreBounds=True)
        main.addWidget(self.plot, stretch=1)
        self.plot.scene().sigMouseMoved.connect(self._mouse_moved)
        self.plot.viewport().installEventFilter(self)
        self._xdata = np.empty(2000, dtype=float)
        self._ydata = np.empty(2000, dtype=float)
        self._display_count = 0
        self._last_mean = 0.0
        self.plot.getViewBox().sigYRangeChanged.connect(self._axis_changed)
        self.set_mode('delay')

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.Leave:
            self._last_mouse = None
            self._hide_hover()
        return False

    def _axis_changed(self, *args):
        if self.mode == 'delay':
            lo, hi = self.plot.getViewBox().viewRange()[1]
            self.axis.update_for_range(lo, hi)

    def set_window(self, samples: int):
        self.samples_limit = int(samples)
        self.high_envelope = self.INITIAL_DELAY_TOP if self.mode == 'delay' else 30.0
        self._display_count = 0
        self._render(force=True)

    def set_paused(self, paused: bool):
        self.paused = bool(paused)

    def set_thresholds(self, thresholds):
        """New OK/Watch/High limits: redraw the bands and rescale at once."""
        self.thresholds = thresholds
        self.set_mode(self.mode)

    def set_mode(self, mode):
        self.mode = mode
        spec = self.MODES[mode]
        color = COLORS.get(spec['color'], spec['color'])
        self.line.setPen(pg.mkPen(color, width=2))
        self.line.setBrush(_fade_brush(color))
        self.marker.setBrush(pg.mkBrush(color))
        self.mean_line.label.setFormat(spec['mean'])
        self.high_envelope = self.INITIAL_DELAY_TOP if mode == 'delay' else 30.0
        self._display_count = 0
        self.axis.enableAutoSIPrefix(False)
        t = self.thresholds
        self.axis.unit = 'ms'
        if hasattr(self.axis, 'quantity'):
            self.axis.quantity = spec['axis']
        self.axis.setLabel(f"{spec['axis']} (ms)", color=COLORS['secondary'], size='10pt')
        if mode == 'delay':
            self.axis.update_for_range(0, self.high_envelope)
            spans = {'warn_hi': (t.delay_good_below_ms, t.delay_warn_below_ms),
                     'bad_hi': (t.delay_warn_below_ms, 1e6)}
        else:
            spans = {'warn_hi': (t.skew_good_below_ms, t.skew_warn_below_ms),
                     'bad_hi': (t.skew_warn_below_ms, 1e6),
                     'warn_lo': (-t.skew_warn_below_ms, -t.skew_good_below_ms),
                     'bad_lo': (-1e6, -t.skew_warn_below_ms)}
        for name, band in self.bands.items():
            band.setVisible(name in spans)
            if name in spans:
                band.setRegion(spans[name])
        self.zero_line.setVisible(mode == 'skew')
        self._hide_hover()
        self._render(force=True)

    def update_samples(self, delays, skews):
        self._sources = (delays, skews)
        if not self.paused:
            self._render()

    def _render(self, force=False):
        values = self._sources[0] if self.mode == 'delay' else self._sources[1]
        count = min(self.samples_limit, len(values))
        if not count:
            self._display_count = 0
            self.line.setData([], [])
            self.mean_line.hide()
            self.plot.setXRange(-self.samples_limit + 1, 0, padding=0.01)
            low = 0.0 if self.mode == 'delay' else -self.high_envelope
            self.plot.setYRange(low, self.high_envelope, padding=0)
            self.placeholder.setPos(-(self.samples_limit - 1) / 2, (low + self.high_envelope) / 2)
            self.placeholder.show()
            return
        self.placeholder.hide()
        samples = list(values)[-count:]
        self._xdata[:count] = np.arange(-count + 1, 1, dtype=float)
        self._ydata[:count] = samples
        self._display_count = count
        self.line.setData(self._xdata[:count], self._ydata[:count])
        self._last_mean = float(np.mean(self._ydata[:count]))
        self.mean_line.setValue(self._last_mean)
        self.mean_line.show()
        vmin = float(np.min(self._ydata[:count]))
        vmax = float(np.max(self._ydata[:count]))
        if self.mode == 'delay':
            floor = 0.0
            desired_top = max(vmax * 1.2, self._delay_min_top())
        else:
            floor = min(-self.thresholds.skew_good_below_ms * 1.5, vmin * 1.3)
            desired_top = max(20.0, vmax * 1.3)
        # Fast expansion, slow reduction to prevent visual jumping.
        self.high_envelope = max(desired_top, self.high_envelope * .985)
        # Strip chart: the axis always spans the selected window. New frames
        # enter at the right and the history slides left; it never rescales.
        self.plot.setXRange(-(self.samples_limit - 1), 0, padding=0.01)
        self.plot.setYRange(floor, max(floor + 1, self.high_envelope), padding=0)
        self._axis_changed()
        if self._last_mouse is not None:
            self._mouse_moved(self._last_mouse)

    def _delay_min_top(self):
        """Lowest top of the delay y-axis: the OK band stays in view."""
        return self.thresholds.delay_good_below_ms * 1.15

    def _hide_hover(self):
        self.crosshair.hide()
        self.marker.hide()
        self.hover_label.hide()

    def _mouse_moved(self, pos):
        self._last_mouse = pos
        if not self._display_count or not self.plot.sceneBoundingRect().contains(pos):
            self._hide_hover()
            return
        view = self.plot.getViewBox()
        point = view.mapSceneToView(pos)
        i = int(round(point.x() + self._display_count - 1))
        if i < 0 or i >= self._display_count:
            self._hide_hover()
            return
        x, y = self._xdata[i], self._ydata[i]
        self.crosshair.setPos(x)
        self.crosshair.show()
        self.marker.setData([x], [y])
        self.marker.show()
        value = self.MODES[self.mode]['value'].format(y)
        self.hover_label.setText(f'{value}\n{abs(int(x))} frames ago')
        (x_lo, x_hi), (_, y_hi) = view.viewRange()
        right_half = x > x_lo + 0.62 * (x_hi - x_lo)
        self.hover_label.setAnchor((1.08, 0) if right_half else (-0.08, 0))
        self.hover_label.setPos(x, y_hi)
        self.hover_label.show()


class _NumberItem(QtWidgets.QTableWidgetItem):
    """Table cell that shows formatted text but sorts by its numeric value."""
    def __init__(self, value, text):
        super().__init__(text)
        self.setData(QtCore.Qt.ItemDataRole.UserRole, float(value))
        self.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)

    def __lt__(self, other):
        return (self.data(QtCore.Qt.ItemDataRole.UserRole)
                < other.data(QtCore.Qt.ItemDataRole.UserRole))


class DetectionTable(QtWidgets.QTableWidget):
    """Read-only table; Ctrl+C copies the selected rows (or all) as tab-separated text."""
    def keyPressEvent(self, event):
        if event.matches(QtGui.QKeySequence.StandardKey.Copy):
            self.copy_rows()
            return
        super().keyPressEvent(event)

    def copy_rows(self):
        rows = sorted({index.row() for index in self.selectedIndexes()}) or range(self.rowCount())
        columns = range(self.columnCount())
        lines = ['\t'.join(self.horizontalHeaderItem(c).text() for c in columns)]
        for r in rows:
            lines.append('\t'.join(self.item(r, c).text() if self.item(r, c) else '' for c in columns))
        text = '\n'.join(lines)
        QtWidgets.QApplication.clipboard().setText(text)
        return text


class DetectionsPanel(QtWidgets.QWidget):
    """Detections of the expanded camera: class, ID, confidence, centre and size."""
    COLUMNS = ('Class', 'ID', 'Confidence', 'Center x', 'Center y', 'Width', 'Height')

    def __init__(self, parent=None):
        super().__init__(parent)
        self.paused = False
        self.side = 'LEFT'
        self.enabled = True
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 10)
        layout.setSpacing(6)
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(10)
        self.caption = QtWidgets.QLabel()
        self.caption.setObjectName('secondary')
        self.caption.setFont(ui_font(12, QtGui.QFont.Weight.DemiBold))
        self.note = QtWidgets.QLabel('Native camera pixels, origin top-left · click a header to sort')
        self.note.setObjectName('caption')
        self.note.setFont(ui_font(12))
        self.copy_button = QtWidgets.QPushButton('Copy')
        self.copy_button.setObjectName('flat')
        self.copy_button.setIcon(icon('copy', COLORS['secondary']))
        self.copy_button.setIconSize(QtCore.QSize(14, 14))
        self.copy_button.setToolTip('Copy the selected rows (or all rows) as tab-separated text · Ctrl+C')
        top.addWidget(self.caption)
        top.addStretch()
        top.addWidget(self.note)
        top.addWidget(self.copy_button)
        layout.addLayout(top)
        self.table = DetectionTable(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.horizontalHeader().setFont(display_font(11))
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setDefaultAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.table.horizontalHeaderItem(0).setTextAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setFont(numeric_font(13))
        self.table.setSortingEnabled(True)
        self.table.sortItems(3, QtCore.Qt.SortOrder.AscendingOrder)  # along the belt, left to right
        self.placeholder = QtWidgets.QLabel()
        self.placeholder.setObjectName('caption')
        self.placeholder.setFont(ui_font(13))
        self.placeholder.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.views = QtWidgets.QStackedWidget()
        self.views.addWidget(self.table)
        self.views.addWidget(self.placeholder)
        layout.addWidget(self.views, 1)
        self.copy_button.clicked.connect(self.table.copy_rows)
        self.set_detections((), stale=False)

    def set_side(self, side: str):
        self.side = side.upper()
        self._update_caption(self.table.rowCount())

    def set_paused(self, paused: bool):
        self.paused = bool(paused)
        self._update_caption(self.table.rowCount())

    def set_enabled(self, enabled: bool):
        self.enabled = bool(enabled)

    def set_detections(self, detections, stale=False):
        """Rows for this camera's latest detections; frozen while paused."""
        if self.paused:
            return
        if not self.enabled:
            self._show_placeholder('Detection is off · choose a YOLO model with the Model button')
            return
        if stale:
            self.table.setRowCount(0)
            self._show_placeholder('Stream stalled · no live detections')
            return
        rows = [d for d in detections if d.side == self.side]
        sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for r, d in enumerate(rows):
            x1, y1, x2, y2 = d.xyxy
            cx, cy = d.center
            name = QtWidgets.QTableWidgetItem(d.label)
            name.setData(QtCore.Qt.ItemDataRole.DecorationRole, QtGui.QColor(class_color(d.label)))
            name.setFont(ui_font(13, QtGui.QFont.Weight.DemiBold))
            self.table.setItem(r, 0, name)
            cells = ((d.class_id, f'{d.class_id}' if d.class_id >= 0 else '—'),
                     (d.confidence, f'{d.confidence:.0%}'),
                     (cx, f'{cx:.0f}'), (cy, f'{cy:.0f}'),
                     (x2 - x1, f'{x2 - x1:.0f}'), (y2 - y1, f'{y2 - y1:.0f}'))
            for c, (value, text) in enumerate(cells, start=1):
                self.table.setItem(r, c, _NumberItem(value, text))
        self.table.setSortingEnabled(sorting)
        if rows:
            self.views.setCurrentWidget(self.table)
            self._update_caption(len(rows))
        else:
            self._show_placeholder('No detections in the latest result')

    def _show_placeholder(self, message):
        self.placeholder.setText(message)
        self.views.setCurrentWidget(self.placeholder)
        self._update_caption(0)

    def _update_caption(self, count):
        objects = 'no objects' if not count else ('1 object' if count == 1 else f'{count} objects')
        text = f'{self.side.title()} camera · {objects}' + (' · paused' if self.paused else '')
        if self.caption.text() != text:
            self.caption.setText(text)


class AnalysisCard(QtWidgets.QFrame):
    """Delay and skew history, detections (single-camera view) and diagnostics as tabs."""
    tab_changed = QtCore.Signal(str)
    limits_requested = QtCore.Signal()
    TABS = (('delay', 'Frame delay', 'NTP-corrected frame delay history · raw samples'),
            ('skew', 'Camera skew', 'Right-minus-left camera timestamp history · raw samples'),
            ('detections', 'Detections',
             'Class, ID, confidence, centre and size of every detection in the expanded camera'),
            ('diagnostics', 'Diagnostics',
             'Timestamp barcode, CRC counters, clock and link details · shortcut D'))
    CHART_TABS = ('delay', 'skew')
    PAUSABLE_TABS = ('delay', 'skew', 'detections')
    # Subclasses (the server console) swap in their own chart and diagnostics page.
    CHART_CLS = LatencyChart
    DIAGNOSTICS_CLS = DiagnosticsPanel
    FIRST_TAB = 'delay'

    def __init__(self, axis_cls, parent=None, thresholds=THRESHOLDS):
        super().__init__(parent)
        self.setObjectName('card')
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        header = QtWidgets.QFrame()
        header.setObjectName('cardHeader')
        row = QtWidgets.QHBoxLayout(header)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(10)
        self.tabs = SegmentedControl(self.TABS)
        self.legend = QtWidgets.QLabel()
        self.legend.setObjectName('legend')
        self.legend.setFont(ui_font(12))
        self.legend.setToolTip('Status limits for this workstation · edit them with Limits')
        self.limits_button = QtWidgets.QPushButton('Limits')
        self.limits_button.setObjectName('flat')
        self.limits_button.setIcon(icon('gauge', COLORS['secondary']))
        self.limits_button.setIconSize(QtCore.QSize(16, 16))
        self.limits_button.setToolTip('Edit the OK / Watch / High limits')
        self.limits_button.clicked.connect(self.limits_requested)
        self.window = SegmentedControl((('100', '100', 'Show the last 100 frames'),
                                        ('500', '500', 'Show the last 500 frames'),
                                        ('2000', '2000', 'Show the last 2000 frames')))
        self.window_caption = QtWidgets.QLabel('frames')
        self.window_caption.setObjectName('caption')
        self.window_caption.setFont(ui_font(12))
        self.pause = QtWidgets.QToolButton()
        self.pause.setCheckable(True)
        self.pause.setIcon(icon('pause', COLORS['secondary']))
        self.pause.setIconSize(QtCore.QSize(16, 16))
        self.pause.setToolTip('Pause updates · shortcut P')
        row.addWidget(self.tabs)
        row.addSpacing(6)
        row.addWidget(self.legend)
        row.addWidget(self.limits_button)
        row.addStretch()
        row.addWidget(self.window)
        row.addWidget(self.window_caption)
        row.addWidget(self.pause)
        layout.addWidget(header)
        self.stack = QtWidgets.QStackedWidget()
        self.chart = self.CHART_CLS(axis_cls, thresholds=thresholds)
        self.detections = DetectionsPanel()
        self.diagnostics = self.DIAGNOSTICS_CLS()
        for page in (self.chart, self.detections, self.diagnostics):
            self.stack.addWidget(page)
        layout.addWidget(self.stack, 1)
        self.current = None
        self._last_chart_tab = self.FIRST_TAB
        if 'detections' in self.tabs.buttons:
            self.tabs.buttons['detections'].hide()  # single-camera view only
        self.tabs.selected.connect(self.show_tab)
        self.window.selected.connect(lambda value: self.chart.set_window(int(value)))
        self.pause.toggled.connect(self._set_paused)
        self.show_tab(self.FIRST_TAB, emit=False)

    def show_tab(self, key: str, emit=True):
        if key == self.current:
            return
        self.current = key
        if self.tabs.current != key:
            self.tabs.select(key, emit=False)
        chart_tab = key in self.CHART_TABS
        if chart_tab:
            self._last_chart_tab = key
            self.chart.set_mode(key)
            self.stack.setCurrentWidget(self.chart)
        elif key == 'detections':
            self.stack.setCurrentWidget(self.detections)
        else:
            self.stack.setCurrentWidget(self.diagnostics)
        for widget in (self.window, self.window_caption, self.limits_button):
            widget.setVisible(chart_tab)
        self.pause.setVisible(key in self.PAUSABLE_TABS)
        self._update_legend()
        if emit:
            self.tab_changed.emit(key)

    def set_detections_side(self, side):
        """'left' / 'right' shows the Detections tab for that camera; None hides it."""
        button = self.tabs.buttons.get('detections')
        if button is None:
            return
        if side is None:
            if self.current == 'detections':
                self.show_tab(self._last_chart_tab)
            button.hide()
        else:
            self.detections.set_side(side)
            button.show()
        self._fit_legend()

    def set_thresholds(self, thresholds):
        self.chart.set_thresholds(thresholds)
        self._update_legend()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_legend()

    def _fit_legend(self):
        """The threshold legend is the first thing to go when the card is narrow."""
        chart_tab = self.current in self.CHART_TABS
        parts = (self.tabs, self.legend, self.limits_button, self.window, self.window_caption, self.pause)
        needed = sum(part.sizeHint().width() for part in parts) + 7 * 10 + 20
        self.legend.setVisible(chart_tab and self.width() >= needed)

    def toggle_diagnostics(self):
        self.show_tab(self._last_chart_tab if self.current == 'diagnostics' else 'diagnostics')

    def toggle_pause(self):
        if self.current in self.PAUSABLE_TABS:
            self.pause.toggle()

    def _set_paused(self, paused):
        self.chart.set_paused(paused)
        self.detections.set_paused(paused)
        self.pause.setIcon(icon('play' if paused else 'pause',
                                COLORS['accent'] if paused else COLORS['secondary']))
        self.pause.setToolTip(('Resume' if paused else 'Pause') + ' updates · shortcut P')

    def _update_legend(self):
        t = self.chart.thresholds
        if self.current == 'skew':
            watch, high = f'|skew| ≥ {t.skew_good_below_ms:g} ms', f'≥ {t.skew_warn_below_ms:g} ms'
        else:
            watch, high = f'≥ {t.delay_good_below_ms:g} ms', f'≥ {t.delay_warn_below_ms:g} ms'
        self.legend.setText(
            f'<span style="color:{COLORS["warning"]}">■</span> watch {watch}'
            f'&nbsp;&nbsp;&nbsp;<span style="color:{COLORS["bad"]}">■</span> high {high}')
        self._fit_legend()
