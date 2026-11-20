"""Per-client software image adjustments for stereo video and YOLO.

Never mutates the incoming frame: the bottom timestamp/CRC region stays as-is.
These controls are pixel-domain approximations, NOT physical camera exposure/
white-balance controls. Each client maintains its own preferences.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import cv2
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from icons import icon, pixmap
from theme import COLORS, display_font, numeric_font, ui_font


@dataclass(frozen=True)
class ImageSettings:
    """Neutral values are all zero; exposure uses hundredths of a stop."""
    exposure: int = 0
    brightness: int = 0
    contrast: int = 0
    temperature: int = 0
    tint: int = 0
    enabled: bool = True

    def neutral(self) -> bool:
        return not self.enabled or all(
            getattr(self, k) == 0 for k in (
                "exposure", "brightness", "contrast", "temperature", "tint"))


def build_lut(settings: ImageSettings) -> np.ndarray | None:
    """Return 256×1×3 BGR LUT, or None for identity adjustment.

    LUT-based correction is predictable and much faster than manipulating
    every video pixel with separate NumPy floating-point arrays.
    """
    if settings.neutral():
        return None
    values = np.arange(256, dtype=np.float32)
    exposure_gain = 2.0 ** (settings.exposure / 100.0)
    contrast_gain = 2.0 ** (settings.contrast / 100.0)
    brightness_offset = settings.brightness * 0.85
    base = (values * exposure_gain - 127.5) * contrast_gain + 127.5
    base += brightness_offset
    # BGR: warm increases red and decreases blue, tint moves green
    # relative to red/blue. Neutral for all controls means exact identity.
    wb = (1.0 - settings.temperature / 300.0,
          1.0 + settings.tint / 300.0,
          1.0 + settings.temperature / 300.0)
    lut = np.empty((256, 1, 3), dtype=np.uint8)
    for channel, gain in enumerate(wb):
        lut[:, 0, channel] = np.clip(np.rint(base * gain), 0, 255).astype(np.uint8)
    return lut


def apply_to_stereo_frame(frame: np.ndarray, lut: np.ndarray | None,
                          source_height: int) -> np.ndarray:
    """Correct the two video panes but preserve every metadata/CRC pixel.

    The caller MUST decode timestamps from the unmodified incoming frame.
    A neutral LUT returns that frame directly, without a copy.
    """
    if lut is None:
        return frame
    video_height = max(0, min(int(source_height), frame.shape[0]))
    output = frame.copy()
    if video_height:
        output[:video_height] = cv2.LUT(frame[:video_height], lut)
    return output


class DraggablePanelHeader(QtWidgets.QWidget):
    """Drag a frameless dialog by its title strip; leave its buttons interactive."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_offset = None
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Drag here to move the image adjustments panel")

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            dialog = self.window()
            self._drag_offset = event.globalPosition().toPoint() - dialog.frameGeometry().topLeft()
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & QtCore.Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._drag_offset = None
            self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)


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
        track = QtCore.QRectF(0.5, (self.height() - 20) / 2 + 0.5, 38, 20)
        p.setPen(QtCore.Qt.PenStyle.NoPen if on else QtGui.QPen(QtGui.QColor(COLORS["border_strong"]), 1))
        p.setBrush(QtGui.QColor(COLORS["accent"] if on else COLORS["raised"]))
        p.drawRoundedRect(track, 10, 10)
        knob_x = track.right() - 17 if on else track.left() + 3
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(COLORS["background"] if on else COLORS["secondary"]))
        p.drawEllipse(QtCore.QRectF(knob_x, track.top() + 3, 14, 14))
        p.setPen(self.palette().color(QtGui.QPalette.ColorRole.WindowText))
        p.setFont(self.font())
        p.drawText(QtCore.QRectF(50, 0, self.width() - 50, self.height()),
                   QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignLeft, self.text())
        p.end()


class ImageAdjustmentsDialog(QtWidgets.QDialog):
    """Translucent non-blocking floating adjustment panel.

    Non-modal by design: decoded video and YOLO stay responsive while the
    panel is open. This dialog never writes camera/server settings.
    """
    changed = QtCore.Signal(object)

    _FIELDS = (
        ("exposure", "Exposure", -200, 200, "EV", 100),
        ("brightness", "Brightness", -100, 100, "", 1),
        ("contrast", "Contrast", -100, 100, "", 1),
        ("temperature", "Temperature", -100, 100, "", 1),
        ("tint", "Tint", -100, 100, "", 1),
    )
    _GROUPS = (("Exposure", ("exposure", "brightness", "contrast")),
               ("White balance", ("temperature", "tint")))
    # Direction words make bipolar values readable without knowing the sign convention.
    _DIRECTIONS = {"temperature": ("cool", "warm"), "tint": ("magenta", "green")}

    def __init__(self, initial: ImageSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Image adjustments · client only")
        self.setModal(False)
        self.setWindowFlags(
            QtCore.Qt.WindowType.Dialog |
            QtCore.Qt.WindowType.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumWidth(400)
        self._settings = initial
        self._sliders = {}
        self._values = {}
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(16, 10, 16, 22)  # room for the drop shadow
        outer.setSpacing(0)
        self.card = QtWidgets.QFrame()
        self.card.setObjectName("imageAdjustmentsPanel")
        shadow = QtWidgets.QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QtGui.QColor(0, 0, 0, 150))
        self.card.setGraphicsEffect(shadow)
        outer.addWidget(self.card)
        root = QtWidgets.QVBoxLayout(self.card)
        root.setContentsMargins(18, 14, 14, 16)
        root.setSpacing(10)

        self.drag_header = DraggablePanelHeader(self.card)
        header = QtWidgets.QHBoxLayout(self.drag_header)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)
        glyph = QtWidgets.QLabel()
        glyph.setPixmap(pixmap("sliders", COLORS["accent"], 18))
        heading = QtWidgets.QLabel("Image adjustments")
        heading.setObjectName("cardTitle")
        heading.setFont(ui_font(15, QtGui.QFont.Weight.DemiBold))
        # Forward title mouse gestures to the draggable parent, not the sliders.
        for label in (glyph, heading):
            label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        header.addWidget(glyph)
        header.addWidget(heading)
        header.addStretch()
        close = QtWidgets.QToolButton()
        close.setIcon(icon("close", COLORS["secondary"]))
        close.setIconSize(QtCore.QSize(16, 16))
        close.setToolTip("Hide image adjustments")
        close.clicked.connect(self.hide)
        header.addWidget(close)
        root.addWidget(self.drag_header)

        explanation = QtWidgets.QLabel(
            "This workstation only. Applies to both cameras and the YOLO input; "
            "the shared stream and physical cameras are unchanged.")
        explanation.setObjectName("secondary")
        explanation.setFont(ui_font(12))
        explanation.setWordWrap(True)
        root.addWidget(explanation)

        self.enable = ToggleSwitch("Apply adjustments")
        self.enable.setFont(ui_font(13, QtGui.QFont.Weight.DemiBold))
        self.enable.setToolTip("Turn off to compare with the original decoded image; "
                               "slider values are kept")
        self.enable.setChecked(initial.enabled)
        self.enable.toggled.connect(self._emit)
        root.addWidget(self.enable)

        specs = {spec[0]: spec for spec in self._FIELDS}
        for group, fields in self._GROUPS:
            divider = QtWidgets.QFrame()
            divider.setObjectName("divider")
            divider.setFixedHeight(1)
            root.addSpacing(2)
            root.addWidget(divider)
            section = QtWidgets.QLabel(group)
            section.setObjectName("overline")
            section.setFont(display_font(11))
            root.addWidget(section)
            for field in fields:
                _, caption, lo, hi, unit, denominator = specs[field]
                title_row = QtWidgets.QHBoxLayout()
                name = QtWidgets.QLabel(caption)
                name.setFont(ui_font(13))
                number = QtWidgets.QLabel()
                number.setObjectName("metricValue")
                number.setFont(numeric_font(13))
                number.setMinimumWidth(96)
                number.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
                title_row.addWidget(name)
                title_row.addStretch()
                title_row.addWidget(number)
                root.addLayout(title_row)
                slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
                if field in self._DIRECTIONS:
                    slider.setObjectName(f"{field}Slider")
                slider.setRange(lo, hi)
                slider.setValue(getattr(initial, field))
                slider.setSingleStep(1 if denominator == 1 else 5)
                slider.setPageStep(10)
                slider.setToolTip(f"{caption} · double-click to reset")
                slider.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
                slider.valueChanged.connect(self._emit)
                slider.installEventFilter(self)
                root.addWidget(slider)
                self._sliders[field] = slider
                self._values[field] = number

        divider = QtWidgets.QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        root.addSpacing(2)
        root.addWidget(divider)
        footer = QtWidgets.QHBoxLayout()
        self.reset = QtWidgets.QPushButton("Reset all")
        self.reset.setObjectName("flat")
        self.reset.setToolTip("Return every control to neutral")
        self.reset.clicked.connect(self.reset_values)
        footer.addWidget(self.reset)
        footer.addStretch()
        self.close_button = QtWidgets.QPushButton("Done")
        self.close_button.setObjectName("primary")
        self.close_button.setMinimumWidth(88)
        self.close_button.clicked.connect(self.hide)
        footer.addWidget(self.close_button)
        root.addLayout(footer)
        self._update_labels()

    @property
    def settings(self) -> ImageSettings:
        return self._settings

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.MouseButtonDblClick:
            for field, slider in self._sliders.items():
                if slider is obj:
                    self._reset_slider(field)
                    return True
        return super().eventFilter(obj, event)

    def _reset_slider(self, field):
        self._sliders[field].setValue(0)

    def reset_values(self):
        """Restore zero correction, leaving adjustments enabled."""
        for slider in self._sliders.values():
            slider.blockSignals(True)
            slider.setValue(0)
            slider.blockSignals(False)
        self.enable.setChecked(True)
        self._emit()

    def _update_labels(self):
        for field, _, _, _, unit, denominator in self._FIELDS:
            value = self._sliders[field].value()
            if denominator != 1:
                text = f"{value / denominator:+.2f} {unit}" if value else f"0.00 {unit}"
            elif field in self._DIRECTIONS and value:
                low, high = self._DIRECTIONS[field]
                text = f"{value:+d} {high if value > 0 else low}"
            else:
                text = f"{value:+d}" if value else "0"
            self._values[field].setText(text)

    def _emit(self, *_):
        self._settings = ImageSettings(
            **{name: slider.value() for name, slider in self._sliders.items()},
            enabled=self.enable.isChecked())
        self._update_labels()
        self.changed.emit(self._settings)


def load_settings(qsettings: QtCore.QSettings) -> ImageSettings:
    """Read local per-client preferences, safely bounded to slider ranges."""
    values = {}
    for name, _, lo, hi, _, _ in ImageAdjustmentsDialog._FIELDS:
        try:
            value = int(qsettings.value(f"image/{name}", 0))
        except (TypeError, ValueError):
            value = 0
        values[name] = max(lo, min(hi, value))
    enabled = str(qsettings.value("image/enabled", "true")).lower() in (
        "true", "1", "yes")
    return ImageSettings(**values, enabled=enabled)


def save_settings(qsettings: QtCore.QSettings, settings: ImageSettings):
    """Store only this client's image adjustments (never on the server)."""
    for key, value in asdict(settings).items():
        qsettings.setValue(f"image/{key}", value)
