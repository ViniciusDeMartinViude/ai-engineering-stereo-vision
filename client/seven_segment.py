"""Custom 3-significant-digit seven-segment displays for stereo timing telemetry.

The underlying measurements remain in milliseconds; unit scaling is display-only.
"""
import math
from PySide6 import QtCore, QtGui, QtWidgets


def format_three_digits(milliseconds):
    """Return (three digits, decimal position, sign, unit) for a timing value.

    Decimal position counts digits BEFORE the decimal, 0..3. A value smaller
    than 1 is drawn with a leading decimal point, e.g. .125 ms. Scaling to
    microseconds/seconds keeps three meaningful digits for extreme values.
    """
    if milliseconds is None or not math.isfinite(milliseconds):
        return '---', None, '', 'ms'
    sign = '-' if milliseconds < 0 else ''
    amount = abs(float(milliseconds))
    unit = 'ms'
    if amount >= 999.5:
        amount /= 1000.
        unit = 's'
    elif 0 < amount < 0.1:
        amount *= 1000.
        unit = 'µs'
    for _ in range(3):
        if amount == 0:
            return '000', 1, sign, unit
        exponent = math.floor(math.log10(amount))
        scale = 10 ** (2 - exponent)
        rounded = round(amount * scale)
        if rounded >= 1000:
            amount = rounded / scale
            if amount >= 1000 and unit == 'ms':
                amount /= 1000
                unit = 's'
            elif amount >= 1000 and unit == 'µs':
                amount /= 1000
                unit = 'ms'
            else:
                amount = 999
            continue
        digits = f'{rounded:03d}'
        # For 0.125, exponent=-1 -> decimal before first digit.
        decimal_position = exponent + 1
        return digits, (None if decimal_position == 3 else decimal_position), sign, unit
    return '999', 3, sign, unit


_SEGMENTS = {
    '0': 'ab cdef'.replace(' ', ''), '1': 'bc', '2': 'abged',
    '3': 'abgcd', '4': 'fgbc', '5': 'afgcd', '6': 'afgecd',
    '7': 'abc', '8': 'abcdefg', '9': 'abcdfg', '-': 'g',
}


class SevenSegmentDisplay(QtWidgets.QFrame):
    """Large anti-aliased instrument; draws exactly three seven-segment digits."""

    def __init__(self, title, color='#63edba', parent=None):
        super().__init__(parent)
        self.setObjectName('sevenSegmentInstrument')
        self.setMinimumSize(165, 92)
        self.title = title
        self.color = QtGui.QColor(color)
        self.digits = '---'
        self.decimal = None
        self.sign = ''
        self.unit = 'ms'
        self.setToolTip('Three significant digits; decimal point and unit adjust automatically')

    def set_milliseconds(self, value):
        formatted = format_three_digits(value)
        if formatted != (self.digits, self.decimal, self.sign, self.unit):
            self.digits, self.decimal, self.sign, self.unit = formatted
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        bounds = self.rect()
        p.setPen(QtGui.QColor('#aebfd5'))
        font = QtGui.QFont('Segoe UI', 10, QtGui.QFont.Weight.DemiBold)
        p.setFont(font)
        p.drawText(QtCore.QRectF(7, 3, bounds.width()-14, 22),
                   QtCore.Qt.AlignmentFlag.AlignLeft, self.title)

        # Draw on a normalized coordinate system so proportions remain readable
        # as the bottom dock is resized.
        area = QtCore.QRectF(6, 26, max(20, bounds.width()-12),
                             max(30, bounds.height()-30))
        scale = min(area.width()/190, area.height()/86)
        xoffset = area.left() + (area.width()-190*scale)/2
        yoffset = area.top() + (area.height()-86*scale)/2
        p.translate(xoffset, yoffset)
        p.scale(scale, scale)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        active = self.color
        inactive = QtGui.QColor(active)
        inactive.setAlpha(8)  # barely visible unlit segments; lit LEDs stay bright

        # Fixed slot layout: sign, three 43px digits, units.
        if self.sign:
            p.setBrush(active)
            p.drawRoundedRect(QtCore.QRectF(1, 39, 17, 6), 2, 2)
        for i, char in enumerate(self.digits):
            self._draw_digit(p, 20 + i*44, 4, char, active, inactive)
        if self.decimal is not None and 0 <= self.decimal <= 3:
            # Decimal at digit boundary; for .123 it precedes first digit.
            dot_x = 18 if self.decimal == 0 else 20 + (self.decimal-1)*44 + 39
            p.setBrush(active)
            p.drawEllipse(QtCore.QRectF(dot_x, 69, 6.8, 6.8))
        p.setPen(QtGui.QColor('#c9dbed'))
        unit_font = QtGui.QFont('Segoe UI', 13, QtGui.QFont.Weight.Bold)
        p.setFont(unit_font)
        p.drawText(QtCore.QRectF(153, 45, 38, 29),
                   QtCore.Qt.AlignmentFlag.AlignLeft, self.unit)
        p.end()

    @staticmethod
    def _draw_digit(p, ox, oy, char, active, inactive):
        # Seven segments in a 36×69 nominal digit.
        shapes = {
            'a': [(7, 0), (29, 0), (25, 6), (11, 6)],
            'g': [(7, 32), (29, 32), (25, 38), (11, 38)],
            'd': [(7, 64), (29, 64), (25, 70), (11, 70)],
            'f': [(3, 4), (9, 10), (9, 28), (3, 34), (0, 29), (0, 9)],
            'b': [(33, 4), (36, 9), (36, 29), (33, 34), (27, 28), (27, 10)],
            'e': [(3, 36), (9, 42), (9, 60), (3, 66), (0, 61), (0, 41)],
            'c': [(33, 36), (36, 41), (36, 61), (33, 66), (27, 60), (27, 42)],
        }
        enabled = _SEGMENTS.get(char, '')
        for segment, vertices in shapes.items():
            p.setBrush(active if segment in enabled else inactive)
            poly = QtGui.QPolygonF([QtCore.QPointF(ox+x, oy+y) for x, y in vertices])
            p.drawPolygon(poly)
