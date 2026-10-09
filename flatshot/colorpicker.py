"""The picker for your own colour, under the toolbar's eighth swatch. Loaded
only when it's first opened, so it costs screenshots nothing."""

from flatshot.qt import (
    QColor, QImage, QLinearGradient, QLineEdit, QPainter, QPainterPath, QPen, QPointF, QRect, QRectF, Qt, QWidget,
)
from flatshot.theme import C, font
from flatshot.widgets import IconButton


class ColorPicker(QWidget):
    """A square of shades (saturation across, brightness down), a hue bar,
    the colour as hex to read or type, and an eyedropper for a colour on the
    screen. Every change shows on the toolbar and in what's drawn next."""

    SHADES = QRect(10, 10, 196, 124)
    HUES = QRect(10, 144, 196, 12)

    def __init__(self, ctl, parent):
        super().__init__(parent)
        self.ctl = ctl
        self.setFixedSize(216, 202)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.h, self.s, self.v = 0.0, 1.0, 1.0
        self._dragging: str | None = None  # "shades" or "hues"
        self._shades: tuple[float, QImage] | None = None  # (hue, picture) for the square
        self.hex = QLineEdit(self)
        self.hex.setGeometry(10, 166, 158, 28)
        self.hex.setMaxLength(7)
        self.hex.setFont(font(13, mono=True))
        self.hex.setStyleSheet(
            f"QLineEdit {{ background: {C.INK.name()}; color: {C.TEXT.name()}; border: 1px solid {C.LINE.name()};"
            f" border-radius: 6px; padding: 0 8px; selection-background-color: {C.HOVER.name()}; }}"
            f"QLineEdit:focus {{ border-color: {C.MUTED.name()}; }}")
        self.hex.textEdited.connect(self._typed)
        self.hex.returnPressed.connect(ctl.close_picker)
        self.dropper = IconButton(ctl, "eyedropper", "Take a colour from the screen  ·  then click a pixel",
                                  self, size=28)
        self.dropper.move(178, 166)
        self.dropper.clicked.connect(ctl.start_eyedropper)

    def set_color(self, color: QColor):
        h, s, v, _ = color.getHsvF()
        if h >= 0:  # (greys have no hue: keep the one there was)
            self.h = h
        self.s, self.v = s, v
        self.hex.setText(color.name().upper())
        self.update()

    def color(self) -> QColor:
        return QColor.fromHsvF(self.h, self.s, self.v)

    def _changed(self):
        color = self.color()
        self.hex.setText(color.name().upper())
        self.ctl.set_custom_color(color)
        self.update()

    def _typed(self, text: str):
        text = text.strip()
        if not text.startswith("#"):
            text = "#" + text
        color = QColor(text)
        if len(text) == 7 and color.isValid():
            h, s, v, _ = color.getHsvF()
            self.h = h if h >= 0 else self.h
            self.s, self.v = s, v
            self.ctl.set_custom_color(color)
            self.update()

    def keyPressEvent(self, event):
        # Keys the hex field doesn't use stop here (Enter would capture the
        # screen), except Esc, which closes the picker.
        if event.key() == Qt.Key.Key_Escape:
            event.ignore()
        else:
            event.accept()

    # -- mouse ---------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        if QRectF(self.SHADES).adjusted(-4, -4, 4, 4).contains(pos):
            self._dragging = "shades"
        elif QRectF(self.HUES).adjusted(-4, -6, 4, 6).contains(pos):
            self._dragging = "hues"
        self._drag_to(pos)

    def mouseMoveEvent(self, event):
        self._drag_to(event.position())

    def mouseReleaseEvent(self, event):
        self._dragging = None

    def _drag_to(self, pos: QPointF):
        def along(lo: float, length: float, at: float) -> float:
            return min(max((at - lo) / max(1.0, length), 0.0), 1.0)

        if self._dragging == "shades":
            r = self.SHADES
            self.s = along(r.left(), r.width(), pos.x())
            self.v = 1.0 - along(r.top(), r.height(), pos.y())
        elif self._dragging == "hues":
            r = self.HUES
            self.h = min(along(r.left(), r.width(), pos.x()), 0.9999)
        else:
            return
        self._changed()

    # -- painting ------------------------------------------------------------

    def _shades_image(self) -> QImage:
        if self._shades is None or self._shades[0] != self.h:
            r = self.SHADES
            img = QImage(r.width(), r.height(), QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(QColor.fromHsvF(self.h, 1.0, 1.0))
            p = QPainter(img)
            across = QLinearGradient(0, 0, r.width(), 0)
            across.setColorAt(0, QColor(255, 255, 255))
            across.setColorAt(1, QColor(255, 255, 255, 0))
            p.fillRect(img.rect(), across)
            down = QLinearGradient(0, 0, 0, r.height())
            down.setColorAt(0, QColor(0, 0, 0, 0))
            down.setColorAt(1, QColor(0, 0, 0))
            p.fillRect(img.rect(), down)
            p.end()
            self._shades = (self.h, img)
        return self._shades[1]

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(C.LINE, 1))
        p.setBrush(C.BASE)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)

        shades = QRectF(self.SHADES)
        clip = QPainterPath()
        clip.addRoundedRect(shades, 6, 6)
        p.save()
        p.setClipPath(clip)
        p.drawImage(shades.topLeft(), self._shades_image())
        p.restore()

        hues = QRectF(self.HUES)
        bar = QLinearGradient(hues.left(), 0, hues.right(), 0)
        for i in range(7):
            bar.setColorAt(i / 6, QColor.fromHsvF((i / 6) % 1.0, 1.0, 1.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bar)
        p.drawRoundedRect(hues, 6, 6)

        # Markers: a white ring with a dark edge shows on any colour.
        at = QPointF(shades.left() + self.s * shades.width(), shades.top() + (1 - self.v) * shades.height())
        self._ring(p, at, 6, self.color())
        self._ring(p, QPointF(hues.left() + self.h * hues.width(), hues.center().y()), 7,
                   QColor.fromHsvF(self.h, 1.0, 1.0))

    @staticmethod
    def _ring(p: QPainter, at: QPointF, r: float, fill: QColor):
        p.setBrush(fill)
        p.setPen(QPen(QColor(0, 0, 0, 110), 4))
        p.drawEllipse(at, r, r)
        p.setPen(QPen(QColor("#FFFFFF"), 2))
        p.drawEllipse(at, r, r)
