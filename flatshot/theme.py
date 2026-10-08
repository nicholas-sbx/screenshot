"""The Flatshot look: one fixed palette, flat surfaces, rounded squares.

Nothing here reads from the desktop — the app opts out of the user's Qt
style, palette and fonts so it looks the same everywhere.
"""

from flatshot.qt import QColor, QFont, QPalette


class C:
    """'Ember' palette — warm ink surfaces with a single hot accent."""

    INK = QColor("#121218")
    BASE = QColor("#1A1A22")
    RAISED = QColor("#24242E")
    HOVER = QColor("#2E2E3A")
    LINE = QColor("#363642")
    TEXT = QColor("#F2EEE6")
    SOFT = QColor("#C9C5BD")
    MUTED = QColor("#8B8898")
    ACCENT = QColor("#FF6A3D")
    ACCENT_SOFT = QColor(255, 106, 61, 46)
    CODE = QColor("#3DDC97")
    CODE_SOFT = QColor(61, 220, 151, 40)
    DIM = QColor(12, 12, 18, 150)


# Annotation colours, in toolbar order (keys 1-7).
SWATCHES = [
    QColor("#FF6A3D"),  # ember
    QColor("#FFC53D"),  # amber
    QColor("#3DDC97"),  # mint
    QColor("#3DB2FF"),  # sky
    QColor("#9B7BFF"),  # violet
    QColor("#F2EEE6"),  # bone
    QColor("#121218"),  # ink
]

# Stroke widths for the three size steps.
SIZES = [2.0, 4.0, 7.0]

FONT_FAMILIES = ["Inter", "Manrope", "IBM Plex Sans", "Noto Sans", "DejaVu Sans", "sans-serif"]
MONO_FAMILIES = ["JetBrains Mono", "IBM Plex Mono", "Noto Sans Mono", "DejaVu Sans Mono", "monospace"]


def font(px: int, weight: QFont.Weight = QFont.Weight.Medium, mono: bool = False) -> QFont:
    f = QFont()
    f.setFamilies(MONO_FAMILIES if mono else FONT_FAMILIES)
    f.setPixelSize(px)
    f.setWeight(weight)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def is_light(color: QColor) -> bool:
    return 0.2126 * color.redF() + 0.7152 * color.greenF() + 0.0722 * color.blueF() > 0.55


def apply(app) -> None:
    """Force our own style, palette and font regardless of the desktop."""
    app.setStyle("Fusion")
    pal = QPalette()
    for role, color in {
        QPalette.ColorRole.Window: C.BASE,
        QPalette.ColorRole.WindowText: C.TEXT,
        QPalette.ColorRole.Base: C.RAISED,
        QPalette.ColorRole.AlternateBase: C.BASE,
        QPalette.ColorRole.Text: C.TEXT,
        QPalette.ColorRole.Button: C.RAISED,
        QPalette.ColorRole.ButtonText: C.TEXT,
        QPalette.ColorRole.Highlight: C.ACCENT,
        QPalette.ColorRole.HighlightedText: C.INK,
        QPalette.ColorRole.ToolTipBase: C.BASE,
        QPalette.ColorRole.ToolTipText: C.TEXT,
        QPalette.ColorRole.PlaceholderText: C.MUTED,
    }.items():
        pal.setColor(role, color)
    app.setPalette(pal)
    app.setFont(font(13))
