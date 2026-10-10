"""Beautify: a capture on a background, with padding, rounded corners and a
soft shadow, for docs, slides and posts. Used by the annotation editor's
Background panel (with a live preview) and as an after-capture step
(the last style used, without any window).

Sizes in a Style are logical pixels; ``scale`` (pixels per logical one)
makes them as big on a HiDPI capture as they look on screen. The result is
at the picture's full resolution."""

from dataclasses import asdict, dataclass, fields

from flatshot.qt import (
    QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPointF, QRectF, QSize, Qt,
)

BACKGROUNDS = ("none", "color", "edge", "gradient", "blur", "image")
BACKGROUND_LABELS = {"none": "Clear", "color": "Colour", "edge": "Edge colour", "gradient": "Gradient",
                     "blur": "Blurred copy", "image": "Image"}
RATIOS = {"auto": None, "1:1": 1.0, "4:3": 4 / 3, "16:9": 16 / 9}
# Gradients from the themes' own colours: (from, to), top left to bottom right.
GRADIENTS = {
    "ember": ("#FF6A3D", "#FFC53D"),
    "graphite": ("#4C9EFF", "#3DDC97"),
    "nord": ("#88C0D0", "#5E81AC"),
    "dusk": ("#C08CFF", "#FF6A9E"),
    "paper": ("#F5F4F0", "#D2CEC4"),
    "night": ("#24242E", "#121218"),
}


@dataclass
class Style:
    background: str = "gradient"  # see BACKGROUNDS ("edge": the picture's own edge colour, as Gradia's auto-balance)
    color: str = "#1A1A22"  # for "color"
    gradient: str = "ember"  # see GRADIENTS
    image: str = ""  # a picture file, for "image"
    padding: int = 64  # around the picture: logical px, or % of its longer side with padding_percent
    padding_percent: bool = False
    radius: int = 12  # its corners
    shadow: int = 24  # how soft (0: none)
    shadow_offset: int = 10  # downwards
    shadow_opacity: int = 45  # %
    ratio: str = "auto"  # the whole: auto, 1:1, 4:3 or 16:9 (grown to it, never cut)

    @classmethod
    def from_dict(cls, data) -> "Style":
        known = {f.name for f in fields(cls)}
        style = cls(**{k: v for k, v in (data or {}).items() if k in known}) if isinstance(data, dict) else cls()
        style.clean()
        return style

    def to_dict(self) -> dict:
        return asdict(self)

    def clean(self):
        if self.background not in BACKGROUNDS:
            self.background = "gradient"
        if self.gradient not in GRADIENTS:
            self.gradient = "ember"
        if self.ratio not in RATIOS:
            self.ratio = "auto"
        if not QColor(str(self.color)).isValid():
            self.color = "#1A1A22"
        self.padding = max(0, min(int(self.padding), 400))
        self.radius = max(0, min(int(self.radius), 64))
        self.shadow = max(0, min(int(self.shadow), 96))
        self.shadow_offset = max(0, min(int(self.shadow_offset), 64))
        self.shadow_opacity = max(0, min(int(self.shadow_opacity), 100))


def has_own_shape(image: QImage) -> bool:
    """Already cut out with transparency round it (a window KWin took on
    its own, with its shadow and rounded corners): no second shadow or
    corners are added."""
    if not image.hasAlphaChannel() or image.width() < 2 or image.height() < 2:
        return False
    w, h = image.width() - 1, image.height() - 1
    return all(image.pixelColor(x, y).alpha() < 8 for x, y in ((0, 0), (w, 0), (0, h), (w, h)))


def layout(size: QSize, style: Style, scale: float = 1.0) -> tuple[QSize, QPointF]:
    """The whole picture's size, and where the capture goes in it (pixels)."""
    w, h = size.width(), size.height()
    pad = round(max(w, h) * style.padding / 100) if style.padding_percent else round(style.padding * scale)
    out_w, out_h = w + 2 * pad, h + 2 * pad
    ratio = RATIOS.get(style.ratio)
    if ratio:
        if out_w / out_h < ratio:
            out_w = round(out_h * ratio)
        else:
            out_h = round(out_w / ratio)
    return QSize(out_w, out_h), QPointF((out_w - w) // 2, (out_h - h) // 2)  # (on whole pixels: not resampled)


def _soft(image: QImage, k: float) -> QImage:
    """``image`` blurred: shrunk ``k`` times and grown back smoothly, twice
    (as the Blur drawing does, quick even when big)."""
    if k <= 1:
        return image
    smooth = Qt.TransformationMode.SmoothTransformation
    ignore = Qt.AspectRatioMode.IgnoreAspectRatio
    w, h = image.width(), image.height()
    small = image.scaled(max(1, round(w / k)), max(1, round(h / k)), ignore, smooth)
    smaller = small.scaled(max(1, small.width() // 2), max(1, small.height() // 2), ignore, smooth)
    return smaller.scaled(small.width(), small.height(), ignore, smooth).scaled(w, h, ignore, smooth)


def edge_color(image: QImage) -> QColor:
    """The colour most of the picture's border has (to pad with, so the
    margin looks like more of the picture)."""
    counts: dict = {}
    w, h = image.width(), image.height()
    step = max(1, (w + h) // 400)
    points = [(x, y) for x in range(0, w, step) for y in (0, h - 1)] + \
             [(x, y) for y in range(0, h, step) for x in (0, w - 1)]
    for x, y in points:
        c = image.pixelColor(x, y)
        key = (c.red() // 8, c.green() // 8, c.blue() // 8)
        counts[key] = counts.get(key, 0) + 1
    r, g, b = max(counts, key=counts.get) if counts else (0, 0, 0)
    return QColor(min(255, r * 8 + 4), min(255, g * 8 + 4), min(255, b * 8 + 4))


def _cover(picture: QImage, size: QSize) -> QImage:
    """``picture`` grown or shrunk to cover ``size``, its middle kept."""
    scaled = picture.scaled(size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                            Qt.TransformationMode.SmoothTransformation)
    return scaled.copy((scaled.width() - size.width()) // 2, (scaled.height() - size.height()) // 2,
                       size.width(), size.height())


def backdrop(image: QImage, style: Style, scale: float = 1.0) -> tuple[QImage, QPointF]:
    """Everything but the capture itself: the background and the shadow,
    the whole size (see layout), and where the capture goes on it."""
    size, at = layout(image.size(), style, scale)
    out = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    whole = QRectF(0, 0, size.width(), size.height())
    kind = style.background
    if kind == "color":
        p.fillRect(whole, QColor(style.color))
    elif kind == "edge":
        p.fillRect(whole, edge_color(image))
    elif kind == "gradient":
        a, b = GRADIENTS.get(style.gradient, GRADIENTS["ember"])
        g = QLinearGradient(whole.topLeft(), whole.bottomRight())
        g.setColorAt(0, QColor(a))
        g.setColorAt(1, QColor(b))
        p.fillRect(whole, g)
    elif kind == "blur":
        soft = _soft(_cover(image.convertToFormat(QImage.Format.Format_RGB32), size),
                     max(4.0, max(size.width(), size.height()) / 60))
        p.drawImage(0, 0, soft)
        p.fillRect(whole, QColor(0, 0, 0, 40))  # (a little darker, so the capture stands out)
    elif kind == "image":
        picture = QImage(style.image) if style.image else QImage()
        if picture.isNull():  # (gone, or unreadable: a gradient instead)
            a, b = GRADIENTS["night"]
            g = QLinearGradient(whole.topLeft(), whole.bottomRight())
            g.setColorAt(0, QColor(a))
            g.setColorAt(1, QColor(b))
            p.fillRect(whole, g)
        else:
            p.drawImage(0, 0, _cover(picture, size))
    if style.shadow and style.shadow_opacity and not has_own_shape(image):
        blur = style.shadow * scale
        margin = round(blur * 2)
        mask = QImage(image.width() + 2 * margin, image.height() + 2 * margin,
                      QImage.Format.Format_ARGB32_Premultiplied)
        mask.fill(Qt.GlobalColor.transparent)
        m = QPainter(mask)
        m.setRenderHint(QPainter.RenderHint.Antialiasing)
        m.setPen(Qt.PenStyle.NoPen)
        m.setBrush(QColor(0, 0, 0, round(255 * style.shadow_opacity / 100)))
        radius = style.radius * scale
        m.drawRoundedRect(QRectF(margin, margin, image.width(), image.height()), radius, radius)
        m.end()
        shadow = _soft(mask, max(1.0, blur / 3))
        p.drawImage(QPointF(at.x() - margin, at.y() - margin + style.shadow_offset * scale), shadow)
    p.end()
    return out, at


def corners(style: Style, image: QImage, scale: float = 1.0) -> float:
    """The capture's corner radius in pixels (none for one already shaped)."""
    return 0.0 if has_own_shape(image) else style.radius * scale


def render(image: QImage, style: Style, scale: float = 1.0) -> QImage:
    """The capture beautified, at full resolution."""
    out, at = backdrop(image, style, scale)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    radius = corners(style, image, scale)
    if radius > 0:
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(at, QPointF(at.x() + image.width(), at.y() + image.height())), radius, radius)
        p.setClipPath(clip)
    p.drawImage(at, image)
    p.end()
    if style.background != "none" and style.background != "image":
        return out.convertToFormat(QImage.Format.Format_RGB32)  # (opaque all over)
    return out
