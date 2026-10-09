"""Pin a capture to the screen (as in Flameshot and ShareX): a borderless
window that stays above the others. Drag to move, scroll to zoom,
double-click or Esc to close, right-click to copy or save."""

from itertools import count

from flatshot import config, output, windows
from flatshot.qt import (
    QColor, QCursor, QGuiApplication, QImage, QKeySequence, QMenu, QPainter, QPen, QPixmap, QPoint, QRect, QRectF,
    QSize, QSizeF, Qt, QTimer, QWidget, keyval,
)
from flatshot.theme import C

_open: list["PinWindow"] = []


def screen_layout() -> tuple:
    """The monitors' names, places and scales: a pin may open where its
    capture was only while these are as they were then."""
    return tuple((s.name(), s.geometry().getRect(), round(s.devicePixelRatio(), 3))
                 for s in QGuiApplication.screens())
_ids = count(1)
_on_all_closed: list = []


def open_count() -> int:
    return len(_open)


def when_all_closed(callback) -> None:
    _on_all_closed.append(callback)


def show(image: QImage, at: QRect | None = None, size: QSize | None = None) -> "PinWindow":
    """Pin ``image``. ``at`` is where it was captured (global logical coords);
    the pin opens right there when the desktop allows placing windows, at
    that size. ``size``: the size it was captured at, when where can't be
    used (the monitors changed since)."""
    pin = PinWindow(image, at, size)
    pin.show()
    _open.append(pin)
    return pin


class PinWindow(QWidget):
    def __init__(self, image: QImage, at: QRect | None, size: QSize | None = None):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.image = image
        self.at = at
        self.pixmap = QPixmap.fromImage(image)
        self.setWindowTitle(f"Flatshot pin {next(_ids)}")
        screen = (QGuiApplication.screenAt(at.center()) if at else None) \
            or QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        # Its natural (logical) size: that of the area captured. Its pixels
        # are no guide: KWin takes the whole desktop at the highest scale of
        # any monitor, so they needn't match this monitor's scale.
        if at is not None or size is not None:
            self.base = QSizeF(at.size() if at is not None else size)
        else:
            dpr = screen.devicePixelRatio()
            self.base = QSizeF(image.width() / dpr, image.height() / dpr)
        avail = screen.availableGeometry()
        self.zoom = min(1.0, avail.width() * 0.9 / max(1, self.base.width()),
                        avail.height() * 0.9 / max(1, self.base.height()))
        self.resize(self._scaled())
        if at is not None:
            self.move(at.topLeft())
        else:
            self.move(avail.center() - QRect(QPoint(), self.size()).center())
        self._drag: QPoint | None = None
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip("Drag to move · scroll to zoom · double-click or Esc to close · right-click for more")

    def _scaled(self) -> QSize:
        size = (self.base * self.zoom).toSize()
        return QSize(max(16, size.width()), max(16, size.height()))

    def showEvent(self, event):
        # Wayland has no "keep above" or placement for normal windows; KWin can do both.
        place = QRect(self.at.topLeft(), self.size()) if self.at is not None else None
        title = self.windowTitle()
        QTimer.singleShot(50, lambda: windows.keep_above(title, place))

    # -- input -------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.windowHandle()
            if not (handle and handle.startSystemMove()):
                self._drag = event.globalPosition().toPoint() - self.pos()
        elif event.button() == Qt.MouseButton.MiddleButton:
            self.close()

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event):
        self._drag = None

    def mouseDoubleClickEvent(self, event):
        self.close()

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120
        if not steps:
            return
        self.zoom = max(0.1, min(self.zoom * 1.15 ** steps, 5.0))
        self.resize(self._scaled())
        self.update()

    def keyPressEvent(self, event):
        if keyval(event.key()) == keyval(Qt.Key.Key_Escape):
            self._escape_down = True  # closes on release, which would otherwise go to the window below
        elif event.matches(QKeySequence.StandardKey.Copy):
            self.copy()
        elif event.matches(QKeySequence.StandardKey.Save):
            self.save()

    def keyReleaseEvent(self, event):
        if keyval(event.key()) == keyval(Qt.Key.Key_Escape) and not event.isAutoRepeat() \
                and getattr(self, "_escape_down", False):
            self.close()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("Copy").triggered.connect(self.copy)
        menu.addAction("Save to screenshots folder").triggered.connect(self.save)
        menu.addAction("Actual size").triggered.connect(self.actual_size)
        menu.addSeparator()
        menu.addAction("Close").triggered.connect(self.close)
        menu.exec(event.globalPos())

    # -- actions -----------------------------------------------------------

    def copy(self):
        output.copy_image(self.image)

    def save(self):
        try:
            path = output.save(self.image, config.load(), shot=output.Shot(mode="region"))
            print(path, flush=True)
        except OSError as e:
            print(f"flatshot: {e}", flush=True)

    def actual_size(self):
        self.zoom = 1.0
        self.resize(self._scaled())

    # -- painting ----------------------------------------------------------

    def paintEvent(self, event):
        p = QPainter(self)
        # Smooth at any size: the picture's pixels seldom match this screen's.
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(QRectF(self.rect()), self.pixmap, QRectF(self.pixmap.rect()))
        edge = QColor(C.ACCENT)
        edge.setAlpha(170)
        p.setPen(QPen(edge, 1))
        p.drawRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))

    def closeEvent(self, event):
        super().closeEvent(event)
        if self in _open:
            _open.remove(self)
        if not _open:
            callbacks = list(_on_all_closed)
            _on_all_closed.clear()
            for callback in callbacks:
                callback()
