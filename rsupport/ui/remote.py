"""The technician's live view of the other computer, plus the client-side warning banner."""
import time

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton, QSizePolicy,
                               QToolBar, QVBoxLayout, QWidget)

from .theme import app_icon, icon, style

_SPECIAL_KEYS = {
    Qt.Key_Return: "Return", Qt.Key_Enter: "Return", Qt.Key_Backspace: "BackSpace",
    Qt.Key_Tab: "Tab", Qt.Key_Escape: "Escape", Qt.Key_Delete: "Delete", Qt.Key_Insert: "Insert",
    Qt.Key_Home: "Home", Qt.Key_End: "End", Qt.Key_PageUp: "Prior", Qt.Key_PageDown: "Next",
    Qt.Key_Up: "Up", Qt.Key_Down: "Down", Qt.Key_Left: "Left", Qt.Key_Right: "Right",
    Qt.Key_Space: "space", Qt.Key_CapsLock: "Caps_Lock", Qt.Key_Menu: "Menu",
    Qt.Key_Shift: "Shift_L", Qt.Key_Control: "Control_L", Qt.Key_Alt: "Alt_L",
    Qt.Key_Meta: "Super_L", Qt.Key_NumLock: "Num_Lock", Qt.Key_Print: "Print",
}


def key_name(key: int, text: str):
    """Map a Qt key event to the key names the host understands (see inputctl)."""
    k = _SPECIAL_KEYS.get(key)
    if k:
        return k
    if int(Qt.Key_F1) <= key <= int(Qt.Key_F12):
        return f"F{key - int(Qt.Key_F1) + 1}"
    if text and len(text) == 1 and ord(text) >= 32 and ord(text) != 127:
        return text
    if 0x20 <= key <= 0x7E:                  # Ctrl+letter etc.: text is a control char, use the key
        ch = chr(key)
        return ch.lower() if ch.isalpha() else ch
    return None


class RemoteView(QWidget):
    inputEvent = Signal(dict)

    def __init__(self):
        super().__init__()
        self.fb = None
        self._last_move = 0.0
        self._wheel = [0, 0]
        self._down = set()
        self.frames = 0
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setMinimumSize(480, 300)

    def apply_frame(self, sw, sh, x, y, w, h, jpeg):
        img = QImage.fromData(jpeg)
        if img.isNull():
            return
        if self.fb is None or self.fb.width() != sw or self.fb.height() != sh:
            self.fb = QImage(sw, sh, QImage.Format_RGB32)
            self.fb.fill(Qt.black)
        p = QPainter(self.fb)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.drawImage(x, y, img)
        p.end()
        self.frames += 1
        self.update()

    def target_rect(self):
        if self.fb is None:
            return None
        s = min(self.width() / self.fb.width(), self.height() / self.fb.height())
        w, h = max(1, int(self.fb.width() * s)), max(1, int(self.fb.height() * s))
        return QRect((self.width() - w) // 2, (self.height() - h) // 2, w, h)

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0b1220"))
        r = self.target_rect()
        if r is None:
            p.setPen(QColor("#64748b"))
            f = QFont(self.font()); f.setPointSize(13); p.setFont(f)
            p.drawText(self.rect(), Qt.AlignCenter, "Waiting for the remote screen...")
            return
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage(r, self.fb)

    def _norm(self, pos):
        r = self.target_rect()
        if r is None:
            return 0.0, 0.0, False
        x, y = (pos.x() - r.x()) / r.width(), (pos.y() - r.y()) / r.height()
        return min(1.0, max(0.0, x)), min(1.0, max(0.0, y)), (0 <= x <= 1 and 0 <= y <= 1)

    # ---- mouse -------------------------------------------------------------
    def mouseMoveEvent(self, e):
        now = time.monotonic()
        if now - self._last_move < 0.016:
            return
        self._last_move = now
        x, y, inside = self._norm(e.position())
        if inside:
            self.inputEvent.emit({"t": "move", "x": x, "y": y})

    def _button(self, e, down):
        name = {Qt.LeftButton: "left", Qt.RightButton: "right", Qt.MiddleButton: "middle"}.get(e.button())
        if name:
            x, y, _ = self._norm(e.position())
            self.inputEvent.emit({"t": "btn", "b": name, "d": down, "x": x, "y": y})

    def mousePressEvent(self, e):
        self.setFocus()
        self._button(e, True)

    def mouseReleaseEvent(self, e):
        self._button(e, False)

    def wheelEvent(self, e):
        d = e.angleDelta()
        self._wheel[0] += d.x()
        self._wheel[1] += d.y()
        dx, dy = int(self._wheel[0] / 120), int(self._wheel[1] / 120)
        if dx or dy:
            self._wheel[0] -= dx * 120
            self._wheel[1] -= dy * 120
            self.inputEvent.emit({"t": "scroll", "dx": dx, "dy": dy})

    # ---- keyboard ------------------------------------------------------------
    def event(self, e):
        if e.type() == QEvent.ShortcutOverride:      # let Ctrl+C etc. reach the remote PC
            e.accept()
            return True
        return super().event(e)

    def focusNextPrevChild(self, _next):             # Tab belongs to the remote PC
        return False

    def keyPressEvent(self, e):
        name = key_name(e.key(), e.text())
        if name:
            self._down.add(name)
            self.inputEvent.emit({"t": "key", "k": name, "d": True})

    def keyReleaseEvent(self, e):
        if e.isAutoRepeat():
            return
        name = key_name(e.key(), e.text())
        if name:
            self._down.discard(name)
            self.inputEvent.emit({"t": "key", "k": name, "d": False})

    def release_all_keys(self):
        for name in list(self._down):
            self.inputEvent.emit({"t": "key", "k": name, "d": False})
        self._down.clear()

    def focusOutEvent(self, e):                      # never leave Ctrl/Alt stuck on the remote PC
        self.release_all_keys()
        super().focusOutEvent(e)


class RemoteWindow(QMainWindow):
    closed = Signal()

    def __init__(self, title):
        super().__init__()
        self.setWindowTitle(f"RemoteSupport - {title}")
        self.setWindowIcon(app_icon())
        self.setStyleSheet(style())
        self.view = RemoteView()
        self.setCentralWidget(self.view)

        bar = QToolBar()
        bar.setObjectName("remoteBar")
        bar.setMovable(False)
        bar.setFloatable(False)
        self.addToolBar(Qt.TopToolBarArea, bar)
        self.info = QLabel(f"<span style='color:#22c55e'>●</span>&nbsp; Connected to <b>{title}</b>")
        self.info.setTextFormat(Qt.RichText)
        bar.addWidget(self.info)
        spacer = QWidget(); spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(spacer)
        self.stats = QLabel("")
        bar.addWidget(self.stats)
        self.fs_btn = QPushButton("Full screen")
        self.fs_btn.setIcon(icon("maximize", "#ffffff", 16))
        self.fs_btn.clicked.connect(self.toggle_fullscreen)
        bar.addWidget(self.fs_btn)
        self.dis_btn = QPushButton("Disconnect")
        self.dis_btn.setObjectName("danger")
        self.dis_btn.setIcon(icon("x", "#ffffff", 16))
        self.dis_btn.clicked.connect(self.close)
        bar.addWidget(self.dis_btn)
        QShortcut(QKeySequence("F11"), self, activated=self.toggle_fullscreen)

        self._last_count = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self.resize(1180, 760)

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def _tick(self):
        n = self.view.frames
        fps, self._last_count = n - self._last_count, n
        fb = self.view.fb
        size = f"{fb.width()}×{fb.height()}  ·  " if fb is not None else ""
        self.stats.setText(f"{size}{fps} fps")

    def closeEvent(self, e):
        self._timer.stop()
        self.view.release_all_keys()
        self.closed.emit()
        super().closeEvent(e)


class ControlBanner(QWidget):
    """Always-on-top red strip on the shared computer while a technician is connected."""

    def __init__(self, on_disconnect):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        f = QFrame(self)
        f.setObjectName("banner")
        lay = QHBoxLayout(f)
        lay.setContentsMargins(16, 8, 10, 8)
        lay.setSpacing(14)
        lay.addWidget(QLabel("●  A technician is connected to this computer"))
        b = QPushButton("Disconnect")
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(on_disconnect)
        lay.addWidget(b)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(f)
        self.setStyleSheet(style())
        self.setAttribute(Qt.WA_TranslucentBackground)

    def pop(self):
        self.adjustSize()
        scr = QGuiApplication.primaryScreen()
        if scr:
            g = scr.availableGeometry()
            self.move(g.x() + (g.width() - self.width()) // 2, g.y() + 8)
        self.show()
        self.raise_()
