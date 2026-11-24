"""Theme picker: choose the client's colour theme from previews.

A popover under the app bar's Theme button. Each card is a miniature of the
app drawn in that theme's own colours: app bar, two camera tiles, the status
marks and the accent. Choosing one rebuilds the window in that theme.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from icons import icon, pixmap
from theme import COLORS, THEMES, ui_font
import theme as theme_module

CARD_W, PREVIEW_H, NAME_H = 236, 104, 40


class ThemeCard(QtWidgets.QAbstractButton):
    """A clickable miniature of the app in one theme."""

    def __init__(self, key, parent=None):
        super().__init__(parent)
        self.key = key
        entry = THEMES[key]
        self.setFixedSize(CARD_W, PREVIEW_H + NAME_H)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f'{entry["name"]} · {entry["inspired"]}')
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_Hover, True)
        self.current = False

    def paintEvent(self, event):
        c = THEMES[self.key]['colors']
        ui = COLORS  # the theme the client is in now, for the name and frame
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        outer = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        frame = QtGui.QColor(ui['accent'] if self.current else
                             (ui['border_strong'] if self.underMouse() else ui['border']))
        p.setPen(QtGui.QPen(frame, 2 if self.current else 1))
        p.setBrush(QtGui.QColor(ui['hover'] if self.underMouse() else ui['raised']))
        p.drawRoundedRect(outer, 9, 9)
        # Miniature window.
        view = QtCore.QRectF(8, 8, CARD_W - 16, PREVIEW_H - 8)
        clip = QtGui.QPainterPath()
        clip.addRoundedRect(view, 6, 6)
        p.save()
        p.setClipPath(clip)
        p.fillRect(view, QtGui.QColor(c['background']))
        bar = QtCore.QRectF(view.left(), view.top(), view.width(), 16)
        p.fillRect(bar, QtGui.QColor(c['surface']))
        p.fillRect(QtCore.QRectF(bar.left(), bar.bottom(), bar.width(), 1), QtGui.QColor(c['border']))
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        for i, state in enumerate(('good', 'warning', 'bad')):
            p.setBrush(QtGui.QColor(c['raised']))
            pill = QtCore.QRectF(bar.left() + 8 + i * 30, bar.top() + 4, 26, 8)
            p.drawRoundedRect(pill, 4, 4)
            p.setBrush(QtGui.QColor(c[state]))
            p.drawEllipse(QtCore.QPointF(pill.left() + 5, pill.center().y()), 2.2, 2.2)
        p.setBrush(QtGui.QColor(c['accent']))
        p.drawRoundedRect(QtCore.QRectF(bar.right() - 34, bar.top() + 4, 26, 8), 3, 3)
        tile_w = (view.width() - 18) / 2
        for i in range(2):
            tile = QtCore.QRectF(view.left() + 6 + i * (tile_w + 6), bar.bottom() + 6, tile_w,
                                 view.bottom() - bar.bottom() - 12)
            p.setPen(QtGui.QPen(QtGui.QColor(c['border']), 1))
            p.setBrush(QtGui.QColor(c['surface']))
            p.drawRoundedRect(tile, 4, 4)
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(QtGui.QColor(c['text']))
            p.drawRoundedRect(QtCore.QRectF(tile.left() + 6, tile.top() + 6, tile_w * 0.42, 4), 2, 2)
            p.setBrush(QtGui.QColor(c['secondary']))
            p.drawRoundedRect(QtCore.QRectF(tile.left() + 6, tile.top() + 14, tile_w * 0.62, 3), 1.5, 1.5)
            video = QtCore.QRectF(tile.left() + 6, tile.top() + 22, tile.width() - 12, tile.height() - 28)
            p.fillRect(video, QtGui.QColor(c['video']))
            line = QtGui.QPen(QtGui.QColor(c['accent'] if i == 0 else c['skew']), 1.6)
            p.setPen(line)
            mid = video.center().y()
            points = [QtCore.QPointF(video.left() + video.width() * t / 6,
                                     mid + (3 if t % 2 else -3)) for t in range(7)]
            p.drawPolyline(points)
        p.restore()
        # Name and inspiration, in the current UI colours.
        p.setPen(QtGui.QColor(ui['text']))
        p.setFont(ui_font(13, QtGui.QFont.Weight.DemiBold))
        name_rect = QtCore.QRectF(10, PREVIEW_H + 2, CARD_W - 20, 18)
        name = THEMES[self.key]['name'] + ('  ✓' if self.current else '')
        p.drawText(name_rect, QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter, name)
        p.setPen(QtGui.QColor(ui['muted']))
        p.setFont(ui_font(11))
        sub = QtCore.QRectF(10, PREVIEW_H + 19, CARD_W - 20, 16)
        text = p.fontMetrics().elidedText(THEMES[self.key]['inspired'], QtCore.Qt.TextElideMode.ElideRight,
                                          int(sub.width()))
        p.drawText(sub, QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter, text)
        p.end()


class ThemePopover(QtWidgets.QFrame):
    """Grid of theme cards; a Qt popup that closes on an outside click or Esc."""
    theme_chosen = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent, QtCore.Qt.WindowType.Popup | QtCore.Qt.WindowType.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoMouseReplay, True)
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
        glyph = QtWidgets.QLabel()
        glyph.setPixmap(pixmap('palette', COLORS['accent'], 18))
        title = QtWidgets.QLabel('Colour theme')
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
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(10)
        self.cards = {}
        for i, key in enumerate(THEMES):
            card_button = ThemeCard(key)
            card_button.clicked.connect(lambda checked=False, k=key: self._choose(k))
            self.cards[key] = card_button
            grid.addWidget(card_button, i // 3, i % 3)
        root.addLayout(grid)
        note = QtWidgets.QLabel('Choosing a theme rebuilds the window: the video reconnects and the model '
                                'reloads in a few seconds. ROIs, levelling and other settings stay. '
                                'Video overlays (boxes, labels, ROI) keep the same colours in every theme.')
        note.setObjectName('caption')
        note.setFont(ui_font(11))
        note.setWordWrap(True)
        root.addWidget(note)
        self.set_current(theme_module.CURRENT_THEME)

    def set_current(self, key):
        for k, card_button in self.cards.items():
            card_button.current = k == key
            card_button.update()

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def _choose(self, key):
        self.close()
        if key != theme_module.CURRENT_THEME:
            self.theme_chosen.emit(key)

