"""Visual design tokens, theme and provisional operator thresholds.

All palette/font/QSS decisions for the control-room interface live here.
The thresholds are DISPLAY defaults, not validated competition limits.

Colour is reserved for meaning: the accent marks interactive/selected
elements, the state colours mark health, and detection classes get their own
categorical palette. Every text colour meets WCAG AA (4.5:1) on the surfaces
it is used on.
"""
import zlib
from dataclasses import dataclass

from PySide6 import QtGui, QtWidgets

COLORS = {
    "background": "#0B1016",
    "surface": "#121A23",
    "raised": "#1A2430",
    "hover": "#223041",
    "border": "#253244",
    "border_strong": "#34465C",
    "text": "#E6EDF5",
    "secondary": "#A3B3C6",
    "muted": "#8293A8",
    "accent": "#4CC2FF",
    "good": "#3DD68C",
    "warning": "#F5B83D",
    "bad": "#FF6B6B",
    "info": "#7AA7FF",
    "neutral": "#8A9AAD",
    "video": "#070A0E",
    "skew": "#B69CFF",
    "roi": "#F4F7FB",
}
STATE_COLORS = {state: COLORS[state] for state in ("good", "warning", "bad", "info", "neutral")}
STATE_COLORS["stale"] = COLORS["neutral"]  # last reading kept, but no new frames
# Shape + word accompany every state colour, so state never relies on hue alone.
STATE_MARKS = {"good": "●", "warning": "▲", "bad": "✕", "info": "◆", "neutral": "○", "stale": "◌"}
STATE_WORDS = {"good": "OK", "warning": "Watch", "bad": "High", "info": "Info",
               "neutral": "No data", "stale": "Stale"}
# Detection classes: high contrast against the green belt and dark UI.
CLASS_COLORS = ("#FFB547", "#FF6FB5", "#A98BFF", "#4FD8F0", "#FF8A5B", "#F2F5F8")

SPACING = (4, 8, 12, 16, 24)
RADIUS = 8
CARD_RADIUS = 10
FONT_UI = "Segoe UI"
FONT_NUMERIC = "Consolas"
UI_FAMILIES = ("Segoe UI Variable Text", "Segoe UI")
DISPLAY_FAMILIES = ("Bahnschrift", "Segoe UI Variable Display", "Segoe UI")
NUMERIC_FAMILIES = ("Cascadia Mono", "Consolas")
FONTS = {"title": 16, "section": 14, "body": 13, "caption": 12, "small": 11, "number": 27}


@dataclass(frozen=True)
class Thresholds:
    """Replace these provisional UI limits with measured operating targets."""
    delay_good_below_ms: float = 150.0
    delay_warn_below_ms: float = 300.0
    skew_good_below_ms: float = 5.0
    skew_warn_below_ms: float = 16.0
    stale_after_s: float = 0.5
    stream_disconnected_after_s: float = 3.0
    recent_error_window_s: float = 10.0


THRESHOLDS = Thresholds()


def quality(value, good_below, warning_below):
    """Classification for absolute operator limits; not for raw data changes."""
    if value is None:
        return "neutral"
    if abs(value) < good_below:
        return "good"
    if abs(value) < warning_below:
        return "warning"
    return "bad"


def class_color(label: str) -> str:
    """Stable colour per detection class name (same class, same colour)."""
    return CLASS_COLORS[zlib.crc32(str(label).encode("utf-8")) % len(CLASS_COLORS)]


def tint(color, alpha: int) -> QtGui.QColor:
    value = QtGui.QColor(color)
    value.setAlpha(alpha)
    return value


def _font(families, pixel_size, weight, spacing=0.0, caps=False):
    font = QtGui.QFont()
    font.setFamilies(list(families))
    font.setPixelSize(int(pixel_size))
    font.setWeight(weight)
    if spacing:
        font.setLetterSpacing(QtGui.QFont.SpacingType.AbsoluteSpacing, spacing)
    if caps:
        font.setCapitalization(QtGui.QFont.Capitalization.AllUppercase)
    return font


def ui_font(pixel_size=FONTS["body"], weight=QtGui.QFont.Weight.Normal):
    return _font(UI_FAMILIES, pixel_size, weight)


def display_font(pixel_size=FONTS["small"], weight=QtGui.QFont.Weight.DemiBold, spacing=0.8):
    """Condensed DIN-style face for overline labels and instrument units."""
    return _font(DISPLAY_FAMILIES, pixel_size, weight, spacing, caps=True)


def numeric_font(pixel_size=FONTS["body"], weight=QtGui.QFont.Weight.Normal):
    """Monospaced figures so changing values never shift their neighbours."""
    return _font(NUMERIC_FAMILIES, pixel_size, weight)


def apply_theme(app: QtWidgets.QApplication):
    """Install style, palette, font and QSS once per application."""
    if app is None or app.property("stereoThemeApplied"):
        return
    app.setStyle("Fusion")
    c = COLORS
    palette = QtGui.QPalette()
    role = QtGui.QPalette.ColorRole
    for name, value in ((role.Window, c["background"]), (role.WindowText, c["text"]),
                        (role.Base, c["surface"]), (role.AlternateBase, c["raised"]),
                        (role.Text, c["text"]), (role.Button, c["raised"]),
                        (role.ButtonText, c["text"]), (role.Highlight, c["accent"]),
                        (role.HighlightedText, c["background"]), (role.ToolTipBase, c["raised"]),
                        (role.ToolTipText, c["text"]), (role.PlaceholderText, c["muted"]),
                        (role.Link, c["accent"]), (role.BrightText, c["bad"])):
        palette.setColor(name, QtGui.QColor(value))
    disabled = QtGui.QPalette.ColorGroup.Disabled
    for name in (role.WindowText, role.Text, role.ButtonText):
        palette.setColor(disabled, name, QtGui.QColor(c["muted"]))
    app.setPalette(palette)
    app.setFont(ui_font())
    app.setStyleSheet(style_sheet())
    app.setProperty("stereoThemeApplied", True)


def style_sheet():
    """One application-wide QSS, never reinstalled per frame.

    Fonts are set in code (ui_font/display_font/numeric_font) so these rules
    only carry colour, borders and spacing.
    """
    c = COLORS
    return f"""
    QMainWindow, QWidget#central {{ background-color:{c['background']}; }}
    QWidget {{ color:{c['text']}; }}
    QToolTip {{
        background-color:{c['raised']}; color:{c['text']};
        border:1px solid {c['border_strong']}; border-radius:6px; padding:6px 8px;
    }}

    /* App bar */
    QFrame#appBar {{
        background-color:{c['surface']}; border:0px;
        border-bottom:1px solid {c['border']};
    }}
    QLabel#brandTitle {{ color:{c['text']}; }}
    QLabel#brandCaption {{ color:{c['muted']}; }}

    /* Cards */
    QFrame#card, QFrame#kpiCard, QFrame#cameraTile {{
        background-color:{c['surface']}; border:1px solid {c['border']};
        border-radius:{CARD_RADIUS}px;
    }}
    QFrame#tileHeader, QFrame#cardHeader {{
        background:transparent; border:0px; border-bottom:1px solid {c['border']};
        border-top-left-radius:{CARD_RADIUS}px; border-top-right-radius:{CARD_RADIUS}px;
    }}
    QLabel {{ background:transparent; }}
    QLabel#tileTitle, QLabel#cardTitle {{ color:{c['text']}; }}
    QLabel#overline {{ color:{c['secondary']}; }}
    QLabel#caption, QLabel#hud {{ color:{c['muted']}; }}
    QLabel#secondary {{ color:{c['secondary']}; }}
    QLabel#sideBadge {{
        color:{c['accent']}; border:1px solid {c['accent']}; border-radius:5px;
        padding:0px 5px;
    }}
    QLabel#pill {{
        color:{c['secondary']}; background-color:{c['raised']};
        border:1px solid {c['border']}; border-radius:10px; padding:2px 9px;
    }}
    QLabel#pill[state="active"] {{ color:{c['text']}; border-color:{c['border_strong']}; }}
    QLabel#stateBadge {{ color:{c['muted']}; }}
    QLabel#stateBadge[state="good"] {{ color:{c['good']}; }}
    QLabel#stateBadge[state="warning"] {{ color:{c['warning']}; }}
    QLabel#stateBadge[state="bad"] {{ color:{c['bad']}; }}
    QLabel#metricValue {{ color:{c['text']}; }}
    QLabel#metricValue[state="warning"] {{ color:{c['warning']}; }}
    QLabel#metricValue[state="bad"] {{ color:{c['bad']}; }}
    QLabel#metricValue[state="good"] {{ color:{c['good']}; }}
    QLabel#metricLabel {{ color:{c['muted']}; }}
    QLabel#legend {{ color:{c['muted']}; }}

    /* Overlays drawn on top of video */
    QFrame#overlayCard {{
        background-color:rgba(11, 16, 22, 215); border:1px solid {c['border_strong']};
        border-radius:12px;
    }}
    QLabel#overlayTitle {{ color:{c['text']}; }}
    QLabel#overlaySubtitle {{ color:{c['secondary']}; }}
    QLabel#overlayMark {{ color:{c['neutral']}; }}
    QLabel#overlayMark[state="warning"] {{ color:{c['warning']}; }}
    QLabel#overlayMark[state="bad"] {{ color:{c['bad']}; }}
    QLabel#overlayMark[state="info"] {{ color:{c['accent']}; }}
    QLabel#hint {{
        color:{c['text']}; background-color:rgba(11, 16, 22, 190);
        border:1px solid {c['border']}; border-radius:8px; padding:5px 10px;
    }}

    /* Buttons */
    QPushButton {{
        background-color:{c['raised']}; color:{c['text']};
        border:1px solid {c['border']}; border-radius:{RADIUS}px; padding:5px 12px;
    }}
    QPushButton:hover {{ background-color:{c['hover']}; border-color:{c['border_strong']}; }}
    QPushButton:pressed {{ background-color:{c['border']}; }}
    QPushButton:focus {{ border-color:{c['accent']}; }}
    QPushButton[active="true"] {{ color:{c['accent']}; border-color:{c['accent']}; }}
    QPushButton#primary {{
        background-color:{c['accent']}; color:{c['background']}; border-color:{c['accent']};
        font-weight:600;
    }}
    QPushButton#primary:hover {{ background-color:#7AD3FF; }}
    QPushButton#flat {{ background:transparent; border-color:transparent; color:{c['secondary']}; }}
    QPushButton#flat:hover {{ color:{c['text']}; background-color:{c['raised']}; }}
    QPushButton:disabled {{ color:{c['muted']}; }}
    QToolButton {{
        background:transparent; border:1px solid transparent; border-radius:6px; padding:4px;
    }}
    QToolButton:hover {{ background-color:{c['hover']}; border-color:{c['border']}; }}
    QToolButton:checked {{ background-color:{c['hover']}; border-color:{c['border_strong']}; }}

    /* Segmented control */
    QFrame#segmented {{
        background-color:{c['background']}; border:1px solid {c['border']}; border-radius:8px;
    }}
    QPushButton#segment {{
        background:transparent; border:0px; border-radius:6px; padding:4px 12px;
        color:{c['secondary']};
    }}
    QPushButton#segment:hover {{ color:{c['text']}; background-color:{c['raised']}; }}
    QPushButton#segment:checked {{ color:{c['text']}; background-color:{c['hover']}; }}

    /* Sliders (image adjustments) */
    QSlider::groove:horizontal {{
        background:{c['border']}; height:4px; border-radius:2px;
    }}
    QSlider::sub-page:horizontal, QSlider::add-page:horizontal {{ background:transparent; }}
    QSlider::handle:horizontal {{
        background:{c['text']}; border:2px solid {c['accent']};
        width:12px; height:12px; margin:-6px 0; border-radius:8px;
    }}
    QSlider::handle:horizontal:hover {{ background:#FFFFFF; }}
    QSlider#temperatureSlider::groove:horizontal {{
        background:qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 #5AA9FF, stop:0.5 {c['border_strong']}, stop:1 #FFB547);
    }}
    QSlider#tintSlider::groove:horizontal {{
        background:qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 #F07BE0, stop:0.5 {c['border_strong']}, stop:1 #5BD68C);
    }}

    /* Floating image adjustment panel */
    QDialog {{ background-color:transparent; }}
    QFrame#imageAdjustmentsPanel, QFrame#popoverCard {{
        background-color:rgba(18, 26, 35, 242);
        border:1px solid {c['border_strong']}; border-radius:12px;
    }}
    QFrame#imageAdjustmentsPanel QLabel, QFrame#imageAdjustmentsPanel QCheckBox,
    QFrame#popoverCard QCheckBox {{
        background:transparent;
    }}
    QFrame#divider {{ background-color:{c['border']}; border:0px; max-height:1px; }}

    /* Clickable KPI status badges open the limits dialog */
    QPushButton#stateBadge {{
        background:transparent; border:0px; padding:0px 2px; color:{c['muted']};
    }}
    QPushButton#stateBadge[state="good"] {{ color:{c['good']}; }}
    QPushButton#stateBadge[state="warning"] {{ color:{c['warning']}; }}
    QPushButton#stateBadge[state="bad"] {{ color:{c['bad']}; }}
    QPushButton#stateBadge:hover {{ text-decoration:underline; }}

    /* Numeric inputs (status limits) */
    QAbstractSpinBox {{
        background-color:{c['background']}; color:{c['text']};
        border:1px solid {c['border_strong']}; border-radius:6px; padding:4px 6px;
        selection-background-color:{c['accent']}; selection-color:{c['background']};
    }}
    QAbstractSpinBox:focus {{ border-color:{c['accent']}; }}

    /* Detections table */
    QTableView {{
        background-color:{c['surface']}; alternate-background-color:#151E29;
        border:0px; gridline-color:{c['border']}; color:{c['text']};
        selection-background-color:{c['hover']}; selection-color:{c['text']};
    }}
    /* Right padding keeps numbers aligned with headers and clear of the sort arrow */
    QTableView::item {{ padding:0px 20px 0px 8px; border:0px; }}
    QHeaderView::section {{
        background-color:{c['raised']}; color:{c['secondary']}; border:0px;
        border-bottom:1px solid {c['border']}; padding:4px 20px 4px 8px;
    }}
    QTableCornerButton::section {{ background-color:{c['raised']}; border:0px; }}

    /* Backdrop behind modal dialogs */
    QWidget#modalDim {{ background-color:rgba(4, 7, 10, 150); }}

    QSplitter::handle {{ background:transparent; }}
    QSplitter::handle:hover {{ background-color:{c['border']}; }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ background:transparent; border:0px; }}
    QScrollBar:vertical {{ background:transparent; width:10px; margin:2px; }}
    QScrollBar::handle:vertical {{ background:{c['border_strong']}; border-radius:3px; min-height:24px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height:0px; width:0px; }}
    """
