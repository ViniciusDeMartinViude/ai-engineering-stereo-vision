"""Per-client options for how YOLO detections are annotated on the video.

Display only: detections, YOLO inference and the shared stream are unchanged.
Centre and size are in native camera pixels (origin top-left), the same
coordinate space as the ROI.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace

from PySide6 import QtCore, QtGui, QtWidgets

from control_room_widgets import SegmentedControl, ToggleSwitch, overline
from icons import icon, pixmap
from theme import COLORS, class_color, ui_font
from yolo_detector import Detection


@dataclass(frozen=True)
class AnnotationOptions:
    show: bool = True            # draw boxes, labels and markers at all
    class_name: bool = True
    class_id: bool = False
    confidence: bool = True
    center: bool = False         # box centre (x, y) in the label
    size: bool = False           # box width × height in the label
    center_marker: bool = False  # crosshair at each box centre


PRESETS = {
    'minimal': AnnotationOptions(confidence=False),
    'standard': AnnotationOptions(),
    'detailed': AnnotationOptions(class_id=True, center=True, size=True, center_marker=True),
}
PRESET_ORDER = ('minimal', 'standard', 'detailed')
# (field, switch text, tooltip)
LABEL_FIELDS = (
    ('class_name', 'Class name', 'Model class name, e.g. "box"'),
    ('class_id', 'Class ID', 'Numeric class index from the model'),
    ('confidence', 'Confidence', 'Detection confidence, 0–100 %'),
    ('center', 'Center (x, y)', 'Box centre in native camera pixels, origin top-left'),
    ('size', 'Box size (w × h)', 'Box width × height in native camera pixels'),
)
MARKER_FIELDS = (
    ('center_marker', 'Center marker', 'Crosshair drawn at each box centre'),
)


def preset_of(options: AnnotationOptions):
    """Name of the preset these options match (visibility aside), or None if custom."""
    for name in PRESET_ORDER:
        if replace(PRESETS[name], show=options.show) == options:
            return name
    return None


def next_preset(options: AnnotationOptions) -> str:
    """Preset that the L shortcut moves to: Minimal → Standard → Detailed → Minimal."""
    current = preset_of(options)
    if current is None:
        return 'standard'
    return PRESET_ORDER[(PRESET_ORDER.index(current) + 1) % len(PRESET_ORDER)]


def format_detection_label(det: Detection, options: AnnotationOptions) -> str:
    """Label text: identity on the first line, geometry on the second ('' = box only)."""
    head = []
    if options.class_name:
        head.append(det.label)
    if options.class_id:
        head.append(f'id {det.class_id}' if det.class_id >= 0 else 'id ?')
    if options.confidence:
        head.append(f'{det.confidence:.0%}')
    tail = []
    x1, y1, x2, y2 = det.xyxy
    if options.center:
        cx, cy = det.center
        tail.append(f'({cx:.0f}, {cy:.0f})')
    if options.size:
        tail.append(f'{x2 - x1:.0f}×{y2 - y1:.0f} px')
    return '\n'.join(' · '.join(part) for part in (head, tail) if part)


def load_annotation_options(qsettings: QtCore.QSettings) -> AnnotationOptions:
    values = {}
    for spec in fields(AnnotationOptions):
        raw = qsettings.value(f'annotations/{spec.name}', None)
        values[spec.name] = spec.default if raw is None else str(raw).lower() in ('true', '1', 'yes')
    return AnnotationOptions(**values)


def save_annotation_options(qsettings: QtCore.QSettings, options: AnnotationOptions):
    """Stored per workstation, like the image adjustments."""
    for key, value in asdict(options).items():
        qsettings.setValue(f'annotations/{key}', value)


class AnnotationPopover(QtWidgets.QFrame):
    """Popover with presets, per-field switches and a live label preview.

    A Qt popup: it closes on any click outside it or on Esc. Every change
    applies immediately to both cameras.
    """
    changed = QtCore.Signal(object)
    SAMPLE = Detection('LEFT', 'box', 0.94, (717.0, 385.0, 907.0, 525.0), 0)

    def __init__(self, options: AnnotationOptions, parent=None):
        super().__init__(parent, QtCore.Qt.WindowType.Popup | QtCore.Qt.WindowType.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # Clicking the Labels button while open just closes the popover.
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoMouseReplay, True)
        self._options = options
        self._switches = {}
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(16, 10, 16, 22)  # room for the drop shadow
        card = QtWidgets.QFrame()
        card.setObjectName('popoverCard')
        shadow = QtWidgets.QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QtGui.QColor(0, 0, 0, 150))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)
        root = QtWidgets.QVBoxLayout(card)
        root.setContentsMargins(18, 14, 14, 16)
        root.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(10)
        glyph = QtWidgets.QLabel()
        glyph.setPixmap(pixmap('tag', COLORS['accent'], 18))
        title = QtWidgets.QLabel('Detection labels')
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
        caption = QtWidgets.QLabel('Applies to both cameras on this workstation. Shortcut L cycles the presets.')
        caption.setObjectName('secondary')
        caption.setFont(ui_font(12))
        caption.setWordWrap(True)
        root.addWidget(caption)

        self.show_switch = ToggleSwitch('Show detections')
        self.show_switch.setFont(ui_font(13, QtGui.QFont.Weight.DemiBold))
        self.show_switch.setToolTip('Turn off to inspect the raw image; detection counts keep updating')
        self.show_switch.toggled.connect(self._controls_changed)
        root.addWidget(self.show_switch)

        root.addWidget(self._divider())
        preset_row = QtWidgets.QHBoxLayout()
        self.presets = SegmentedControl((
            ('minimal', 'Minimal', 'Class name only'),
            ('standard', 'Standard', 'Class name and confidence'),
            ('detailed', 'Detailed', 'Class name, ID, confidence, centre, size and centre marker')))
        self.presets.selected.connect(self._preset_selected)
        self.custom_note = QtWidgets.QLabel('Custom')
        self.custom_note.setObjectName('caption')
        self.custom_note.setFont(ui_font(12))
        preset_row.addWidget(self.presets)
        preset_row.addSpacing(6)
        preset_row.addWidget(self.custom_note)
        preset_row.addStretch()
        root.addLayout(preset_row)

        for title_text, group in (('Show in labels', LABEL_FIELDS), ('Markers', MARKER_FIELDS)):
            section = overline(title_text)
            root.addSpacing(2)
            root.addWidget(section)
            for field, text, tooltip in group:
                switch = ToggleSwitch(text)
                switch.setFont(ui_font(13))
                switch.setToolTip(tooltip)
                switch.toggled.connect(self._controls_changed)
                root.addWidget(switch)
                self._switches[field] = switch

        root.addWidget(self._divider())
        root.addWidget(overline('Preview'))
        preview_row = QtWidgets.QHBoxLayout()
        self.preview = QtWidgets.QLabel()
        self.preview.setFont(ui_font(12, QtGui.QFont.Weight.DemiBold))
        preview_row.addWidget(self.preview)
        preview_row.addStretch()
        root.addLayout(preview_row)
        note = QtWidgets.QLabel('Centre and size use native camera pixels, origin top-left (same as the ROI).')
        note.setObjectName('caption')
        note.setFont(ui_font(11))
        note.setWordWrap(True)
        root.addWidget(note)
        self.setMinimumWidth(360)
        self._sync_controls()

    @staticmethod
    def _divider():
        line = QtWidgets.QFrame()
        line.setObjectName('divider')
        line.setFixedHeight(1)
        return line

    @property
    def options(self) -> AnnotationOptions:
        return self._options

    def set_options(self, options: AnnotationOptions):
        """Reflect externally changed options (e.g. the L shortcut) without emitting."""
        self._options = options
        self._sync_controls()

    def _sync_controls(self):
        options = self._options
        widgets = [self.show_switch, *self._switches.values()]
        for widget in widgets:
            widget.blockSignals(True)
        self.show_switch.setChecked(options.show)
        for field, switch in self._switches.items():
            switch.setChecked(getattr(options, field))
            switch.setEnabled(options.show)
        for widget in widgets:
            widget.blockSignals(False)
        self.presets.setEnabled(options.show)
        preset = preset_of(options)
        if preset is None:
            self.presets.clear_selection()
        elif self.presets.current != preset:
            self.presets.select(preset, emit=False)
        self.custom_note.setVisible(preset is None)
        self._update_preview()

    def _update_preview(self):
        text = format_detection_label(self.SAMPLE, self._options)
        if not self._options.show:
            self._muted_preview('Detections hidden')
        elif not text:
            self._muted_preview('Boxes only, no label text')
        else:
            self.preview.setText(text)
            self.preview.setStyleSheet(
                f'background:{class_color(self.SAMPLE.label)}; color:{COLORS["background"]};'
                'border-radius:4px; padding:3px 7px;')

    def _muted_preview(self, message):
        self.preview.setText(message)
        self.preview.setStyleSheet(f'color:{COLORS["muted"]}; background:transparent; padding:3px 0px;')

    def _controls_changed(self, *_):
        values = {field: switch.isChecked() for field, switch in self._switches.items()}
        self._options = AnnotationOptions(show=self.show_switch.isChecked(), **values)
        self._sync_controls()
        self.changed.emit(self._options)

    def _preset_selected(self, name):
        self._options = replace(PRESETS[name], show=self._options.show)
        self._sync_controls()
        self.changed.emit(self._options)
