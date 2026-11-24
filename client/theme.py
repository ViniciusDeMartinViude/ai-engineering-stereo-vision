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
    "on_accent": "#0B1016",
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

# Things drawn ON the camera image keep these colours in every theme: they must
# read on video, not on the UI. Ink is the text on bright fills (box labels, ROI tag).
VIDEO_INK = "#0B1016"
VIDEO_OVERLAY = {"background": "rgba(11, 16, 22, 215)", "hint": "rgba(11, 16, 22, 190)",
                 "border": "#34465C", "text": "#E6EDF5", "secondary": "#A3B3C6",
                 "neutral": "#8A9AAD", "warning": "#F5B83D", "bad": "#FF6B6B", "info": "#4CC2FF"}

# ---- Colour themes ------------------------------------------------------------
# Each theme sets every UI token above except "roi" (drawn on video). The palettes
# are inspired by popular VS Code colour themes, adjusted so that text, state and
# accent colours keep WCAG AA contrast on the surfaces they're used on
# (tests/test_themes.py checks every theme).
THEME_TOKENS = ("background", "surface", "raised", "hover", "border", "border_strong", "text",
                "secondary", "muted", "accent", "on_accent", "good", "warning", "bad", "info",
                "neutral", "video", "skew")
DEFAULT_THEME = "control-room"
THEMES = {
    "control-room": {
        "name": "Control Room", "inspired": "The client's original dark theme", "dark": True,
        "colors": {k: COLORS[k] for k in THEME_TOKENS},
    },
    "dark-modern": {
        "name": "Dark Modern", "inspired": "Inspired by VS Code's default dark theme", "dark": True,
        "colors": {"background": "#1F1F1F", "surface": "#181818", "raised": "#262626",
                   "hover": "#313131", "border": "#2B2B2B", "border_strong": "#454545",
                   "text": "#CCCCCC", "secondary": "#A8A8A8", "muted": "#8F8F8F",
                   "accent": "#4DAAFC", "on_accent": "#181818", "good": "#89D185",
                   "warning": "#CCA700", "bad": "#F48771", "info": "#75BEFF",
                   "neutral": "#8F8F8F", "video": "#111111", "skew": "#C586C0"},
    },
    "light-modern": {
        "name": "Light Modern", "inspired": "Inspired by VS Code's default light theme", "dark": False,
        "colors": {"background": "#F8F8F8", "surface": "#FFFFFF", "raised": "#F3F3F3",
                   "hover": "#E8E8E8", "border": "#E5E5E5", "border_strong": "#C8C8C8",
                   "text": "#3B3B3B", "secondary": "#5F5F5F", "muted": "#6B6B6B",
                   "accent": "#005FB8", "on_accent": "#FFFFFF", "good": "#1E7A36",
                   "warning": "#8A6100", "bad": "#C42B1C", "info": "#1A64B4",
                   "neutral": "#6B6B6B", "video": "#DADADA", "skew": "#8250DF"},
    },
    "monokai": {
        "name": "Monokai", "inspired": "Inspired by the Monokai theme", "dark": True,
        "colors": {"background": "#272822", "surface": "#1E1F1C", "raised": "#34352F",
                   "hover": "#3E3D32", "border": "#3B3C35", "border_strong": "#5B5C52",
                   "text": "#F8F8F2", "secondary": "#CFCFC2", "muted": "#A59F85",
                   "accent": "#66D9EF", "on_accent": "#1E1F1C", "good": "#A6E22E",
                   "warning": "#E6DB74", "bad": "#FF4F8B", "info": "#66D9EF",
                   "neutral": "#A59F85", "video": "#161713", "skew": "#AE81FF"},
    },
    "dracula": {
        "name": "Dracula", "inspired": "Inspired by the Dracula theme", "dark": True,
        "colors": {"background": "#282A36", "surface": "#21222C", "raised": "#343746",
                   "hover": "#44475A", "border": "#3A3D4D", "border_strong": "#5A5D74",
                   "text": "#F8F8F2", "secondary": "#C3C6DB", "muted": "#9EA6CC",
                   "accent": "#BD93F9", "on_accent": "#21222C", "good": "#50FA7B",
                   "warning": "#F1FA8C", "bad": "#FF6E6E", "info": "#8BE9FD",
                   "neutral": "#9EA6CC", "video": "#191A21", "skew": "#FF79C6"},
    },
    "one-dark": {
        "name": "One Dark Pro", "inspired": "Inspired by the One Dark Pro theme", "dark": True,
        "colors": {"background": "#282C34", "surface": "#21252B", "raised": "#2C313A",
                   "hover": "#353B45", "border": "#3A404B", "border_strong": "#4B5263",
                   "text": "#C8CCD4", "secondary": "#ABB2BF", "muted": "#9199A6",
                   "accent": "#61AFEF", "on_accent": "#21252B", "good": "#98C379",
                   "warning": "#E5C07B", "bad": "#E77B84", "info": "#56B6C2",
                   "neutral": "#9199A6", "video": "#1B1F24", "skew": "#C678DD"},
    },
    "solarized-dark": {
        "name": "Solarized Dark", "inspired": "Inspired by the Solarized Dark theme", "dark": True,
        "colors": {"background": "#002B36", "surface": "#00212B", "raised": "#073642",
                   "hover": "#0B4250", "border": "#0E4553", "border_strong": "#2A5E6B",
                   "text": "#A2AFAF", "secondary": "#93A1A1", "muted": "#869799",
                   "accent": "#3FA1E6", "on_accent": "#00212B", "good": "#9DB300",
                   "warning": "#CC9B00", "bad": "#F2554F", "info": "#2DB3A9",
                   "neutral": "#869799", "video": "#001A21", "skew": "#8C90D8"},
    },
    "solarized-light": {
        "name": "Solarized Light", "inspired": "Inspired by the Solarized Light theme", "dark": False,
        "colors": {"background": "#EEE8D5", "surface": "#FDF6E3", "raised": "#F5EEDA",
                   "hover": "#E9E2CC", "border": "#DDD6C1", "border_strong": "#C2BAA2",
                   "text": "#073642", "secondary": "#4C6168", "muted": "#566A70",
                   "accent": "#1A6AA6", "on_accent": "#FDF6E3", "good": "#5A6B00",
                   "warning": "#7E5D00", "bad": "#B8271F", "info": "#1A7A74",
                   "neutral": "#566A70", "video": "#E2DAC3", "skew": "#5B5FAF"},
    },
    "nord": {
        "name": "Nord", "inspired": "Inspired by the Nord theme", "dark": True,
        "colors": {"background": "#2E3440", "surface": "#3B4252", "raised": "#434C5E",
                   "hover": "#4C566A", "border": "#4C566A", "border_strong": "#616E88",
                   "text": "#ECEFF4", "secondary": "#D8DEE9", "muted": "#B9C2D0",
                   "accent": "#88C0D0", "on_accent": "#2E3440", "good": "#A3BE8C",
                   "warning": "#EBCB8B", "bad": "#F09AA0", "info": "#8FBCBB",
                   "neutral": "#B9C2D0", "video": "#242933", "skew": "#C9A3C3"},
    },
    "high-contrast": {
        "name": "Dark High Contrast", "inspired": "Inspired by VS Code's Dark High Contrast theme",
        "dark": True,
        "colors": {"background": "#000000", "surface": "#000000", "raised": "#0E0E0E",
                   "hover": "#1C1C1C", "border": "#6FC3DF", "border_strong": "#6FC3DF",
                   "text": "#FFFFFF", "secondary": "#E6E6E6", "muted": "#CFCFCF",
                   "accent": "#F38518", "on_accent": "#000000", "good": "#89D185",
                   "warning": "#FFD54F", "bad": "#FF8F7A", "info": "#75BEFF",
                   "neutral": "#CFCFCF", "video": "#000000", "skew": "#E78BE7"},
    },
}
CURRENT_THEME = DEFAULT_THEME


def theme_id(value):
    """A known theme id for ``value`` (an id or a display name), else the default."""
    text = str(value or "").strip().lower()
    for key, theme in THEMES.items():
        if text in (key, theme["name"].lower()):
            return key
    return DEFAULT_THEME


def select_theme(value):
    """Make ``value`` the active theme. Updates COLORS and STATE_COLORS in place.

    Widgets read these when they are built, so the window must be built (or
    rebuilt) after this call. Returns the theme id actually selected.
    """
    global CURRENT_THEME
    key = theme_id(value)
    COLORS.update(THEMES[key]["colors"])
    for state in ("good", "warning", "bad", "info", "neutral"):
        STATE_COLORS[state] = COLORS[state]
    STATE_COLORS["stale"] = COLORS["neutral"]
    CURRENT_THEME = key
    return key


def load_theme_setting(qsettings):
    return theme_id(qsettings.value("ui/theme", DEFAULT_THEME))


def save_theme_setting(qsettings, key):
    qsettings.setValue("ui/theme", theme_id(key))


def mix(a, b, amount):
    """Colour ``amount`` (0-1) of the way from ``a`` to ``b``, as #RRGGBB."""
    ca, cb = QtGui.QColor(a), QtGui.QColor(b)
    return QtGui.QColor(round(ca.red() + (cb.red() - ca.red()) * amount),
                        round(ca.green() + (cb.green() - ca.green()) * amount),
                        round(ca.blue() + (cb.blue() - ca.blue()) * amount)).name()


def rgba(color, alpha):
    c = QtGui.QColor(color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"


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
    if app is None or app.property("stereoThemeApplied") == CURRENT_THEME:
        return
    app.setStyle("Fusion")
    c = COLORS
    palette = QtGui.QPalette()
    role = QtGui.QPalette.ColorRole
    for name, value in ((role.Window, c["background"]), (role.WindowText, c["text"]),
                        (role.Base, c["surface"]), (role.AlternateBase, c["raised"]),
                        (role.Text, c["text"]), (role.Button, c["raised"]),
                        (role.ButtonText, c["text"]), (role.Highlight, c["accent"]),
                        (role.HighlightedText, c["on_accent"]), (role.ToolTipBase, c["raised"]),
                        (role.ToolTipText, c["text"]), (role.PlaceholderText, c["muted"]),
                        (role.Link, c["accent"]), (role.BrightText, c["bad"])):
        palette.setColor(name, QtGui.QColor(value))
    disabled = QtGui.QPalette.ColorGroup.Disabled
    for name in (role.WindowText, role.Text, role.ButtonText):
        palette.setColor(disabled, name, QtGui.QColor(c["muted"]))
    app.setPalette(palette)
    app.setFont(ui_font())
    app.setStyleSheet(style_sheet())
    app.setProperty("stereoThemeApplied", CURRENT_THEME)


def style_sheet():
    """One application-wide QSS, never reinstalled per frame.

    Fonts are set in code (ui_font/display_font/numeric_font) so these rules
    only carry colour, borders and spacing.
    """
    c = COLORS
    v = VIDEO_OVERLAY
    dark = THEMES[CURRENT_THEME]["dark"]
    accent_hover = mix(c['accent'], '#FFFFFF' if dark else '#000000', 0.25)
    popover = rgba(c['surface'], 242)
    table_alt = mix(c['surface'], c['raised'], 0.45)
    modal_dim = rgba('#04070A', 150) if dark else rgba('#000000', 70)
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
        background-color:{v['background']}; border:1px solid {v['border']};
        border-radius:12px;
    }}
    QLabel#overlayTitle {{ color:{v['text']}; }}
    QLabel#overlaySubtitle {{ color:{v['secondary']}; }}
    QLabel#overlayMark {{ color:{v['neutral']}; }}
    QLabel#overlayMark[state="warning"] {{ color:{v['warning']}; }}
    QLabel#overlayMark[state="bad"] {{ color:{v['bad']}; }}
    QLabel#overlayMark[state="info"] {{ color:{v['info']}; }}
    QLabel#hint {{
        color:{v['text']}; background-color:{v['hint']};
        border:1px solid {v['border']}; border-radius:8px; padding:5px 10px;
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
        background-color:{c['accent']}; color:{c['on_accent']}; border-color:{c['accent']};
        font-weight:600;
    }}
    QPushButton#primary:hover {{ background-color:{accent_hover}; }}
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
    QSlider::handle:horizontal:hover {{ background:{c['surface']}; }}
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
        background-color:{popover};
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
        selection-background-color:{c['accent']}; selection-color:{c['on_accent']};
    }}
    QAbstractSpinBox:focus {{ border-color:{c['accent']}; }}

    /* Detections table */
    QTableView {{
        background-color:{c['surface']}; alternate-background-color:{table_alt};
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
    QWidget#modalDim {{ background-color:{modal_dim}; }}

    QSplitter::handle {{ background:transparent; }}
    QSplitter::handle:hover {{ background-color:{c['border']}; }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ background:transparent; border:0px; }}
    QScrollBar:vertical {{ background:transparent; width:10px; margin:2px; }}
    QScrollBar::handle:vertical {{ background:{c['border_strong']}; border-radius:3px; min-height:24px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height:0px; width:0px; }}
    """
