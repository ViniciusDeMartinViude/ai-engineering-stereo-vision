"""Small vector icons painted with QPainter.

No icon font or image assets: icons stay crisp at any Windows display scaling
and render identically on Windows 10 and 11. Each painter draws on a 16x16 grid.
"""
from PySide6 import QtCore, QtGui

_P = QtCore.QPointF


def _brackets(p, inward):
    """Four corner brackets, pointing out (expand) or in (collapse)."""
    if inward:
        corners = [((6, 2), (6, 6), (2, 6)), ((10, 2), (10, 6), (14, 6)),
                   ((14, 10), (10, 10), (10, 14)), ((2, 10), (6, 10), (6, 14))]
    else:
        corners = [((2, 6), (2, 2), (6, 2)), ((10, 2), (14, 2), (14, 6)),
                   ((14, 10), (14, 14), (10, 14)), ((6, 14), (2, 14), (2, 10))]
    for a, b, c in corners:
        p.drawPolyline([_P(*a), _P(*b), _P(*c)])


def _split(p):
    p.drawRoundedRect(QtCore.QRectF(1.5, 3, 5.5, 10), 1.5, 1.5)
    p.drawRoundedRect(QtCore.QRectF(9, 3, 5.5, 10), 1.5, 1.5)


def _fit(p):
    p.drawRoundedRect(QtCore.QRectF(1.5, 2.5, 13, 11), 2, 2)
    p.drawRect(QtCore.QRectF(5, 5.5, 6, 5))


def _close(p):
    p.drawLine(_P(4, 4), _P(12, 12))
    p.drawLine(_P(12, 4), _P(4, 12))


def _pause(p):
    pen = p.pen()
    pen.setWidthF(2.2)
    p.setPen(pen)
    p.drawLine(_P(5.5, 3.5), _P(5.5, 12.5))
    p.drawLine(_P(10.5, 3.5), _P(10.5, 12.5))


def _play(p):
    p.setBrush(p.pen().color())
    p.drawPolygon(QtGui.QPolygonF([_P(5, 3), _P(13, 8), _P(5, 13)]))


def _sliders(p):
    for y, knob in ((4, 10), (8, 5), (12, 8.5)):
        p.drawLine(_P(2, y), _P(14, y))
        p.save()
        p.setBrush(p.pen().color())
        p.drawEllipse(_P(knob, y), 1.9, 1.9)
        p.restore()


def _pulse(p):
    p.drawPolyline([_P(1, 8.5), _P(4.5, 8.5), _P(6.5, 3), _P(9.5, 13), _P(11.5, 8.5), _P(15, 8.5)])


def _tag(p):
    p.drawPolygon(QtGui.QPolygonF([_P(2.5, 2.5), _P(8.5, 2.5), _P(14, 8), _P(8, 14), _P(2.5, 8.5)]))
    p.save()
    p.setBrush(p.pen().color())
    p.drawEllipse(_P(5.6, 5.6), 1.2, 1.2)
    p.restore()


def _logo(p):
    """Two overlapping lenses: the stereo pair."""
    p.drawEllipse(_P(5.6, 8), 4.4, 4.4)
    p.drawEllipse(_P(10.4, 8), 4.4, 4.4)
    p.save()
    p.setBrush(p.pen().color())
    p.drawEllipse(_P(5.6, 8), 1.3, 1.3)
    p.drawEllipse(_P(10.4, 8), 1.3, 1.3)
    p.restore()


_PAINTERS = {
    "expand": lambda p: _brackets(p, inward=False),
    "fullscreen": lambda p: _brackets(p, inward=False),
    "exit_fullscreen": lambda p: _brackets(p, inward=True),
    "split": _split,
    "fit": _fit,
    "close": _close,
    "pause": _pause,
    "play": _play,
    "sliders": _sliders,
    "pulse": _pulse,
    "tag": _tag,
    "logo": _logo,
}


def pixmap(name, color, size=16, scale=2.0, width=1.6):
    pm = QtGui.QPixmap(int(size * scale), int(size * scale))
    pm.fill(QtCore.Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(scale)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    p.scale(size / 16.0, size / 16.0)
    pen = QtGui.QPen(QtGui.QColor(color), width, QtCore.Qt.PenStyle.SolidLine,
                     QtCore.Qt.PenCapStyle.RoundCap, QtCore.Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
    _PAINTERS[name](p)
    p.end()
    return pm


def icon(name, color, size=16):
    """QIcon with normal and disabled modes, sharp at 1x and 2x scaling."""
    result = QtGui.QIcon()
    for scale in (1.0, 2.0):
        result.addPixmap(pixmap(name, color, size, scale), QtGui.QIcon.Mode.Normal)
    return result
