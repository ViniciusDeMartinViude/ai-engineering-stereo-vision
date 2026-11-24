"""Model picker: choose the YOLO model from the camera server's model library.

A popover under the app bar's Model button. It lists the library (name,
classes, size, date) with each model's local state (active, downloaded, not
downloaded, downloading NN %). Clicking a model downloads it in the background
if needed and switches detection to it without restarting the client.
"""
from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from control_room_widgets import set_state
from icons import icon, pixmap
from model_library import format_size
from theme import COLORS, ui_font


class ModelRow(QtWidgets.QFrame):
    """One clickable library entry."""
    clicked = QtCore.Signal()

    def __init__(self, title, detail, parent=None):
        super().__init__(parent)
        self.setObjectName('modelRow')
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(10, 7, 10, 7)
        row.setSpacing(12)
        text = QtWidgets.QVBoxLayout()
        text.setSpacing(1)
        self.title = QtWidgets.QLabel(title)
        self.title.setFont(ui_font(13, QtGui.QFont.Weight.DemiBold))
        self.detail = QtWidgets.QLabel(detail)
        self.detail.setObjectName('caption')
        self.detail.setFont(ui_font(12))
        text.addWidget(self.title)
        text.addWidget(self.detail)
        self.state = QtWidgets.QLabel()
        self.state.setObjectName('stateBadge')
        self.state.setFont(ui_font(12, QtGui.QFont.Weight.DemiBold))
        self.state.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.state.setMinimumWidth(130)
        row.addLayout(text, 1)
        row.addWidget(self.state)

    def set_state_text(self, text, state=''):
        if self.state.text() != text:
            self.state.setText(text)
        set_state(self.state, state)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class ModelPopover(QtWidgets.QFrame):
    """Library listing; a Qt popup that closes on an outside click or Esc."""
    model_chosen = QtCore.Signal(object)      # ModelEntry
    refresh_requested = QtCore.Signal()
    detection_off = QtCore.Signal()

    def __init__(self, source_text, parent=None):
        super().__init__(parent, QtCore.Qt.WindowType.Popup | QtCore.Qt.WindowType.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoMouseReplay, True)
        self.rows = {}  # model name -> ModelRow
        self.entries = []
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(16, 10, 16, 22)
        card = QtWidgets.QFrame()
        card.setObjectName('popoverCard')
        card.setStyleSheet(
            f"QFrame#modelRow {{ background:transparent; border:1px solid transparent; border-radius:8px; }}"
            f"QFrame#modelRow:hover {{ background-color:{COLORS['hover']}; border-color:{COLORS['border']}; }}"
            f"QFrame#modelRow[active=\"true\"] {{ border-color:{COLORS['accent']}; }}")
        shadow = QtWidgets.QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QtGui.QColor(0, 0, 0, 150))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)
        root = QtWidgets.QVBoxLayout(card)
        root.setContentsMargins(18, 14, 14, 14)
        root.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(10)
        glyph = QtWidgets.QLabel()
        glyph.setPixmap(pixmap('stack', COLORS['accent'], 18))
        title = QtWidgets.QLabel('YOLO model')
        title.setObjectName('cardTitle')
        title.setFont(ui_font(15, QtGui.QFont.Weight.DemiBold))
        self.refresh_button = QtWidgets.QPushButton('Refresh')
        self.refresh_button.setObjectName('flat')
        self.refresh_button.setToolTip('Read the library again from the camera server')
        self.refresh_button.clicked.connect(self.refresh_requested)
        close = QtWidgets.QToolButton()
        close.setIcon(icon('close', COLORS['secondary']))
        close.setIconSize(QtCore.QSize(16, 16))
        close.setToolTip('Close · Esc')
        close.clicked.connect(self.close)
        header.addWidget(glyph)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.refresh_button)
        header.addWidget(close)
        root.addLayout(header)
        self.source = QtWidgets.QLabel(source_text)
        self.source.setObjectName('secondary')
        self.source.setFont(ui_font(12))
        self.source.setWordWrap(True)
        root.addWidget(self.source)
        self.message = QtWidgets.QLabel()
        self.message.setObjectName('stateBadge')
        self.message.setFont(ui_font(12, QtGui.QFont.Weight.DemiBold))
        self.message.setWordWrap(True)
        self.message.hide()
        root.addWidget(self.message)

        self.list_holder = QtWidgets.QWidget()
        self.list_layout = QtWidgets.QVBoxLayout(self.list_holder)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(2)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.list_holder)
        scroll.setMinimumHeight(70)
        scroll.setMaximumHeight(330)
        self.scroll = scroll
        root.addWidget(scroll)
        self.local_row = None

        divider = QtWidgets.QFrame()
        divider.setObjectName('divider')
        divider.setFixedHeight(1)
        root.addWidget(divider)
        footer = QtWidgets.QHBoxLayout()
        self.cache_note = QtWidgets.QLabel()
        self.cache_note.setObjectName('caption')
        self.cache_note.setFont(ui_font(11))
        self.off_button = QtWidgets.QPushButton('Turn detection off')
        self.off_button.setObjectName('flat')
        self.off_button.clicked.connect(self.detection_off)
        footer.addWidget(self.cache_note, 1)
        footer.addWidget(self.off_button)
        root.addLayout(footer)
        self.setMinimumWidth(520)

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def set_cache_folder(self, folder):
        self.cache_note.setText(f'Downloads are kept in {folder}')
        self.cache_note.setToolTip('Each model is downloaded once per PC, checked against its '
                                   'SHA-256, and reused offline')

    def set_message(self, text, state=''):
        self.message.setVisible(bool(text))
        self.message.setText(text)
        set_state(self.message, state)

    def set_entries(self, entries, local_label=None):
        """Rebuild the rows when the library listing changes."""
        key = [(e.name, e.sha256) for e in entries] + [local_label]
        if key == [(e.name, e.sha256) for e in self.entries] + [getattr(self, '_local_label', None)]:
            return
        self.entries = list(entries)
        self._local_label = local_label
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.rows = {}
        self.local_row = None
        if local_label:
            row = ModelRow(f'{Path(local_label).name}', f'Local file · {local_label}')
            row.setToolTip('Started with --yolo-model PATH on this PC')
            row.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
            self.local_row = row
            self.list_layout.addWidget(row)
        for entry in entries:
            classes = ', '.join(entry.classes[:6]) + (' …' if len(entry.classes) > 6 else '') \
                if entry.classes else 'classes unknown'
            detail = f'{classes}  ·  {format_size(entry.size)}' + (f'  ·  {entry.added}' if entry.added else '')
            row = ModelRow(entry.name, detail)
            tip = f'{entry.file}\nSHA-256 {entry.sha256[:16]}…'
            if entry.description:
                tip = f'{entry.description}\n{tip}'
            row.setToolTip(tip)
            row.clicked.connect(lambda e=entry: self.model_chosen.emit(e))
            self.rows[entry.name] = row
            self.list_layout.addWidget(row)
        if not entries and not local_label:
            empty = QtWidgets.QLabel('No models in the library yet.')
            empty.setObjectName('caption')
            empty.setFont(ui_font(12))
            empty.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self.list_layout.addWidget(empty)
        self.list_layout.addStretch()
        self.list_holder.adjustSize()
        rows = len(entries) + (1 if local_label else 0)
        self.scroll.setFixedHeight(max(70, min(330, rows * 54 + 6)))
        self.adjustSize()

    def update_states(self, states):
        """states: {model name | 'local': (text, state, active)}"""
        for key, (text, state, active) in states.items():
            row = self.local_row if key == 'local' else self.rows.get(key)
            if row is None:
                continue
            row.set_state_text(text, state)
            if row.property('active') != ('true' if active else 'false'):
                row.setProperty('active', 'true' if active else 'false')
                row.style().unpolish(row)
                row.style().polish(row)
