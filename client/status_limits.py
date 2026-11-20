"""Operator-configurable OK / Watch / High limits for frame delay and camera skew.

The defaults in theme.THRESHOLDS are provisional display values, not validated
competition limits. Each workstation can set its own in a modal dialog; they
drive the KPI colours and badges and the chart's threshold bands.
"""
from __future__ import annotations

from dataclasses import replace

from PySide6 import QtCore, QtGui, QtWidgets

from control_room_widgets import overline
from icons import icon, pixmap
from theme import (COLORS, STATE_COLORS, STATE_MARKS, STATE_WORDS, THRESHOLDS,
                   Thresholds, numeric_font, quality, tint, ui_font)

# (key, title, scope, OK field, Watch field, decimals, step, maximum)
METRICS = (
    ('delay', 'Frame delay', 'Average delay, delay P95 and the frame-delay chart',
     'delay_good_below_ms', 'delay_warn_below_ms', 0, 10.0, 10000.0),
    ('skew', 'Camera skew |R − L|', 'Smoothed camera skew and the skew chart (absolute value)',
     'skew_good_below_ms', 'skew_warn_below_ms', 1, 0.5, 1000.0),
)
EDITABLE_FIELDS = tuple(field for spec in METRICS for field in spec[3:5])


def load_thresholds(qsettings: QtCore.QSettings) -> Thresholds:
    """This workstation's limits; any missing or inconsistent pair keeps its default."""
    values = {}
    for _, _, _, ok_field, watch_field, *_ in METRICS:
        try:
            ok = float(qsettings.value(f'limits/{ok_field}', getattr(THRESHOLDS, ok_field)))
            watch = float(qsettings.value(f'limits/{watch_field}', getattr(THRESHOLDS, watch_field)))
        except (TypeError, ValueError):
            continue
        if 0 < ok < watch:
            values[ok_field], values[watch_field] = ok, watch
    return replace(THRESHOLDS, **values)


def save_thresholds(qsettings: QtCore.QSettings, thresholds: Thresholds):
    for field in EDITABLE_FIELDS:
        qsettings.setValue(f'limits/{field}', float(getattr(thresholds, field)))


def _fmt(value, decimals):
    return f'{value:.{decimals}f}'


class ThresholdScale(QtWidgets.QWidget):
    """OK / Watch / High zones as a bar, with a marker for the current reading."""

    def __init__(self, decimals=0, parent=None):
        super().__init__(parent)
        self.decimals = decimals
        self.ok, self.watch = 1.0, 2.0
        self.marker = None  # (value, label) or None
        self.setFixedHeight(48)

    def set_values(self, ok, watch, marker=None):
        self.ok, self.watch, self.marker = ok, watch, marker
        self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        left, right = 6.0, self.width() - 6.0
        top = max(self.watch * 1.5, (abs(self.marker[0]) * 1.15) if self.marker else 0.0)

        def x(value):
            return left + (right - left) * max(0.0, min(value, top)) / top

        bar = QtCore.QRectF(left, 20, right - left, 8)
        path = QtGui.QPainterPath()
        path.addRoundedRect(bar, 4, 4)
        p.save()
        p.setClipPath(path)
        for start, end, state in ((0.0, self.ok, 'good'), (self.ok, self.watch, 'warning'),
                                  (self.watch, top, 'bad')):
            p.fillRect(QtCore.QRectF(x(start), bar.top(), x(end) - x(start), bar.height()),
                       tint(STATE_COLORS[state], 200))
        p.restore()
        p.setFont(numeric_font(11))
        p.setPen(QtGui.QColor(COLORS['secondary']))
        for value in (self.ok, self.watch):
            text = _fmt(value, self.decimals)
            rect = QtCore.QRectF(x(value) - 40, 31, 80, 16)
            p.drawText(rect, QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.AlignmentFlag.AlignTop, text)
        if self.marker is not None:
            value, label = self.marker
            mx = x(abs(value))
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(QtGui.QColor(COLORS['text']))
            p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(mx - 5, 11), QtCore.QPointF(mx + 5, 11),
                                           QtCore.QPointF(mx, 18)]))
            p.setFont(ui_font(11, QtGui.QFont.Weight.DemiBold))
            p.setPen(QtGui.QColor(COLORS['text']))
            width = QtGui.QFontMetrics(p.font()).horizontalAdvance(label) + 4
            lx = max(0.0, min(self.width() - width, mx - width / 2))
            p.drawText(QtCore.QRectF(lx, -1, width, 13), QtCore.Qt.AlignmentFlag.AlignCenter, label)
        p.end()


class LimitsDialog(QtWidgets.QDialog):
    """Modal editor for the OK / Watch / High limits.

    OK and Watch stay consistent (Watch is always above OK): raising OK past
    Watch pushes Watch up, lowering Watch below OK pushes OK down. Values
    commit on Enter, focus change or the arrows, never mid-typing.
    """

    def __init__(self, thresholds: Thresholds, current=None, parent=None):
        super().__init__(parent, QtCore.Qt.WindowType.Dialog | QtCore.Qt.WindowType.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setWindowTitle('Status limits')
        self._base = thresholds
        self._current = current or {}
        self._spins = {}
        self._high = {}
        self._scales = {}
        self._now = {}
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(16, 10, 16, 22)  # room for the drop shadow
        card = QtWidgets.QFrame()
        card.setObjectName('popoverCard')
        shadow = QtWidgets.QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 10)
        shadow.setColor(QtGui.QColor(0, 0, 0, 170))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)
        root = QtWidgets.QVBoxLayout(card)
        root.setContentsMargins(20, 16, 16, 16)
        root.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(10)
        glyph = QtWidgets.QLabel()
        glyph.setPixmap(pixmap('gauge', COLORS['accent'], 18))
        title = QtWidgets.QLabel('Status limits')
        title.setObjectName('cardTitle')
        title.setFont(ui_font(16, QtGui.QFont.Weight.DemiBold))
        close = QtWidgets.QToolButton()
        close.setIcon(icon('close', COLORS['secondary']))
        close.setIconSize(QtCore.QSize(16, 16))
        close.setToolTip('Close without saving · Esc')
        close.clicked.connect(self.reject)
        header.addWidget(glyph)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(close)
        root.addLayout(header)
        intro = QtWidgets.QLabel('Choose when readings show OK, Watch or High. Saved on this '
                                 'workstation. The defaults are provisional, not validated '
                                 'competition limits.')
        intro.setObjectName('secondary')
        intro.setFont(ui_font(12))
        intro.setWordWrap(True)
        root.addWidget(intro)

        for key, name, scope, ok_field, watch_field, decimals, step, maximum in METRICS:
            root.addSpacing(4)
            root.addWidget(self._divider())
            root.addWidget(overline(name))
            scope_label = QtWidgets.QLabel(scope)
            scope_label.setObjectName('caption')
            scope_label.setFont(ui_font(12))
            root.addWidget(scope_label)
            grid = QtWidgets.QGridLayout()
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(6)
            ok_spin = self._make_spin(decimals, step, maximum, getattr(thresholds, ok_field))
            watch_spin = self._make_spin(decimals, step, maximum, getattr(thresholds, watch_field))
            ok_spin.setToolTip('Readings below this value show OK')
            watch_spin.setToolTip('Readings from OK up to this value show Watch; at or above it, High')
            for row, (state, widget) in enumerate((('good', ok_spin), ('warning', watch_spin))):
                grid.addWidget(self._state_label(state), row, 0)
                below = QtWidgets.QLabel('below')
                below.setObjectName('secondary')
                below.setFont(ui_font(13))
                grid.addWidget(below, row, 1)
                grid.addWidget(widget, row, 2)
            grid.addWidget(self._state_label('bad'), 2, 0)
            high = QtWidgets.QLabel()
            high.setObjectName('secondary')
            high.setFont(ui_font(13))
            grid.addWidget(high, 2, 1, 1, 2)
            grid.setColumnStretch(3, 1)
            root.addLayout(grid)
            scale = ThresholdScale(decimals)
            root.addWidget(scale)
            now = QtWidgets.QLabel()
            now.setObjectName('secondary')
            now.setFont(ui_font(12))
            root.addWidget(now)
            self._spins[key] = (ok_spin, watch_spin, step, decimals)
            self._high[key] = high
            self._scales[key] = scale
            self._now[key] = now
            ok_spin.valueChanged.connect(lambda value, k=key: self._ok_changed(k, value))
            watch_spin.valueChanged.connect(lambda value, k=key: self._watch_changed(k, value))

        root.addSpacing(4)
        root.addWidget(self._divider())
        footer = QtWidgets.QHBoxLayout()
        self.defaults_button = QtWidgets.QPushButton('Restore defaults')
        self.defaults_button.setObjectName('flat')
        self.defaults_button.setToolTip('Back to the provisional defaults from client/theme.py')
        self.defaults_button.setAutoDefault(False)
        self.defaults_button.clicked.connect(self.restore_defaults)
        cancel = QtWidgets.QPushButton('Cancel')
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        self.save_button = QtWidgets.QPushButton('Save')
        self.save_button.setObjectName('primary')
        self.save_button.setMinimumWidth(88)
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.accept)
        footer.addWidget(self.defaults_button)
        footer.addStretch()
        footer.addWidget(cancel)
        footer.addWidget(self.save_button)
        root.addLayout(footer)
        self.setMinimumWidth(460)
        for key in self._spins:
            self._sync(key)

    @staticmethod
    def _divider():
        line = QtWidgets.QFrame()
        line.setObjectName('divider')
        line.setFixedHeight(1)
        return line

    @staticmethod
    def _state_label(state):
        label = QtWidgets.QLabel(f'{STATE_MARKS[state]}  {STATE_WORDS[state]}')
        label.setFont(ui_font(13, QtGui.QFont.Weight.DemiBold))
        label.setStyleSheet(f'color:{STATE_COLORS[state]};')
        label.setMinimumWidth(78)
        return label

    @staticmethod
    def _make_spin(decimals, step, maximum, value):
        spin = QtWidgets.QDoubleSpinBox()
        spin.setDecimals(decimals)
        spin.setSingleStep(step)
        spin.setRange(step, maximum)
        spin.setValue(value)
        spin.setSuffix(' ms')
        spin.setKeyboardTracking(False)
        spin.setFont(numeric_font(13))
        spin.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        spin.setFixedWidth(128)
        return spin

    @property
    def thresholds(self) -> Thresholds:
        values = {}
        for spec in METRICS:
            ok_spin, watch_spin, *_ = self._spins[spec[0]]
            values[spec[3]] = ok_spin.value()
            values[spec[4]] = watch_spin.value()
        return replace(self._base, **values)

    def restore_defaults(self):
        defaults = Thresholds()
        for spec in METRICS:
            ok_spin, watch_spin, *_ = self._spins[spec[0]]
            for spin, field in ((ok_spin, spec[3]), (watch_spin, spec[4])):
                spin.blockSignals(True)
                spin.setValue(getattr(defaults, field))
                spin.blockSignals(False)
            self._sync(spec[0])

    def _ok_changed(self, key, value):
        ok_spin, watch_spin, step, _ = self._spins[key]
        if watch_spin.value() <= value:
            watch_spin.blockSignals(True)
            watch_spin.setValue(value + step)
            watch_spin.blockSignals(False)
        self._sync(key)

    def _watch_changed(self, key, value):
        ok_spin, watch_spin, step, _ = self._spins[key]
        if ok_spin.value() >= value:
            ok_spin.blockSignals(True)
            ok_spin.setValue(value - step)
            ok_spin.blockSignals(False)
            if ok_spin.value() >= watch_spin.value():  # OK hit its minimum
                watch_spin.blockSignals(True)
                watch_spin.setValue(ok_spin.value() + step)
                watch_spin.blockSignals(False)
        self._sync(key)

    def _sync(self, key):
        ok_spin, watch_spin, _, decimals = self._spins[key]
        ok, watch = ok_spin.value(), watch_spin.value()
        self._high[key].setText(f'at or above {_fmt(watch, decimals)} ms')
        if key == 'delay':
            readings = [('average', 'average'), ('p95', 'P95')]
        else:
            readings = [('skew', 'skew')]
        present = [(name, self._current.get(field)) for field, name in readings
                   if self._current.get(field) is not None]
        marker = (present[0][1], f'now {_fmt(present[0][1], decimals)}') if present else None
        self._scales[key].set_values(ok, watch, marker)
        if not present:
            self._now[key].setText('No readings yet')
            return
        parts = []
        for name, value in present:
            state = quality(value, ok, watch)
            parts.append(f'{name} {_fmt(value, decimals)} ms → '
                         f'<span style="color:{STATE_COLORS[state]}">{STATE_WORDS[state]}</span>')
        self._now[key].setText('Now: ' + ' &nbsp;·&nbsp; '.join(parts))
