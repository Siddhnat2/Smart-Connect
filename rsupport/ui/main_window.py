"""Main window: sidebar navigation + three pages (This computer / Connect / Settings).

Opening the app puts this computer in *client mode* (sharing is on, waiting for a technician).
Choosing "Connect" in the sidebar switches to *technician mode*.
"""
import json
import time

from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtNetwork import QAbstractSocket, QNetworkInterface
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
                               QMessageBox, QPushButton, QScrollArea, QSpinBox, QStackedWidget, QSystemTrayIcon,
                               QVBoxLayout, QWidget)

from .. import __version__
from ..config import gen_password, gen_username
from .controllers import (DEFAULT_PORT, AsyncRunner, HostController, ViewerController)
from .remote import ControlBanner, RemoteWindow
from .theme import C, app_icon, app_pixmap, icon, icon_pixmap, style
from .widgets import (Card, CopyField, NavButton, StateCard, StatusPill, ToggleSwitch, divider,
                      label, repolish)


def group_id(uid: str) -> str:
    uid = uid.replace(" ", "")
    return " ".join(uid[i:i + 3] for i in range(0, len(uid), 3))


def local_addresses(port):
    """[(ip:port, description)] - VPN (Tailscale 100.64/10) first, then LAN."""
    found = []
    for a in QNetworkInterface.allAddresses():
        if a.protocol() != QAbstractSocket.IPv4Protocol or a.isLoopback():
            continue
        ip = a.toString()
        if ip.startswith("169.254."):
            continue
        p = [int(x) for x in ip.split(".")]
        vpn = p[0] == 100 and 64 <= p[1] <= 127
        found.append((0 if vpn else 1, f"{ip}:{port}", "Tailscale / VPN" if vpn else "local network"))
    found.sort()
    return [(addr, desc) for _, addr, desc in found] or [(f"(this PC's IP):{port}", "")]


def _row(title, desc, control) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 6, 0, 6)
    col = QVBoxLayout()
    col.setSpacing(2)
    col.addWidget(label(title, "h2"))
    if desc:
        col.addWidget(label(desc, "hint", True))
    lay.addLayout(col, 1)
    lay.addWidget(control, 0, Qt.AlignVCenter)
    return w


def _page(title, subtitle):
    page = QWidget()
    page.setObjectName("page")
    lay = QVBoxLayout(page)
    lay.setContentsMargins(36, 32, 36, 28)
    lay.setSpacing(18)
    head = QVBoxLayout()
    head.setSpacing(4)
    head.addWidget(label(title, "h1"))
    head.addWidget(label(subtitle, "sub", True))
    lay.addLayout(head)
    return page, lay


class MainWindow(QMainWindow):
    def __init__(self, settings=None, agent_kwargs=None, port=None, autostart=None):
        super().__init__()
        self.setWindowTitle("RemoteSupport")
        self.setWindowIcon(app_icon())
        self.s = settings or QSettings("RemoteSupport", "RemoteSupport")
        self.runner = AsyncRunner()
        self.remote_win = None
        self._sharing = False
        self._stopping = False
        self._quitting = False
        port = port or self.s.value("port", DEFAULT_PORT, type=int)
        self._port = int(port)
        autostart = self.s.value("autostart", True, type=bool) if autostart is None else autostart

        uid = self.s.value("id") or gen_username()
        self.s.setValue("id", uid)
        perm_on = self.s.value("perm_pw_on", False, type=bool)
        perm_pw = str(self.s.value("perm_pw", "") or "")
        start_pw = perm_pw if (perm_on and perm_pw) else gen_password()   # permanent password survives restarts
        self.host = HostController(self.runner, uid, start_pw, agent_kwargs)
        self.host.apply_quality(self.s.value("quality", "balanced"))
        self.host.status.connect(self.on_host_status)
        self.host.consent.connect(self.on_consent)
        self.host.started.connect(self.on_started)
        self.host.start_failed.connect(self.on_start_failed)
        self.viewer = ViewerController(self.runner)
        self.viewer.opened.connect(self.on_opened)
        self.viewer.failed.connect(self.on_failed)
        self.viewer.ended.connect(self.on_ended)
        self.viewer.frame.connect(self.on_frame)
        self.banner = ControlBanner(self.host.disconnect_technician)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_sidebar())
        self.stack = QStackedWidget()
        self.share_page = self._build_share_page(uid)
        self.connect_page = self._build_connect_page()
        self.settings_page = self._build_settings_page()
        for p in (self.share_page, self.connect_page, self.settings_page):
            self.stack.addWidget(p)
        outer.addWidget(self.stack, 1)

        self.setStyleSheet(style())
        self.setMinimumSize(920, 640)
        self.resize(1000, 680)
        self._build_tray()
        self.refresh_addresses()
        self.refresh_recents()
        self.show_page(0)
        self._set_state("idle", "Sharing is turned off", "Nobody can connect to this computer.")
        if autostart:
            self.start_sharing()

    # ======================================================================== building
    def _build_sidebar(self):
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(236)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(16, 22, 16, 18)
        lay.setSpacing(6)
        brand = QHBoxLayout()
        brand.setSpacing(11)
        logo = QLabel()
        logo.setPixmap(app_pixmap(40))
        logo.setFixedSize(40, 40)
        brand.addWidget(logo)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(label("RemoteSupport", "brand"))
        col.addWidget(label("Secure remote assistance", "tagline"))
        brand.addLayout(col)
        lay.addLayout(brand)
        lay.addSpacing(26)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_btns = []
        for i, (text, ic) in enumerate([("This computer", "share"), ("Connect", "link"),
                                        ("Settings", "sliders")]):
            b = NavButton(text, ic)
            b.clicked.connect(lambda _=False, i=i: self.show_page(i))
            self.nav_group.addButton(b)
            self.nav_btns.append(b)
            lay.addWidget(b)
        lay.addStretch()
        self.pill = StatusPill()
        lay.addWidget(self.pill)
        lay.addSpacing(6)
        lay.addWidget(label(f"Version {__version__}", "ver"), 0, Qt.AlignHCenter)
        return side

    def _build_share_page(self, uid):
        page, lay = _page("This computer", "Let a technician see and control this PC. You stay in control: "
                          "nothing is shared until you click Allow.")
        self.state = StateCard()
        self.state.action.clicked.connect(self.host.disconnect_technician)
        lay.addWidget(self.state)

        row = QHBoxLayout()
        row.setSpacing(18)
        card = Card("Connection details", "Send these three details to your technician.")
        self.addr_box = QComboBox()
        self.addr_box.setObjectName("addr")
        self.addr_hint = label("", "hint")
        self.addr_box.currentIndexChanged.connect(self._update_addr_hint)
        card.body.addSpacing(4)
        card.body.addWidget(CopyField("Address", self.addr_box, lambda: self.addr_box.currentData()))
        card.body.addWidget(self.addr_hint)
        self.id_edit = QLineEdit(group_id(uid))
        self.id_edit.setReadOnly(True)
        self.id_edit.setObjectName("big")
        card.body.addWidget(CopyField("ID", self.id_edit, lambda: self.id_edit.text()))
        self.pw_edit = QLineEdit(self.host.password)
        self.pw_edit.setReadOnly(True)
        self.pw_edit.setObjectName("big")
        card.body.addWidget(CopyField("Password", self.pw_edit, lambda: self.pw_edit.text()))
        card.body.addSpacing(4)
        btns = QHBoxLayout()
        self.copy_all_btn = QPushButton("Copy all details")
        self.copy_all_btn.setObjectName("primary")
        self.copy_all_btn.setIcon(icon("copy", "#ffffff", 17))
        self.copy_all_btn.clicked.connect(self.copy_details)
        self.newpw_btn = QPushButton("New password")
        self.newpw_btn.setIcon(icon("refresh", C["slate"], 17))
        self.newpw_btn.clicked.connect(self.new_password)
        btns.addWidget(self.copy_all_btn, 1)
        btns.addWidget(self.newpw_btn)
        card.body.addLayout(btns)
        row.addWidget(card, 3)

        right = QVBoxLayout()
        right.setSpacing(18)
        how = Card("How it works")
        for n, text in enumerate(["Send the address, ID and password to your technician.",
                                  "They connect from their own copy of RemoteSupport.",
                                  "You click Allow, then watch everything they do."], 1):
            r = QHBoxLayout()
            r.setSpacing(12)
            num = QLabel(str(n))
            num.setObjectName("step")
            num.setFixedSize(24, 24)
            num.setAlignment(Qt.AlignCenter)
            r.addWidget(num, 0, Qt.AlignTop)
            r.addWidget(label(text, "sub", True), 1)
            how.body.addLayout(r)
        how.body.addWidget(divider())
        how.body.addWidget(label("Different networks? Install the free Tailscale app on both PCs and use "
                                 "the 100.x.x.x address.", "hint", True))
        right.addWidget(how)
        sw = Card()
        self.share_toggle = ToggleSwitch(False)
        self.share_toggle.clicked.connect(self.on_share_clicked)
        sw.body.addWidget(_row("Allow remote support", "Turn off to stop anyone from connecting.",
                               self.share_toggle))
        right.addWidget(sw)
        right.addStretch()
        row.addLayout(right, 2)
        lay.addLayout(row)
        lay.addStretch()
        return page

    def _build_connect_page(self):
        page, lay = _page("Connect to a computer", "Enter the details the other person sent you. "
                          "They will be asked to allow the connection.")
        row = QHBoxLayout()
        row.setSpacing(18)
        card = Card("New connection")
        card.body.addSpacing(2)

        def field(caption, edit):
            box = QVBoxLayout()
            box.setSpacing(5)
            box.addWidget(label(caption.upper(), "cap"))
            box.addWidget(edit)
            card.body.addLayout(box)
        recents = self._recents()
        self.r_addr = QLineEdit(recents[0]["address"] if recents else "")
        self.r_addr.setPlaceholderText("e.g. 100.101.102.103:8765")
        self.r_id = QLineEdit(recents[0]["id"] if recents else "")
        self.r_id.setPlaceholderText("9-digit ID")
        self.r_pw = QLineEdit()
        self.r_pw.setEchoMode(QLineEdit.Password)
        self.r_pw.setPlaceholderText("Password")
        self._eye = QAction(icon("eye", C["muted"], 18), "", self.r_pw)
        self._eye.setToolTip("Show / hide password")
        self._eye.triggered.connect(self._toggle_pw)
        self.r_pw.addAction(self._eye, QLineEdit.TrailingPosition)
        field("Address", self.r_addr)
        field("ID", self.r_id)
        field("Password", self.r_pw)
        card.body.addSpacing(6)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("primary")
        self.connect_btn.setIcon(icon("link", "#ffffff", 18))
        self.connect_btn.setCursor(Qt.PointingHandCursor)
        self.connect_btn.clicked.connect(self.connect_remote)
        for w in (self.r_addr, self.r_id, self.r_pw):
            w.returnPressed.connect(self.connect_remote)
        card.body.addWidget(self.connect_btn)
        self.r_status = QLabel("")
        self.r_status.setWordWrap(True)
        self.r_status.setTextFormat(Qt.RichText)
        card.body.addWidget(self.r_status)
        card.body.addStretch()
        card.body.addWidget(divider())
        card.body.addWidget(label("Not on the same network? Install the free Tailscale app on both computers "
                                  "and use the other PC's 100.x.x.x address.", "hint", True))
        row.addWidget(card, 3)

        rc = Card()
        head = QHBoxLayout()
        head.addWidget(label("Recent", "h2"))
        head.addStretch()
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("link")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.clicked.connect(self.clear_recents)
        head.addWidget(self.clear_btn)
        rc.body.addLayout(head)
        self.recent_list = QListWidget()
        self.recent_list.itemClicked.connect(self.use_recent)
        rc.body.addWidget(self.recent_list, 1)
        self.recent_empty = label("No recent connections yet.\nThey will appear here after you connect.",
                                  "hint", True)
        self.recent_empty.setAlignment(Qt.AlignCenter)
        rc.body.addWidget(self.recent_empty, 1)
        row.addWidget(rc, 2)
        lay.addLayout(row, 1)
        return page

    def _build_settings_page(self):
        page, lay = _page("Settings", "Changes are saved automatically.")
        c0 = Card("Password")
        self.perm_toggle = ToggleSwitch(self.s.value("perm_pw_on", False, type=bool)
                                        and bool(self.s.value("perm_pw", "")))
        self.perm_toggle.clicked.connect(self.on_perm_toggled)
        c0.body.addWidget(_row("Keep the same password every time",
                               "The password no longer changes when the app restarts. It is saved on this computer.",
                               self.perm_toggle))
        c0.body.addWidget(divider())
        own = QWidget()
        own_l = QHBoxLayout(own)
        own_l.setContentsMargins(0, 0, 0, 0)
        self.own_pw = QLineEdit()
        self.own_pw.setEchoMode(QLineEdit.Password)
        self.own_pw.setPlaceholderText("Type your own password (8+ characters)")
        self.own_pw.setMinimumWidth(260)
        self.own_pw.returnPressed.connect(self.set_own_password)
        self.own_pw_btn = QPushButton("Save password")
        self.own_pw_btn.setObjectName("primary")
        self.own_pw_btn.clicked.connect(self.set_own_password)
        own_l.addWidget(self.own_pw, 1)
        own_l.addWidget(self.own_pw_btn)
        c0.body.addWidget(own)
        self.own_pw_msg = label("", "hint", True)
        c0.body.addWidget(self.own_pw_msg)
        lay.addWidget(c0)
        c1 = Card("Sharing")
        self.autostart_toggle = ToggleSwitch(self.s.value("autostart", True, type=bool))
        self.autostart_toggle.clicked.connect(lambda on: self.s.setValue("autostart", on))
        c1.body.addWidget(_row("Start sharing when the app opens",
                               "Recommended for the person who needs help.", self.autostart_toggle))
        c1.body.addWidget(divider())
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(self._port)
        self.port_spin.setFixedWidth(110)
        self.port_spin.editingFinished.connect(self.on_port_changed)
        c1.body.addWidget(_row("Port", "The technician's address ends with this number.", self.port_spin))
        c1.body.addWidget(divider())
        self.quality_box = QComboBox()
        for key, text in (("fast", "Fast (low bandwidth)"), ("balanced", "Balanced"),
                          ("sharp", "Sharp (best quality)")):
            self.quality_box.addItem(text, key)
        self.quality_box.setCurrentIndex(max(0, self.quality_box.findData(self.s.value("quality", "balanced"))))
        self.quality_box.setFixedWidth(190)
        self.quality_box.currentIndexChanged.connect(self.on_quality_changed)
        c1.body.addWidget(_row("Screen quality", "Lower quality feels smoother on slow connections.",
                               self.quality_box))
        lay.addWidget(c1)
        c2 = Card("Application")
        self.tray_toggle = ToggleSwitch(self.s.value("tray", False, type=bool))
        self.tray_toggle.clicked.connect(self.on_tray_toggled)
        tray_ok = QSystemTrayIcon.isSystemTrayAvailable()
        self.tray_toggle.setEnabled(tray_ok)
        c2.body.addWidget(_row("Keep running in the system tray when closed",
                               "Sharing stays on while the window is hidden." if tray_ok
                               else "The system tray is not available on this desktop.", self.tray_toggle))
        lay.addWidget(c2)
        c3 = Card("Security")
        for ic, text in (("lock", "End-to-end encrypted (AES-256-GCM). The password never leaves either computer."),
                         ("ok", "Every connection needs your permission, and shows a red banner while active."),
                         ("clock", "Five wrong passwords lock the computer for a minute.")):
            r = QHBoxLayout()
            r.setSpacing(10)
            i = QLabel()
            i.setPixmap(icon_pixmap(ic, C["primary"], 18))
            r.addWidget(i, 0, Qt.AlignTop)
            r.addWidget(label(text, "sub", True), 1)
            c3.body.addLayout(r)
        lay.addWidget(c3)
        lay.addStretch()
        # Scrollable, so the cards keep their natural height on small windows instead of being squashed.
        page.setMinimumHeight(lay.sizeHint().height())
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setObjectName("settingsScroll")
        sa.setStyleSheet("#settingsScroll, #settingsScroll > QWidget > QWidget { background: transparent; }")
        sa.setWidget(page)
        return sa

    def _build_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip("RemoteSupport")
        menu = QMenu()
        menu.addAction("Open RemoteSupport", self._restore)
        menu.addAction("Disconnect technician", self.host.disconnect_technician)
        menu.addSeparator()
        menu.addAction("Quit", self._quit)
        self.tray.setContextMenu(menu)
        self._tray_menu = menu
        self.tray.activated.connect(lambda r: self._restore() if r == QSystemTrayIcon.Trigger else None)
        self.tray.setVisible(self.s.value("tray", False, type=bool))

    # ======================================================================== navigation
    def show_page(self, i):
        self.stack.setCurrentIndex(i)
        self.nav_btns[i].setChecked(True)

    def _restore(self):
        self.show(); self.raise_(); self.activateWindow()

    def _quit(self):
        self._quitting = True
        self.close()

    # ======================================================================== sharing (client side)
    def refresh_addresses(self):
        self.addr_box.clear()
        for addr, desc in local_addresses(self._port):
            self.addr_box.addItem(addr, addr)
            self.addr_box.setItemData(self.addr_box.count() - 1, desc, Qt.UserRole + 1)
        self._update_addr_hint()

    def _update_addr_hint(self):
        desc = self.addr_box.currentData(Qt.UserRole + 1) or ""
        extra = f"  ({self.addr_box.count()} addresses available)" if self.addr_box.count() > 1 else ""
        self.addr_hint.setText((f"{desc}{extra}" if desc else "").strip())

    def _set_state(self, kind, title, detail="", action=False):
        self.state.set_state(kind, title, detail, action)
        pill = {"ok": ("ok", "Sharing is on"), "warn": ("warn", "Incoming connection"),
                "danger": ("danger", "Technician connected" if action else "Problem"),
                "idle": ("idle", "Sharing is off")}[kind]
        self.pill.set_state(*pill)

    def start_sharing(self):
        self._stopping = False
        self.share_toggle.setEnabled(False)
        self._set_state("idle", "Starting...", "")
        self.host.start(self._port)

    def on_started(self, port):
        self._sharing = True
        self._port = port
        self.refresh_addresses()
        self.share_toggle.setChecked(True)
        self.share_toggle.setEnabled(True)

    def on_start_failed(self, msg):
        self._sharing = False
        self.share_toggle.setChecked(False)
        self.share_toggle.setEnabled(True)
        self._set_state("danger", "Sharing could not start", msg + " Pick a different port in Settings.")

    def on_share_clicked(self, on):
        if on:
            self.start_sharing()
        else:
            self._sharing = False
            self._stopping = True
            self.host.stop()

    def on_port_changed(self):
        port = self.port_spin.value()
        if port == self._port:
            return
        self._port = port
        self.s.setValue("port", port)
        self.refresh_addresses()
        if self._sharing:
            self._stopping = False
            self.share_toggle.setEnabled(False)
            self.host.restart(port)

    def on_quality_changed(self):
        key = self.quality_box.currentData()
        self.s.setValue("quality", key)
        self.host.apply_quality(key)

    def on_tray_toggled(self, on):
        self.s.setValue("tray", on)
        if self.tray:
            self.tray.setVisible(on)

    def new_password(self):
        pw = gen_password()
        self.host.set_password(pw)
        self.pw_edit.setText(pw)
        if self.perm_toggle.isChecked():          # permanent mode: the new one becomes the permanent one
            self.s.setValue("perm_pw", pw)

    def on_perm_toggled(self, on):
        self.s.setValue("perm_pw_on", bool(on))
        if on:
            self.s.setValue("perm_pw", self.host.password)    # keep exactly the password shown now
            self.own_pw_msg.setText("This password is now permanent.")
        else:
            self.s.remove("perm_pw")
            self.own_pw_msg.setText("A new random password will be made each time the app starts.")

    def set_own_password(self):
        pw = self.own_pw.text()
        if len(pw) < 8:
            self.own_pw_msg.setText("Use at least 8 characters.")
            return
        self.host.set_password(pw)
        self.pw_edit.setText(pw)
        self.s.setValue("perm_pw", pw)
        self.s.setValue("perm_pw_on", True)
        self.perm_toggle.setChecked(True)
        self.own_pw.clear()
        self.own_pw_msg.setText("Saved. This password will be used every time.")

    def copy_details(self):
        QGuiApplication.clipboard().setText(
            f"Address: {self.addr_box.currentData() or ''}\nID: {self.id_edit.text()}\n"
            f"Password: {self.host.password}")
        self.copy_all_btn.setText("Copied!")
        QTimer.singleShot(1400, lambda: self.copy_all_btn.setText("Copy all details"))

    def on_host_status(self, state, detail):
        if self._stopping and state != "stopped":
            return
        if state == "stopped":
            self._set_state("idle", "Sharing is turned off", "Nobody can connect to this computer.")
            self.banner.hide()
            return
        if state == "waiting":
            note = "" if (not detail or detail.startswith("Ready")) else detail
            self._set_state("ok", "Ready for remote support",
                            note or "Waiting for your technician to connect. Send them the details below.")
            self.banner.hide()
        elif state == "authenticating":
            self._set_state("warn", "Someone is connecting...", "Checking the ID and password.")
        elif state == "awaiting_consent":
            self._set_state("warn", "Waiting for your permission", "Choose Allow or Deny in the dialog.")
        elif state == "active":
            self._set_state("danger", "A technician is connected",
                            "They can see and control this computer. You can disconnect them at any time.",
                            action=True)
            self.banner.pop()
        elif state == "error":
            self._set_state("danger", "Problem", detail)

    def on_consent(self, fut):
        self._restore()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Allow remote access?")
        box.setText("A technician wants to view and control this computer.")
        box.setInformativeText("They entered the correct ID and password. Only allow this if you are "
                               "expecting them.")
        allow = box.addButton("Allow", QMessageBox.AcceptRole)
        deny = box.addButton("Deny", QMessageBox.RejectRole)
        box.setDefaultButton(deny)
        box.exec()
        fut.set_result(box.clickedButton() is allow)

    # ======================================================================== connecting (technician)
    def _toggle_pw(self):
        hidden = self.r_pw.echoMode() == QLineEdit.Password
        self.r_pw.setEchoMode(QLineEdit.Normal if hidden else QLineEdit.Password)
        self._eye.setIcon(icon("eye-off" if hidden else "eye", C["muted"], 18))

    @staticmethod
    def _dot(color, text):
        return f"<span style='color:{color}'>●</span>&nbsp; {text}"

    def connect_remote(self):
        if self.viewer.client is not None:
            return
        addr, uid, pw = self.r_addr.text().strip(), self.r_id.text().strip(), self.r_pw.text()
        if not (addr and uid and pw):
            self.r_status.setText(self._dot(C["red"], "Enter the address, ID and password."))
            return
        self.connect_btn.setEnabled(False)
        self.connect_btn.setText("Connecting...")
        self.r_status.setText(self._dot(C["amber"], "Connecting. The other person may need to click Allow."))
        self.viewer.connect_to(addr, uid, pw)

    def _reset_connect_btn(self):
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText("Connect")

    def on_failed(self, msg):
        self._reset_connect_btn()
        self.r_status.setText(self._dot(C["red"], msg))

    def on_opened(self):
        self._reset_connect_btn()
        self.r_status.setText(self._dot(C["green"], "Connected."))
        self._add_recent(self.r_addr.text().strip(), self.r_id.text().strip())
        self.r_pw.clear()
        win = self.remote_win = RemoteWindow(group_id(self.r_id.text()))
        win.view.inputEvent.connect(self.viewer.send)
        win.closed.connect(self.viewer.disconnect)
        win.show()

    def on_frame(self, sw, sh, x, y, w, h, jpeg):
        if self.remote_win is not None:
            self.remote_win.view.apply_frame(sw, sh, x, y, w, h, jpeg)

    def on_ended(self, reason):
        if self.remote_win is not None:
            win, self.remote_win = self.remote_win, None
            win.blockSignals(True)
            win.close()
        self._reset_connect_btn()
        self.r_status.setText(self._dot(C["muted"], reason))
        self._restore()

    # ---- recent connections (never stores passwords) -----------------------
    def _recents(self):
        try:
            data = json.loads(self.s.value("recents", "[]"))
            return [d for d in data if isinstance(d, dict) and "address" in d and "id" in d]
        except (ValueError, TypeError):
            return []

    def _add_recent(self, address, uid):
        items = [d for d in self._recents() if not (d["address"] == address and d["id"] == uid)]
        items.insert(0, {"address": address, "id": uid, "ts": int(time.time())})
        self.s.setValue("recents", json.dumps(items[:8]))
        self.refresh_recents()

    def refresh_recents(self):
        self.recent_list.clear()
        items = self._recents()
        for d in items:
            it = QListWidgetItem()
            it.setData(Qt.UserRole, d)
            it.setSizeHint(QSize(0, 56))
            self.recent_list.addItem(it)
            w = QWidget()
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
            wl = QVBoxLayout(w)
            wl.setContentsMargins(12, 6, 12, 6)
            wl.setSpacing(3)
            wl.addWidget(label(group_id(d["id"]), "recentId"))
            wl.addWidget(label(d["address"], "hint"))
            self.recent_list.setItemWidget(it, w)
        self.recent_list.setVisible(bool(items))
        self.recent_empty.setVisible(not items)
        self.clear_btn.setVisible(bool(items))

    def use_recent(self, item):
        d = item.data(Qt.UserRole)
        self.r_addr.setText(d["address"])
        self.r_id.setText(d["id"])
        self.r_pw.setFocus()

    def clear_recents(self):
        self.s.setValue("recents", "[]")
        self.refresh_recents()

    # ======================================================================== lifecycle
    def closeEvent(self, e):
        if (not self._quitting and self.tray is not None and self.tray.isVisible()):
            self.hide()
            if not self.s.value("tray_hint_shown", False, type=bool):
                self.tray.showMessage("RemoteSupport is still running",
                                      "Sharing stays on. Use the tray icon to open or quit.")
                self.s.setValue("tray_hint_shown", True)
            e.ignore()
            return
        busy = self.viewer.client is not None or self.banner.isVisible()
        if busy and QMessageBox.question(self, "Quit RemoteSupport",
                                         "A session is active. Quit and disconnect?") != QMessageBox.Yes:
            self._quitting = False
            e.ignore()
            return
        self.banner.hide()
        if self.remote_win is not None:
            self.remote_win.blockSignals(True)
            self.remote_win.close()
        self.viewer.disconnect()
        self.host.stop()
        if self.tray:
            self.tray.hide()
        QTimer.singleShot(400, self.runner.shutdown)
        super().closeEvent(e)
        QApplication.quit()
