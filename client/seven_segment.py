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
    """Large anti-aliased instrument; draws exactly three seven-segment digits.

    The colour carries the reading's state (set by the owning KPI card); the
    digits, decimal point and unit are always drawn so state is never colour-only.
    """
    SLANT = 0.07  # classic instrument lean, as a horizontal shear factor
    max_scale = 10.0  # cap on digit growth; owners lower it for calmer dashboards

    def __init__(self, title, color='#63edba', parent=None, show_title=True):
        super().__init__(parent)
        self.setObjectName('sevenSegmentInstrument')
        self.setMinimumSize(120, 48 if not show_title else 92)
        self.title = title
        self.show_title = show_title
        self.color = QtGui.QColor(color)
        from theme import COLORS
        self.unit_color = QtGui.QColor(COLORS['secondary'])
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

    def set_color(self, color):
        color = QtGui.QColor(color)
        if color != self.color:
            self.color = color
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        bounds = self.rect()
        top = 4
        if self.show_title:
            p.setPen(self.unit_color)
            p.setFont(QtGui.QFont('Segoe UI', 10, QtGui.QFont.Weight.DemiBold))
            p.drawText(QtCore.QRectF(7, 3, bounds.width()-14, 22),
                       QtCore.Qt.AlignmentFlag.AlignLeft, self.title)
            top = 26

        # Draw on a normalized coordinate system so proportions remain readable
        # as the dashboard is resized.
        area = QtCore.QRectF(6, top, max(20, bounds.width()-12),
                             max(30, bounds.height()-top-4))
        scale = min(area.width()/196, area.height()/80, self.max_scale)
        xoffset = area.left() + (area.width()-196*scale)/2
        yoffset = area.top() + (area.height()-80*scale)/2
        p.translate(xoffset, yoffset)
        p.scale(scale, scale)
        active = self.color
        inactive = QtGui.QColor(active)
        inactive.setAlpha(11)  # barely visible unlit segments; lit LEDs stay bright
        glow = QtGui.QColor(active)
        glow.setAlpha(70)

        p.save()
        # Lean the digits like a physical instrument; the unit stays upright.
        p.translate(self.SLANT * 74, 0)
        p.shear(-self.SLANT, 0)
        # Fixed slot layout: sign, three 44px digits, units.
        p.setPen(QtGui.QPen(glow, 1.6))
        if self.sign:
            p.setBrush(active)
            p.drawRoundedRect(QtCore.QRectF(1, 34, 15, 6), 2, 2)
        for i, char in enumerate(self.digits):
            self._draw_digit(p, 20 + i*44, 2, char, active, inactive, glow)
        if self.decimal is not None and 0 <= self.decimal <= 3:
            # Decimal at digit boundary; for .123 it precedes first digit.
            dot_x = 12 if self.decimal == 0 else 20 + (self.decimal-1)*44 + 37.5
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(active)
            p.drawEllipse(QtCore.QRectF(dot_x, 66, 6.8, 6.8))
        p.restore()

        p.setPen(self.unit_color)
        unit_font = QtGui.QFont()
        unit_font.setFamilies(['Bahnschrift', 'Segoe UI'])
        unit_font.setPixelSize(19)
        unit_font.setWeight(QtGui.QFont.Weight.DemiBold)
        p.setFont(unit_font)
        p.drawText(QtCore.QRectF(156, 44, 40, 30),
                   QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignBottom,
                   self.unit)
        p.end()

    @staticmethod
    def _draw_digit(p, ox, oy, char, active, inactive, glow=None):
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
        lit_pen = QtGui.QPen(glow, 1.6) if glow is not None else QtCore.Qt.PenStyle.NoPen
        for segment, vertices in shapes.items():
            lit = segment in enabled
            p.setPen(lit_pen if lit else QtCore.Qt.PenStyle.NoPen)
            p.setBrush(active if lit else inactive)
            poly = QtGui.QPolygonF([QtCore.QPointF(ox+x, oy+y) for x, y in vertices])
            p.drawPolygon(poly)
