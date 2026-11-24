"""Output pill and panel: see the detection messages the client publishes over UDP.

The app-bar **Output** pill shows the send rate and whether anything listens;
its status mark ripples each time a message leaves. Clicking it opens a panel
to switch publishing on or off, set the destination, and watch the latest
message as formatted JSON (pause to read it, copy it as an example).
"""
from __future__ import annotations

import json
import time

from PySide6 import QtCore, QtGui, QtWidgets

from control_room_widgets import StatusChip, ToggleSwitch, set_state
from icons import icon, pixmap
from theme import COLORS, numeric_font, ui_font

PULSE_S = 0.35


def output_pill(status):
    """(state, message, tooltip) for the Output pill from DetectionPublisher.status()."""
    target = status['target']
    if not status['enabled']:
        return ('neutral', 'Off', f'Detection output is off · click to publish detections to UDP {target}')
    since = status['since_last']
    rate = status['rate']
    base = (f'Publishing JSON over UDP to {target}\n'
            f'{status["sent"]:,} messages · {status["bytes"] / 1024:,.0f} kB sent')
    if status['errors'] and status['last_error'] and since is None:
        return 'bad', 'Send failed', f'{base}\nLast error: {status["last_error"]}'
    if since is None:
        return 'info', 'Starting', base
    if status['no_listener']:
        return ('warning', 'No listener',
                f'{base}\nNothing is listening on {target}: Windows answers "port unreachable".\n'
                'Start the receiving program, or check the port.')
    detections = status['since_detection'] is not None and status['since_detection'] < 2.0
    if not detections:
        return ('info', 'Status only',
                f'{base}\nNo detections to send (detection off, model loading or no video):\n'
                'a status message goes out once a second instead.')
    listener = ('A program is listening (no "port unreachable" replies).' if status['local'] else
                'Sent to another PC: delivery can\'t be confirmed from here.')
    return 'good', f'{rate:.1f} msg/s', f'{base}\n{listener}'


class OutputChip(StatusChip):
    """Status pill whose mark ripples once per message sent: a send-activity light."""

    def __init__(self, label, parent=None, min_width=150):
        super().__init__(label, parent, min_width)
        self._sent_at = None
        self._pulse = 0.0
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._animate)

    def notify_sent(self):
        self._sent_at = time.monotonic()
        self._animate()
        if not self._timer.isActive():
            self._timer.start()

    @property
    def pulse(self):
        return self._pulse

    def _animate(self):
        pulse = 0.0 if self._sent_at is None else max(0.0, 1.0 - (time.monotonic() - self._sent_at) / PULSE_S)
        if pulse != self._pulse:
            self._pulse = pulse
            self.update()
        if pulse <= 0.0:
            self._timer.stop()

    def _paint_mark(self, p, cx, cy, color):
        super()._paint_mark(p, cx, cy, color)
        if self._pulse <= 0.0:
            return
        p.save()
        ring = QtGui.QColor(color)
        ring.setAlphaF(0.9 * self._pulse)
        p.setPen(QtGui.QPen(ring, 1.8))
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        radius = 5.0 + 5.0 * (1.0 - self._pulse)
        p.drawEllipse(QtCore.QPointF(cx, cy), radius, radius)
        p.restore()


class OutputPopover(QtWidgets.QFrame):
    """Switch, destination, counters and the latest message; a popup like the Model list."""
    enabled_changed = QtCore.Signal(bool)
    target_changed = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent, QtCore.Qt.WindowType.Popup | QtCore.Qt.WindowType.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoMouseReplay, True)
        self._shown_message = None
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(16, 10, 16, 22)
        card = QtWidgets.QFrame()
        card.setObjectName('popoverCard')
        shadow = QtWidgets.QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QtGui.QColor(0, 0, 0, 150))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)
        root = QtWidgets.QVBoxLayout(card)
        root.setContentsMargins(18, 14, 14, 14)
        root.setSpacing(10)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(10)
        glyph = QtWidgets.QLabel()
        glyph.setPixmap(pixmap('pulse', COLORS['accent'], 18))
        title = QtWidgets.QLabel('Detection output')
        title.setObjectName('cardTitle')
        title.setFont(ui_font(15, QtGui.QFont.Weight.DemiBold))
        close = QtWidgets.QToolButton()
        close.setIcon(icon('close', COLORS['secondary']))
        close.setIconSize(QtCore.QSize(16, 16))
        close.setToolTip('Close · Esc')
        close.clicked.connect(self.close)
        header.addWidget(glyph)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(close)
        root.addLayout(header)

        intro = QtWidgets.QLabel('Sends both cameras\' detections as one JSON message per processed frame, '
                                 'with the camera capture time, for a receiving program such as the robot backend.')
        intro.setObjectName('secondary')
        intro.setFont(ui_font(12))
        intro.setWordWrap(True)
        root.addWidget(intro)

        controls = QtWidgets.QHBoxLayout()
        controls.setSpacing(12)
        self.switch = ToggleSwitch('Publish over UDP')
        self.switch.setFont(ui_font(13, QtGui.QFont.Weight.DemiBold))
        self.switch.toggled.connect(self.enabled_changed)
        to_label = QtWidgets.QLabel('to')
        to_label.setObjectName('secondary')
        self.target = QtWidgets.QLineEdit()
        self.target.setFont(numeric_font(13))
        self.target.setPlaceholderText('127.0.0.1:5600')
        self.target.setToolTip('HOST:PORT of the receiving program. 127.0.0.1 is this PC;\n'
                               'give another PC\'s IPv4 address to send there. Press Enter to apply.')
        self.target.setFixedWidth(190)
        self.target.editingFinished.connect(lambda: self.target_changed.emit(self.target.text()))
        controls.addWidget(self.switch)
        controls.addWidget(to_label)
        controls.addWidget(self.target)
        controls.addStretch()
        root.addLayout(controls)

        self.listener = QtWidgets.QLabel()
        self.listener.setObjectName('stateBadge')
        self.listener.setFont(ui_font(12, QtGui.QFont.Weight.DemiBold))
        self.listener.setWordWrap(True)
        root.addWidget(self.listener)
        self.stats = QtWidgets.QLabel()
        self.stats.setObjectName('caption')
        self.stats.setFont(numeric_font(12))
        root.addWidget(self.stats)

        message_row = QtWidgets.QHBoxLayout()
        self.message_title = QtWidgets.QLabel('Latest message')
        self.message_title.setFont(ui_font(13, QtGui.QFont.Weight.DemiBold))
        self.pause_button = QtWidgets.QPushButton('Pause')
        self.pause_button.setObjectName('flat')
        self.pause_button.setCheckable(True)
        self.pause_button.setToolTip('Hold the message shown here to read it; publishing continues')
        self.pause_button.toggled.connect(lambda on: self.pause_button.setText('Resume' if on else 'Pause'))
        self.copy_button = QtWidgets.QPushButton('Copy')
        self.copy_button.setObjectName('flat')
        self.copy_button.setIcon(icon('copy', COLORS['secondary']))
        self.copy_button.setToolTip('Copy the message shown, e.g. as an example for the receiving program')
        self.copy_button.clicked.connect(self._copy)
        message_row.addWidget(self.message_title)
        message_row.addStretch()
        message_row.addWidget(self.pause_button)
        message_row.addWidget(self.copy_button)
        root.addLayout(message_row)
        self.view = QtWidgets.QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(numeric_font(12))
        self.view.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setPlaceholderText('Nothing sent yet. Switch publishing on to see the messages here.')
        self.view.setMinimumHeight(300)
        root.addWidget(self.view)

        hint = QtWidgets.QLabel('Try it: <code>python tools/receive_detections.py</code> on the receiving PC. '
                                'Format: docs/DETECTION_OUTPUT.md')
        hint.setObjectName('caption')
        hint.setFont(ui_font(11))
        hint.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(hint)
        self.setMinimumWidth(560)

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def set_target_error(self, text):
        set_state(self.target, 'bad' if text else '')
        self.target.setToolTip(text or self.target.toolTip())
        if text:
            self.listener.setText(f'✕ {text}')
            set_state(self.listener, 'bad')

    def show_status(self, status):
        """Refresh from DetectionPublisher.status(); the text box only when the message changed."""
        if self.switch.isChecked() != status['enabled']:
            self.switch.blockSignals(True)
            self.switch.setChecked(status['enabled'])
            self.switch.blockSignals(False)
        if not self.target.hasFocus() and self.target.text() != status['target']:
            self.target.setText(status['target'])
            set_state(self.target, '')
        state, pill, _ = output_pill(status)
        if not status['enabled']:
            text, badge = 'Off · nothing is sent', ''
        elif state == 'warning':
            text, badge = (f'▲ Nothing is listening on {status["target"]}. '
                           'Start the receiving program or check its port.'), 'warning'
        elif state == 'bad':
            text, badge = f'✕ {status["last_error"]}', 'bad'
        elif status['since_last'] is None:
            text, badge = 'Waiting for the first message…', ''
        elif status['local']:
            text, badge = ('● Delivered: a program is listening on this PC' if state == 'good'
                           else '◆ Sending status messages: no detections at the moment'), (
                               'good' if state == 'good' else 'info')
        else:
            text, badge = (f'◆ Sending to another PC ({status["target"]}). Delivery can\'t be confirmed '
                           'from here; its firewall must allow this UDP port.'), 'info'
        self.listener.setText(text)
        set_state(self.listener, badge)
        since = status['since_last']
        self.stats.setText(
            f'{status["sent"]:,} sent  ·  {status["rate"]:.1f} msg/s  ·  '
            f'{status["bytes"] / 1024:,.1f} kB total  ·  '
            + ('nothing sent yet' if since is None else f'last {since:.1f} s ago')
            + (f'  ·  {status["errors"]} send errors' if status['errors'] else ''))
        message = status['last_message']
        if message and message != self._shown_message and not self.pause_button.isChecked():
            self._shown_message = message
            try:
                pretty = json.dumps(json.loads(message), indent=2, ensure_ascii=False)
            except ValueError:
                pretty = message
            self.message_title.setText(f'Latest message · {status["last_type"]}')
            scroll = self.view.verticalScrollBar().value()
            self.view.setPlainText(pretty)
            self.view.verticalScrollBar().setValue(scroll)

    def _copy(self):
        QtWidgets.QApplication.clipboard().setText(self.view.toPlainText())
