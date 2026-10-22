"""Small reusable widgets: Card, ToggleSwitch, CopyField, NavButton, StatusPill, StateCard."""
from PySide6.QtCore import (Property, QEasingCurve, QPropertyAnimation, QSize, Qt, QTimer, Signal)
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel, QPushButton,
                               QToolButton, QVBoxLayout, QWidget)

from .theme import C, icon, icon_pixmap


def repolish(w: QWidget):
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def label(text, name=None, wrap=False) -> QLabel:
    l = QLabel(text)
    if name:
        l.setObjectName(name)
    l.setWordWrap(wrap)
    return l


class Card(QFrame):
    def __init__(self, title=None, subtitle=None):
        super().__init__()
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(22, 20, 22, 20)
        self.body.setSpacing(10)
        if title:
            self.body.addWidget(label(title, "h2"))
        if subtitle:
            self.body.addWidget(label(subtitle, "sub", True))


def divider() -> QFrame:
    f = QFrame()
    f.setObjectName("divider")
    return f


class ToggleSwitch(QAbstractButton):
    """iOS-style on/off switch."""

    def __init__(self, checked=False):
        super().__init__()
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(46, 26)
        self._off = 1.0 if checked else 0.0
        self.setChecked(checked)
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _get(self):
        return self._off

    def _set(self, v):
        self._off = v
        self.update()

    offset = Property(float, _get, _set)

    def _animate(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._off)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def sizeHint(self):
        return QSize(46, 26)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        off = QColor("#cbd5e1")
        on = QColor(C["green"])
        t = self._off
        track = QColor(int(off.red() + (on.red() - off.red()) * t),
                       int(off.green() + (on.green() - off.green()) * t),
                       int(off.blue() + (on.blue() - off.blue()) * t))
        if not self.isEnabled():
            track.setAlpha(110)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(self.rect(), 13, 13)
        p.setBrush(QColor("white"))
        x = 3 + t * (self.width() - 26)
        p.drawEllipse(int(x), 3, 20, 20)


class CopyField(QWidget):
    """Caption + read-only editor (line edit or combo) + copy button."""
    copied = Signal()

    def __init__(self, caption, editor: QWidget, getter):
        super().__init__()
        self._getter = getter
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        lay.addWidget(label(caption.upper(), "cap"))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.editor = editor
        row.addWidget(editor, 1)
        self.btn = QToolButton()
        self.btn.setObjectName("copy")
        self.btn.setIcon(icon("copy", C["slate"], 18))
        self.btn.setIconSize(QSize(18, 18))
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setToolTip("Copy")
        self.btn.clicked.connect(self.copy)
        row.addWidget(self.btn)
        lay.addLayout(row)

    def copy(self):
        QGuiApplication.clipboard().setText(self._getter() or "")
        self.btn.setIcon(icon("check", C["green"], 18))
        QTimer.singleShot(1200, lambda: self.btn.setIcon(icon("copy", C["slate"], 18)))
        self.copied.emit()


class NavButton(QPushButton):
    def __init__(self, text, icon_name):
        super().__init__("  " + text)
        self.setObjectName("nav")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setIconSize(QSize(20, 20))
        self._name = icon_name
        self._refresh()
        self.toggled.connect(lambda _: self._refresh())

    def _refresh(self):
        self.setIcon(icon(self._name, "#ffffff" if self.isChecked() else "#8fa0bf", 20))


_PILL = {  # kind: (background, foreground, dot)
    "ok": ("rgba(34,197,94,0.14)", "#4ade80", "#22c55e"),
    "warn": ("rgba(245,158,11,0.16)", "#fbbf24", "#f59e0b"),
    "danger": ("rgba(239,68,68,0.16)", "#f87171", "#ef4444"),
    "idle": ("rgba(148,163,184,0.14)", "#94a3b8", "#94a3b8"),
}


class StatusPill(QLabel):
    """Rounded status chip with a coloured dot (used on the dark sidebar)."""

    def __init__(self):
        super().__init__()
        self.setTextFormat(Qt.RichText)
        self.set_state("idle", "Starting...")

    def set_state(self, kind, text):
        bg, fg, dot = _PILL[kind]
        self.setStyleSheet(f"background:{bg}; color:{fg}; border-radius:12px; padding:6px 10px; font-weight:600; font-size:12px;")
        self.setText(f"<span style='color:{dot}'>●</span>&nbsp; {text}")


_STATE_ICON = {"ok": ("ok", "#059669"), "warn": ("alert", "#d97706"),
               "danger": ("eye", "#dc2626"), "idle": ("power", "#64748b")}


class StateCard(QFrame):
    """Big coloured banner describing what the shared computer is doing right now."""

    def __init__(self):
        super().__init__()
        self.setObjectName("state")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(14)
        self.icon_lbl = QLabel()
        self.icon_lbl.setFixedSize(34, 34)
        lay.addWidget(self.icon_lbl)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = label("", "stateTitle")
        self.detail = label("", "stateDetail", True)
        col.addWidget(self.title)
        col.addWidget(self.detail)
        lay.addLayout(col, 1)
        self.action = QPushButton("Disconnect")
        self.action.setObjectName("danger")
        self.action.setCursor(Qt.PointingHandCursor)
        lay.addWidget(self.action)
        self.set_state("idle", "Starting...", "")

    def set_state(self, kind, title, detail, show_action=False):
        self.setProperty("kind", kind)
        name, color = _STATE_ICON[kind]
        self.icon_lbl.setPixmap(icon_pixmap(name, color, 30, 1.8))
        self.title.setText(title)
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        self.action.setVisible(show_action)
        repolish(self)
        for w in (self.title,):
            repolish(w)
