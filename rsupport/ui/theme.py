"""Colours, icons (inline SVG, rendered crisply at any DPI) and the application stylesheet."""
import functools
import os
import tempfile

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

C = dict(
    bg="#f4f6fb", surface="#ffffff", border="#e3e8f0", text="#0f172a", muted="#64748b",
    primary="#2563eb", primary_hover="#1d4ed8", primary_soft="#eff4ff",
    sidebar="#0b1220", sidebar_hover="#152036", sidebar_active="#1c2b4a",
    green="#16a34a", amber="#d97706", red="#dc2626", slate="#475569",
)

# Feather-style 24x24 stroke icons
_ICONS = {
    "share": '<path d="M2 16.1A5 5 0 0 1 5.9 20M2 12.05A9 9 0 0 1 9.95 20M2 8V6a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-6"/><line x1="2" y1="20" x2="2.01" y2="20"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    "sliders": '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>',
    "copy": '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    "check": '<polyline points="20 6 9 17 4 12"/>',
    "refresh": '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
    "power": '<path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/>',
    "ok": '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>',
    "alert": '<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>',
    "eye": '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
    "eye-off": '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>',
    "monitor": '<rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>',
    "maximize": '<path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>',
    "x": '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    "lock": '<rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
}

_APP_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#3b82f6"/><stop offset="1" stop-color="#1e40af"/></linearGradient></defs>
<rect width="64" height="64" rx="15" fill="url(#g)"/>
<rect x="12" y="15" width="40" height="28" rx="4.5" fill="none" stroke="#fff" stroke-width="3.6"/>
<path d="M25 50h14M32 43v7" stroke="#fff" stroke-width="3.6" stroke-linecap="round"/>
<path d="M26.5 22.5v13l3.6-3.2 2.7 6 2.6-1.2-2.7-5.9 4.9-.4z" fill="#fff"/></svg>"""


def app_svg() -> str:
    return _APP_SVG


def _render(svg: str, size: int, dpr: int = 2) -> QPixmap:
    pm = QPixmap(size * dpr, size * dpr)
    pm.fill(Qt.transparent)
    pm.setDevicePixelRatio(dpr)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode())).render(p, QRectF(0, 0, size, size))
    p.end()
    return pm


def icon_pixmap(name: str, color: str = "#0f172a", size: int = 20, stroke: float = 2.0) -> QPixmap:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{_ICONS[name]}</svg>')
    return _render(svg, size)


def icon(name: str, color: str = "#0f172a", size: int = 20) -> QIcon:
    return QIcon(icon_pixmap(name, color, size))


def app_pixmap(size: int = 64) -> QPixmap:
    return _render(_APP_SVG, size)


def app_icon() -> QIcon:
    ic = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(_render(_APP_SVG, s, 1))
    return ic


FONT = '"Segoe UI Variable", "Segoe UI", "SF Pro Text", "Inter", "Helvetica Neue", "Noto Sans", "DejaVu Sans", sans-serif'
MONO = '"Cascadia Mono", "Consolas", "SF Mono", "Menlo", "DejaVu Sans Mono", monospace'

STYLE = f"""
* {{ font-family: {FONT}; font-size: 13px; color: {C['text']}; }}
QMainWindow, QWidget#root, QWidget#page {{ background: {C['bg']}; }}
QToolTip {{ background: #1e293b; color: white; border: none; padding: 5px 8px; }}

/* ---------- sidebar ---------- */
QFrame#sidebar {{ background: {C['sidebar']}; }}
QFrame#sidebar QLabel {{ color: #cbd5e1; }}
QLabel#brand {{ color: white; font-size: 16px; font-weight: 700; }}
QLabel#tagline {{ color: #7c8aa5; font-size: 11px; }}
QPushButton#nav {{ background: transparent; color: #aeb9cf; border: none; border-radius: 9px;
    padding: 11px 14px; text-align: left; font-size: 13.5px; font-weight: 500; }}
QPushButton#nav:hover {{ background: {C['sidebar_hover']}; color: white; }}
QPushButton#nav:checked {{ background: {C['sidebar_active']}; color: white; font-weight: 600; }}
QLabel#ver {{ color: #5d6b86; font-size: 11px; }}

/* ---------- typography ---------- */
QLabel#h1 {{ font-size: 24px; font-weight: 700; color: {C['text']}; }}
QLabel#h2 {{ font-size: 15px; font-weight: 650; color: {C['text']}; }}
QLabel#sub {{ color: {C['muted']}; font-size: 13px; }}
QLabel#cap {{ color: {C['muted']}; font-size: 11.5px; font-weight: 600; letter-spacing: 0.4px; }}
QLabel#hint {{ color: {C['muted']}; font-size: 12px; }}
QFrame#card QLabel#step {{ background: {C['primary_soft']}; color: {C['primary']}; font-weight: 700; border-radius: 12px; }}
QLabel#recentId {{ font-weight: 600; font-size: 13.5px; }}

/* ---------- cards ---------- */
QFrame#card {{ background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 14px; }}
QFrame#card QLabel {{ background: transparent; }}
QFrame#divider {{ background: {C['border']}; max-height: 1px; min-height: 1px; border: none; }}

/* ---------- state card ---------- */
QFrame#state {{ border-radius: 14px; border: 1px solid; }}
QFrame#state QLabel {{ background: transparent; }}
QFrame#state[kind="ok"]     {{ background: #ecfdf5; border-color: #a7f3d0; }}
QFrame#state[kind="warn"]   {{ background: #fffbeb; border-color: #fde68a; }}
QFrame#state[kind="danger"] {{ background: #fef2f2; border-color: #fecaca; }}
QFrame#state[kind="idle"]   {{ background: #f1f5f9; border-color: #e2e8f0; }}
QLabel#stateTitle {{ font-size: 15px; font-weight: 650; }}
QFrame#state[kind="ok"] QLabel#stateTitle     {{ color: #047857; }}
QFrame#state[kind="warn"] QLabel#stateTitle   {{ color: #b45309; }}
QFrame#state[kind="danger"] QLabel#stateTitle {{ color: #b91c1c; }}
QFrame#state[kind="idle"] QLabel#stateTitle   {{ color: #334155; }}
QLabel#stateDetail {{ color: #475569; }}

/* ---------- inputs ---------- */
QLineEdit, QComboBox, QSpinBox {{ padding: 9px 11px; border: 1px solid #d3dae6; border-radius: 9px;
    background: #fbfcfe; selection-background-color: {C['primary']}; min-height: 20px; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 1.5px solid {C['primary']}; background: white; }}
QLineEdit[readOnly="true"] {{ background: #f3f5f9; }}
QLineEdit#big {{ font-family: {MONO}; font-size: 19px; font-weight: 600; letter-spacing: 1.2px; padding: 8px 12px; min-height: 28px; }}
QComboBox#addr {{ font-family: {MONO}; font-size: 14px; font-weight: 600; background: #f3f5f9; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url(@ASSETS@/chev-down.png); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{ background: white; border: 1px solid #d3dae6; selection-background-color: {C['primary_soft']};
    selection-color: {C['text']}; outline: none; padding: 4px; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 20px; border: none; background: transparent; }}
QSpinBox::up-arrow {{ image: url(@ASSETS@/chev-up.png); width: 12px; height: 12px; }}
QSpinBox::down-arrow {{ image: url(@ASSETS@/chev-down.png); width: 12px; height: 12px; }}

/* ---------- buttons ---------- */
QPushButton {{ padding: 9px 16px; border-radius: 9px; border: 1px solid #d3dae6; background: white; font-weight: 500; }}
QPushButton:hover {{ background: #f1f5f9; }}
QPushButton:pressed {{ background: #e5eaf2; }}
QPushButton:disabled {{ color: #a3adbf; background: #f4f6fa; }}
QPushButton#primary {{ background: {C['primary']}; color: white; border: none; font-weight: 600; padding: 11px 18px; }}
QPushButton#primary:hover {{ background: {C['primary_hover']}; }}
QPushButton#primary:disabled {{ background: #a9c0f5; color: #eef3ff; }}
QPushButton#danger {{ background: {C['red']}; color: white; border: none; font-weight: 600; }}
QPushButton#danger:hover {{ background: #b91c1c; }}
QPushButton#link {{ border: none; background: transparent; color: {C['primary']}; padding: 4px 6px; font-weight: 600; }}
QPushButton#link:hover {{ background: {C['primary_soft']}; }}
QToolButton#copy {{ border: 1px solid #d3dae6; border-radius: 9px; background: white; padding: 8px; }}
QToolButton#copy:hover {{ background: #f1f5f9; }}

/* ---------- list ---------- */
QListWidget {{ border: none; background: transparent; outline: none; }}
QListWidget::item {{ padding: 10px 12px; border-radius: 9px; margin: 2px 0; }}
QListWidget::item:hover {{ background: #f1f5f9; }}
QListWidget::item:selected {{ background: {C['primary_soft']}; color: {C['text']}; }}

/* ---------- scrollbars ---------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #cbd5e1; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}

/* ---------- always-on-top banner + remote window ---------- */
QFrame#banner {{ background: {C['red']}; border-radius: 12px; }}
QFrame#banner QLabel {{ color: white; font-weight: 600; background: transparent; }}
QFrame#banner QPushButton {{ background: white; color: {C['red']}; border: none; font-weight: 700; padding: 6px 14px; }}
QToolBar#remoteBar {{ background: {C['sidebar']}; border: none; padding: 6px 10px; spacing: 8px; }}
QToolBar#remoteBar QLabel {{ color: #cbd5e1; }}
QToolBar#remoteBar QPushButton {{ background: #1e293b; color: white; border: none; padding: 7px 14px; }}
QToolBar#remoteBar QPushButton:hover {{ background: #334155; }}
QToolBar#remoteBar QPushButton#danger {{ background: {C['red']}; }}
QToolBar#remoteBar QPushButton#danger:hover {{ background: #b91c1c; }}
"""


@functools.lru_cache(maxsize=1)
def style() -> str:
    """The stylesheet with small arrow images generated on the fly (QSS can only load files)."""
    d = os.path.join(tempfile.gettempdir(), "remotesupport-assets")
    os.makedirs(d, exist_ok=True)
    for name, pts in (("down", "6 9 12 15 18 9"), ("up", "6 15 12 9 18 15")):
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#64748b" '
               f'stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="{pts}"/></svg>')
        _render(svg, 14, 2).save(os.path.join(d, f"chev-{name}.png"))
    return STYLE.replace("@ASSETS@", d.replace("\\", "/"))
