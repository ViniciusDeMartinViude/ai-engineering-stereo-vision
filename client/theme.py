"""Visual design tokens, theme and provisional operator thresholds.

All palette/font/QSS decisions for the control-room interface live here.
The thresholds are DISPLAY defaults, not validated competition limits.
"""
from dataclasses import dataclass

COLORS = {
    "background": "#0D1520",
    "surface": "#172433",
    "raised": "#203247",
    "border": "#3D536B",
    "text": "#F2F6FC",
    "secondary": "#B8C9DA",
    "accent": "#69C9F5",
    "good": "#60D6A3",
    "warning": "#F2BD65",
    "bad": "#F27F85",
    "info": "#87B8FA",
    "neutral": "#98A9BA",
    "video": "#090F19",
}
SPACING = (4, 8, 12, 16, 24)
RADIUS = 8
FONT_UI = "Segoe UI"
FONT_NUMERIC = "Consolas"
FONTS = {"title": 20, "section": 14, "body": 13, "caption": 12, "small": 11, "number": 27}


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


def style_sheet():
    """One application-wide QSS, never reinstalled per frame."""
    c = COLORS
    return f"""
    QWidget {{
        background-color:{c['background']}; color:{c['text']};
        font-family:"{FONT_UI}"; font-size:{FONTS['body']}px;
    }}
    QFrame#controlCard, QFrame#cameraPanel {{
        background-color:{c['surface']}; border:1px solid {c['border']};
        border-radius:{RADIUS}px;
    }}
    QDialog {{
        background-color:transparent;
    }}
    QFrame#imageAdjustmentsPanel {{
        background-color:rgba(23, 36, 51, 235);
        border:1px solid {c['accent']};
        border-radius:{RADIUS}px;
    }}
    QFrame#imageAdjustmentsPanel QLabel,
    QFrame#imageAdjustmentsPanel QCheckBox {{
        background:transparent;
    }}
    QSlider::groove:horizontal {{
        background:{c['border']}; height:5px; border-radius:2px;
    }}
    QSlider::handle:horizontal {{
        background:{c['accent']}; width:15px; margin:-6px 0;
        border-radius:7px;
    }}
    QSlider::sub-page:horizontal {{
        background:{c['accent']}; border-radius:2px;
    }}
    QFrame#cameraHeader, QFrame#diagnosticsHeader {{
        background-color:{c['raised']}; border:1px solid {c['border']};
        border-radius:{RADIUS}px;
    }}
    QFrame#cameraHeader QLabel, QFrame#diagnosticsHeader QLabel {{
        background-color:transparent; border:0px;
    }}
    QLabel#diagnosticsSummary {{
        background-color:transparent;
        color:{c['secondary']}; font-size:{FONTS['caption']}px;
    }}
    QFrame#instrumentCard {{
        background-color:{c['surface']}; border:1px solid {c['border']};
        border-radius:{RADIUS}px;
    }}
    QFrame#sevenSegmentInstrument {{
        background-color:{c['raised']}; border:0px;
        border-radius:{RADIUS}px;
    }}
    QLabel#appTitle {{
        color:{c['text']}; font-size:{FONTS['title']}px; font-weight:700;
    }}
    QLabel#sectionTitle, QLabel#cameraTitle {{
        color:{c['text']}; font-size:{FONTS['section']}px; font-weight:600;
    }}
    QLabel#secondary, QLabel#minor, QLabel#hint {{
        color:{c['secondary']}; font-size:{FONTS['caption']}px;
    }}
    QLabel#metricValue {{
        color:{c['text']}; font-family:"{FONT_NUMERIC}";
        font-size:{FONTS['section']}px; font-weight:600;
    }}
    QLabel#metricLabel {{
        color:{c['secondary']}; font-size:{FONTS['small']}px;
    }}
    QLabel#statusChip {{
        background-color:{c['raised']}; border:1px solid {c['border']};
        border-radius:{RADIUS}px; padding:5px 9px; font-size:12px;
        color:{c['text']};
    }}
    QLabel#statusChip[state="good"] {{ border-color:{c['good']}; }}
    QLabel#statusChip[state="warning"] {{ border-color:{c['warning']}; }}
    QLabel#statusChip[state="bad"] {{ border-color:{c['bad']}; }}
    QLabel#statusChip[state="info"] {{ border-color:{c['info']}; }}
    QLabel#statusChip[state="neutral"] {{ border-color:{c['border']}; }}
    QPushButton {{
        background-color:{c['raised']}; color:{c['text']};
        border:1px solid {c['border']}; padding:5px 10px;
        border-radius:{RADIUS}px;
    }}
    QPushButton:hover {{ border-color:{c['accent']}; }}
    QPushButton:checked, QPushButton[active="true"] {{
        background-color:{c['accent']}; color:{c['background']};
        border-color:{c['accent']}; font-weight:700;
    }}
    QPushButton:disabled {{ color:{c['secondary']}; }}
    QComboBox {{
        background:{c['raised']}; border:1px solid {c['border']};
        padding:4px 8px; border-radius:{RADIUS}px;
    }}
    QComboBox QAbstractItemView {{
        color:{c['text']}; background:{c['raised']};
        selection-background-color:{c['border']};
    }}
    QStatusBar {{ background:{c['surface']}; color:{c['secondary']}; }}
    QSplitter::handle {{ background:{c['border']}; width:3px; }}
    QToolTip {{ background:{c['raised']}; color:{c['text']};
                border:1px solid {c['border']}; }}
    """
