"""The Flatshot look: flat surfaces, rounded squares and a choice of palettes.

Nothing here reads from the desktop — the app opts out of the user's Qt
style, palette and fonts so it looks the same everywhere.
"""

from pathlib import Path

from flatshot.qt import QColor, QFont, QPalette

ICON_PATH = str(Path(__file__).with_name("assets") / "flatshot.png")


class C:
    """The palette in use. Widgets read these when they paint, so use()
    switches every window over on its next repaint."""

    INK = QColor("#121218")  # deepest surface: input fields, loupe
    BASE = QColor("#1A1A22")  # windows and panels
    RAISED = QColor("#24242E")
    HOVER = QColor("#2E2E3A")
    LINE = QColor("#363642")
    TEXT = QColor("#F2EEE6")
    SOFT = QColor("#C9C5BD")
    MUTED = QColor("#8B8898")
    ACCENT = QColor("#FF6A3D")
    ON_ACCENT = QColor("#121218")  # text and icons on the accent
    KNOB = QColor("#F2EEE6")  # switch knobs
    CODE = QColor("#3DDC97")
    CODE_SOFT = QColor(61, 220, 151, 40)
    DIM = QColor(12, 12, 18, 150)  # screen shading (its alpha comes from the settings)


# name -> (label, INK, BASE, RAISED, HOVER, LINE, TEXT, SOFT, MUTED, ACCENT, ON_ACCENT, KNOB, CODE, DIM)
THEMES = {
    "ember": ("Ember", "#121218", "#1A1A22", "#24242E", "#2E2E3A", "#363642", "#F2EEE6", "#C9C5BD", "#8B8898",
              "#FF6A3D", "#121218", "#F2EEE6", "#3DDC97", "#0C0C12"),
    "graphite": ("Graphite", "#111214", "#18191C", "#222327", "#2C2D32", "#35363C", "#ECEDEF", "#C3C5CA",
                 "#868991", "#4C9EFF", "#0B0C0E", "#ECEDEF", "#3DDC97", "#0A0B0D"),
    "nord": ("Nord", "#242933", "#2E3440", "#3B4252", "#434C5E", "#4C566A", "#ECEFF4", "#D8DEE9", "#9099AB",
             "#88C0D0", "#242933", "#ECEFF4", "#A3BE8C", "#1C2028"),
    "dusk": ("Dusk", "#15121D", "#1C1826", "#262131", "#30293D", "#3A3249", "#F1ECF7", "#CBC3D6", "#8D8499",
             "#C08CFF", "#15121D", "#F1ECF7", "#5EE6B0", "#0E0B14"),
    "paper": ("Paper", "#FFFFFF", "#F5F4F0", "#EAE8E2", "#E0DDD5", "#D2CEC4", "#1D1C21", "#3E3C44", "#78747F",
              "#E4572E", "#FFFFFF", "#FFFFFF", "#0E9F6E", "#14141C"),
}

# Annotation outlines are part of the saved image: they never follow the theme.
OUTLINE_LIGHT = QColor("#F2EEE6")
OUTLINE_DARK = QColor("#121218")

# Screen recording: the record button's dot, the stop button and the frame
# around the area being recorded. The same red in every theme.
REC = QColor("#FF4747")


def use(name: str) -> None:
    """Switch the palette (unknown names fall back to Ember)."""
    _, *colors = THEMES.get(name, THEMES["ember"])
    for role, value in zip(("INK", "BASE", "RAISED", "HOVER", "LINE", "TEXT", "SOFT", "MUTED", "ACCENT",
                            "ON_ACCENT", "KNOB", "CODE", "DIM"), colors):
        setattr(C, role, QColor(value))
    C.CODE_SOFT = QColor(C.CODE)
    C.CODE_SOFT.setAlpha(40)
    C.DIM.setAlpha(150)


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
        QPalette.ColorRole.HighlightedText: C.ON_ACCENT,
        QPalette.ColorRole.ToolTipBase: C.BASE,
        QPalette.ColorRole.ToolTipText: C.TEXT,
        QPalette.ColorRole.PlaceholderText: C.MUTED,
    }.items():
        pal.setColor(role, color)
    app.setPalette(pal)
    app.setFont(font(13))
