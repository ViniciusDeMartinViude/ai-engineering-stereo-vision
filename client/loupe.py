"""Magnifier shown while dragging an ROI or a levelling line on a camera image.

It enlarges the image around the pointer so a corner or the conveyor edge can
be placed on the exact pixel. The drag in progress (ROI box or line) and a
crosshair at the pointer are drawn on top. Pixels are enlarged without
smoothing, so the real camera pixels stay visible.
"""
import cv2
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from theme import COLORS, RADIUS, numeric_font

SIZE = 200            # magnified image, in screen pixels (square)
BORDER = 2
CAPTION_H = 24
MARGIN = 12           # gap from the edges of the camera tile
MAGNIFICATION = 4.0   # relative to how large the image is on screen right now
MIN_ZOOM, MAX_ZOOM = 1.0, 12.0


def loupe_zoom(screen_px_per_image_px):
    """Camera-pixel enlargement: 4× what is on screen, kept between 1× and 12× native."""
    return float(max(MIN_ZOOM, min(MAX_ZOOM, MAGNIFICATION * screen_px_per_image_px)))


def zoom_text(zoom):
    return f'{zoom:.1f}'.rstrip('0').rstrip('.') + '×'


def magnify(image, x, y, zoom, size=SIZE):
    """``size``×``size`` view of ``image`` centred on view point (x, y), enlarged ``zoom`` times.

    (x, y) uses the image item's coordinates, where pixel i spans i..i+1.
    Outside the image is black. A view point p lands at (p - centre) * zoom + size / 2.
    """
    matrix = np.float32([[zoom, 0, size / 2 - zoom * (x - 0.5)],
                         [0, zoom, size / 2 - zoom * (y - 0.5)]])
    return cv2.warpAffine(image, matrix, (size, size), flags=cv2.INTER_NEAREST,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=0)


CORNERS = ('top-left', 'top-right', 'bottom-left', 'bottom-right')


def corner_rect(corner, area_w, area_h, loupe_w, loupe_h, margin=MARGIN):
    """(x, y, w, h) of the magnifier placed in ``corner`` of an area."""
    x = margin if corner.endswith('left') else area_w - loupe_w - margin
    y = margin if corner.startswith('top') else area_h - loupe_h - margin
    return max(0, x), max(0, y), loupe_w, loupe_h


def corner_for(points, area_w, area_h, loupe_w, loupe_h, current=None, keep_px=60):
    """The corner whose magnifier stays farthest from every point (pointer, drag start).

    The ``current`` corner is kept while everything stays ``keep_px`` clear of it,
    so the magnifier doesn't jump around during a drag. Ties go to the earlier
    corner in CORNERS, so the top corners are preferred.
    """
    def clearance(corner):
        x, y, w, h = corner_rect(corner, area_w, area_h, loupe_w, loupe_h)
        gaps = []
        for px, py in points:
            dx = max(x - px, 0, px - (x + w))
            dy = max(y - py, 0, py - (y + h))
            gaps.append((dx * dx + dy * dy) ** 0.5)
        return min(gaps) if gaps else 0.0
    if current in CORNERS and clearance(current) >= keep_px:
        return current
    return max(CORNERS, key=clearance)


class Loupe(QtWidgets.QWidget):
    """A floating magnifier inside a camera tile; it never takes the mouse."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedSize(SIZE + 2 * BORDER, SIZE + 2 * BORDER + CAPTION_H)
        self._pixels = None
        self._image = None
        self._point = (0.0, 0.0)
        self._anchor = None
        self._mode = 'roi'
        self._zoom = 1.0
        self._caption = ''
        self.corner = 'top-right'
        self.hide()

    def show_view(self, rgb, point, anchor, mode, zoom, caption, avoid):
        """Magnify ``rgb`` around ``point``; ``anchor`` is where the drag began (or None).

        ``avoid`` lists widget positions (pointer, drag start) the magnifier must not cover.
        """
        self._point, self._anchor, self._mode = point, anchor, mode
        self._zoom, self._caption = zoom, caption
        self._pixels = np.ascontiguousarray(magnify(rgb, point[0], point[1], zoom))
        self._image = QtGui.QImage(self._pixels.data, SIZE, SIZE, 3 * SIZE,
                                   QtGui.QImage.Format.Format_RGB888)
        parent = self.parentWidget()
        self.corner = corner_for(avoid, parent.width(), parent.height(), self.width(), self.height(),
                                 current=self.corner if self.isVisible() else None)
        x, y, _, _ = corner_rect(self.corner, parent.width(), parent.height(),
                                 self.width(), self.height())
        self.move(x, y)
        self.show()
        self.raise_()
        self.update()

    def to_loupe(self, p):
        """Image-item coordinates to widget coordinates inside the magnified image."""
        return QtCore.QPointF((p[0] - self._point[0]) * self._zoom + SIZE / 2 + BORDER,
                              (p[1] - self._point[1]) * self._zoom + SIZE / 2 + BORDER)

    def paintEvent(self, event):
        if self._image is None:
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        frame = QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(QtGui.QPen(QtGui.QColor(COLORS['accent']), BORDER))
        p.setBrush(QtGui.QColor(COLORS['surface']))
        p.drawRoundedRect(frame, RADIUS, RADIUS)
        view = QtCore.QRectF(BORDER, BORDER, SIZE, SIZE)
        p.save()
        clip = QtGui.QPainterPath()
        clip.addRoundedRect(view, RADIUS - 2, RADIUS - 2)
        p.setClipPath(clip)
        p.drawImage(view.topLeft(), self._image)
        if self._anchor is not None:
            start, end = self.to_loupe(self._anchor), self.to_loupe(self._point)
            if self._mode == 'line':
                self._outlined(p, lambda: p.drawLine(start, end), COLORS['accent'], 2.5)
            else:
                p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                self._outlined(p, lambda: p.drawRect(QtCore.QRectF(start, end).normalized()),
                               COLORS['roi'], 1.5, QtCore.Qt.PenStyle.DashLine)
        # Crosshair with a gap, so the pixel under the pointer stays visible.
        c = QtCore.QPointF(SIZE / 2 + BORDER, SIZE / 2 + BORDER)
        gap, arm = 5, 16

        def crosshair():
            p.drawLine(QtCore.QPointF(c.x() - gap - arm, c.y()), QtCore.QPointF(c.x() - gap, c.y()))
            p.drawLine(QtCore.QPointF(c.x() + gap, c.y()), QtCore.QPointF(c.x() + gap + arm, c.y()))
            p.drawLine(QtCore.QPointF(c.x(), c.y() - gap - arm), QtCore.QPointF(c.x(), c.y() - gap))
            p.drawLine(QtCore.QPointF(c.x(), c.y() + gap), QtCore.QPointF(c.x(), c.y() + gap + arm))
        self._outlined(p, crosshair, '#FFFFFF', 1.5)
        p.restore()
        p.setPen(QtGui.QColor(COLORS['text']))
        p.setFont(numeric_font(12))
        caption = QtCore.QRectF(BORDER + 8, BORDER + SIZE, SIZE - 16, CAPTION_H)
        p.drawText(caption, QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignLeft,
                   self._caption)
        p.setPen(QtGui.QColor(COLORS['secondary']))
        p.drawText(caption, QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignRight,
                   zoom_text(self._zoom))
        p.end()

    @staticmethod
    def _outlined(p, draw, color, width, style=QtCore.Qt.PenStyle.SolidLine):
        """Draw twice, dark then light, so marks read on bright and dark pixels alike."""
        for pen_color, pen_width, pen_style in (('#000000', width + 2, QtCore.Qt.PenStyle.SolidLine),
                                                (color, width, style)):
            pen = QtGui.QPen(QtGui.QColor(pen_color), pen_width, pen_style)
            pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            draw()
