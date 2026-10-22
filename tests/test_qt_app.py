"""PySide6 app tests (offscreen): two real MainWindows - a client and a technician - talk to each other."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import socket
import time

import pytest
from PySide6.QtCore import QPoint, QSettings, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from rsupport.ui.app import make_app
from rsupport.ui.controllers import normalize_address
from rsupport.ui.main_window import MainWindow, group_id
from rsupport.ui.remote import key_name

from .conftest import FakeCapturer, RecInjector


@pytest.fixture(scope="session")
def app():
    return make_app()


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def wait_for(cond, timeout=10.0, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


def kind(w):
    return w.state.property("kind")


class Env:
    pass


@pytest.fixture
def env(app, tmp_path, monkeypatch, request):
    e = Env()
    e.decision = {"allow": True}
    if not request.node.get_closest_marker("real_consent"):     # default: auto-answer the dialog
        monkeypatch.setattr(MainWindow, "on_consent",
                            lambda self, fut: fut.set_result(e.decision["allow"]))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    e.windows = []

    def make(name, **kw):
        s = QSettings(str(tmp_path / f"{name}.ini"), QSettings.IniFormat)
        w = MainWindow(settings=s, **kw)
        w.show()
        e.windows.append(w)
        return w

    e.cap, e.inj = FakeCapturer(), RecInjector()
    e.port = free_port()
    e.client = make("client", agent_kwargs=dict(capturer=e.cap, injector=e.inj, iters=1000, fps=30),
                    port=e.port, autostart=True)
    wait_for(lambda: kind(e.client) == "ok", what="client ready")
    e.tech = make("tech", autostart=False, port=free_port())
    e.make = make

    def connect(password=None, address=None, uid=None):
        e.tech.show_page(1)
        e.tech.r_addr.setText(address or f"127.0.0.1:{e.port}")
        e.tech.r_id.setText(uid or e.client.host.username)
        e.tech.r_pw.setText(password if password is not None else e.client.host.password)
        e.tech.connect_btn.click()
    e.connect = connect
    yield e
    for w in e.windows:
        try:
            w.host.disconnect_technician()
            w.viewer.disconnect()
        except Exception:
            pass
    wait_for(lambda: True, 0.3)
    for w in e.windows:
        w._quitting = True
        w.close()
    wait_for(lambda: True, 0.5)                                  # let async shutdown finish
    for w in e.windows:
        w.runner.shutdown()


# ---------------------------------------------------------------- client mode
def test_opening_the_app_starts_client_mode(env):
    c = env.client
    assert c.share_toggle.isChecked() and kind(c) == "ok"
    assert "Sharing is on" in c.pill.text()
    assert len(c.host.username) == 9 and c.host.username.isdigit()
    assert len(c.host.password) == 12
    assert c.id_edit.text() == group_id(c.host.username)
    assert c.addr_box.currentData().endswith(f":{env.port}")
    assert c.nav_btns[0].isChecked()                       # opens on "This computer"
    assert not env.tech.share_toggle.isChecked()           # the other window did not autostart


def test_group_id_and_address_helpers_and_keys():
    assert group_id("123456789") == "123 456 789"
    assert normalize_address("1.2.3.4") == "ws://1.2.3.4:8765"
    assert normalize_address("1.2.3.4:9000") == "ws://1.2.3.4:9000"
    assert normalize_address(" ws://h:1 ") == "ws://h:1"
    assert key_name(int(Qt.Key_Return), "\r") == "Return"
    assert key_name(int(Qt.Key_C), "\x03") == "c"           # Ctrl+C
    assert key_name(int(Qt.Key_A), "A") == "A"
    assert key_name(int(Qt.Key_F5), "") == "F5"
    assert key_name(int(Qt.Key_Shift), "") == "Shift_L"
    assert key_name(0x01000fff, "") is None


# ----------------------------------------------------------------- full session
def test_full_session_view_input_and_disconnect(env):
    env.connect()
    wait_for(lambda: env.tech.remote_win is not None, what="remote window")
    view = env.tech.remote_win.view
    wait_for(lambda: view.fb is not None, what="first frame")
    assert (view.fb.width(), view.fb.height()) == (320, 200)
    assert view.fb.pixelColor(20, 20).red() > 200 and view.fb.pixelColor(20, 20).green() < 60
    wait_for(lambda: kind(env.client) == "danger", what="client shows connected")
    assert env.client.state.action.isVisible() and env.client.banner.isVisible()
    assert "Technician connected" in env.client.pill.text()

    # live update
    env.cap.sq = (200, 120, "lime")
    wait_for(lambda: view.fb.pixelColor(220, 140).green() > 200, what="screen update")

    # mouse
    r = view.target_rect()
    QTest.mouseMove(view, QPoint(r.x() + r.width() // 2, r.y() + r.height() // 4))
    QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, QPoint(r.x() + r.width() // 4, r.y() + r.height() // 2))
    wait_for(lambda: any(c[0] == "btn" and c[1] == "left" and c[2] for c in env.inj.calls), what="click")
    down = [c for c in env.inj.calls if c[0] == "btn" and c[2]][0]
    assert abs(down[3] - 0.25) < 0.02 and abs(down[4] - 0.5) < 0.02
    QTest.mouseClick(view, Qt.RightButton)
    wait_for(lambda: any(c[:2] == ("btn", "right") for c in env.inj.calls), what="right click")

    # keyboard incl. Ctrl+C sequence and Tab (must reach the remote PC)
    view.setFocus()
    QTest.keyClick(view, Qt.Key_H)
    QTest.keyPress(view, Qt.Key_Control)
    QTest.keyClick(view, Qt.Key_C, Qt.ControlModifier)
    QTest.keyRelease(view, Qt.Key_Control)
    QTest.keyClick(view, Qt.Key_Tab)
    QTest.keyClick(view, Qt.Key_Return)
    wait_for(lambda: ("key", "Return", False) in env.inj.calls, what="keys")
    keys = [c[1] for c in env.inj.calls if c[0] == "key" and c[2]]
    assert keys[:2] == ["h", "Control_L"] or keys[:2] == ["H", "Control_L"], keys
    assert "c" in keys and "Tab" in keys and "Return" in keys

    # scroll
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtCore import QPointF
    ev = QWheelEvent(QPointF(50, 50), QPointF(50, 50), QPoint(0, 0), QPoint(0, -120),
                     Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(view, ev)
    wait_for(lambda: ("scroll", 0, -1) in env.inj.calls, what="scroll")

    # losing focus must release held keys on the remote PC
    QTest.keyPress(view, Qt.Key_Shift)
    view.release_all_keys()
    wait_for(lambda: ("key", "Shift_L", False) in env.inj.calls, what="release on focus loss")

    # recents: remembered, password never stored
    rec = json.loads(env.tech.s.value("recents"))
    assert rec[0]["id"] == env.client.host.username and rec[0]["address"] == f"127.0.0.1:{env.port}"
    assert env.client.host.password not in json.dumps(rec) and not env.tech.r_pw.text()
    assert env.tech.recent_list.count() == 1

    # client presses DISCONNECT -> technician window closes, client returns to ready
    env.client.state.action.click()
    wait_for(lambda: env.tech.remote_win is None, what="technician disconnected")
    wait_for(lambda: kind(env.client) == "ok", what="client ready again")
    assert not env.client.banner.isVisible()
    assert ("release_all",) in env.inj.calls
    assert env.tech.connect_btn.isEnabled() and "ended" in env.tech.r_status.text().lower()


def test_wrong_password_is_rejected_with_message(env):
    env.connect(password="definitely-wrong")
    wait_for(lambda: "Wrong" in env.tech.r_status.text(), what="error text")
    assert env.tech.remote_win is None and env.tech.connect_btn.isEnabled()
    assert "active" not in env.client.state.title.text().lower()
    env.connect()                                           # right password afterwards works
    wait_for(lambda: env.tech.remote_win is not None, what="remote window")


def test_permission_denied_message(env):
    env.decision["allow"] = False
    env.connect()
    wait_for(lambda: "did not allow" in env.tech.r_status.text(), what="denied text")
    assert env.tech.remote_win is None


@pytest.mark.real_consent
def test_real_consent_dialog_allow_and_deny(env):          # exercises the real Allow/Deny dialog

    def press(label):
        def go():
            m = QApplication.activeModalWidget()
            if not isinstance(m, QMessageBox):
                QTimer.singleShot(50, go)
                return
            for b in m.buttons():
                if b.text().replace("&", "") == label:
                    b.click()
                    return
        QTimer.singleShot(100, go)

    press("Deny")
    env.connect()
    wait_for(lambda: "did not allow" in env.tech.r_status.text(), 15, "deny")
    env.tech.r_status.setText("")
    press("Allow")
    env.connect()
    wait_for(lambda: env.tech.remote_win is not None, 15, "allow")


def test_unreachable_address_gives_friendly_error(env):
    env.connect(address=f"127.0.0.1:{free_port()}")
    wait_for(lambda: "Could not reach" in env.tech.r_status.text(), what="friendly error")


def test_stop_sharing_toggle_blocks_connections(env):
    env.client.share_toggle.click()                         # user turns sharing off
    wait_for(lambda: kind(env.client) == "idle", what="stopped")
    assert "Sharing is off" in env.client.pill.text()
    env.connect()
    wait_for(lambda: "Could not reach" in env.tech.r_status.text(), what="refused")
    env.client.share_toggle.click()                         # and on again
    wait_for(lambda: kind(env.client) == "ok", what="restarted")


def test_second_technician_is_told_busy_and_lockout(env):
    env.connect()
    wait_for(lambda: env.tech.remote_win is not None, what="first technician")
    t2 = env.make("tech2", autostart=False, port=free_port())
    t2.r_addr.setText(f"127.0.0.1:{env.port}"); t2.r_id.setText(env.client.host.username)
    t2.r_pw.setText(env.client.host.password)
    t2.connect_btn.click()
    wait_for(lambda: "already has a technician" in t2.r_status.text(), what="busy")


# ----------------------------------------------------------------- settings
def test_settings_port_change_restarts_sharing_and_persists(env):
    new = free_port()
    env.client.show_page(2)
    env.client.port_spin.setValue(new)
    env.client.port_spin.editingFinished.emit()
    wait_for(lambda: env.client.addr_box.currentData().endswith(f":{new}") and kind(env.client) == "ok",
             what="restart on new port")
    assert env.client.s.value("port", type=int) == new
    env.port = new
    env.connect()
    wait_for(lambda: env.tech.remote_win is not None, what="connect on the new port")


def test_settings_quality_and_autostart_persist(env):
    c = env.client
    c.quality_box.setCurrentIndex(c.quality_box.findData("sharp"))
    assert c.s.value("quality") == "sharp"
    assert (c.host.agent.fps, c.host.agent.quality) == (15, 80)
    c.quality_box.setCurrentIndex(c.quality_box.findData("fast"))
    assert (c.host.agent.fps, c.host.agent.quality) == (10, 40)
    c.autostart_toggle.click()
    assert c.s.value("autostart", type=bool) is False


def test_new_password_changes_the_accepted_password(env):
    old = env.client.host.password
    env.client.newpw_btn.click()
    new = env.client.host.password
    assert new != old and env.client.pw_edit.text() == new
    time.sleep(0.2); QApplication.processEvents()
    env.connect(password=old)
    wait_for(lambda: "Wrong" in env.tech.r_status.text(), what="old password rejected")
    env.connect(password=new)
    wait_for(lambda: env.tech.remote_win is not None, what="new password accepted")


def test_navigation_and_clear_recents(env):
    for i in (1, 2, 0):
        env.client.nav_btns[i].click()
        assert env.client.stack.currentIndex() == i
    env.tech._add_recent("1.1.1.1:8765", "111222333")
    assert env.tech.recent_list.count() == 1
    env.tech.clear_btn.click()
    assert env.tech.recent_list.count() == 0 and not env.tech.recent_empty.isHidden()


def test_permanent_password_survives_restart(env):
    c = env.client
    c.perm_toggle.click()                                   # keep the password that is shown now
    shown = c.pw_edit.text()
    assert c.s.value("perm_pw") == shown
    c2 = env.make("client", agent_kwargs=dict(capturer=FakeCapturer(), injector=RecInjector(), iters=1000),
                  port=free_port(), autostart=False)       # same settings file = "app restarted"
    assert c2.host.password == shown and c2.pw_edit.text() == shown and c2.perm_toggle.isChecked()


def test_own_password_is_validated_saved_and_accepted(env):
    c = env.client
    c.own_pw.setText("short")
    c.own_pw_btn.click()
    assert "8" in c.own_pw_msg.text() and c.pw_edit.text() != "short"
    c.own_pw.setText("MyLaptop#2026")
    c.own_pw_btn.click()
    assert c.pw_edit.text() == "MyLaptop#2026" and c.perm_toggle.isChecked()
    time.sleep(0.3); QApplication.processEvents()
    env.connect(password="MyLaptop#2026")
    wait_for(lambda: env.tech.remote_win is not None, what="own password accepted")


def test_without_permanent_mode_password_changes_each_start(env):
    first = env.client.pw_edit.text()
    c2 = env.make("client", agent_kwargs=dict(capturer=FakeCapturer(), injector=RecInjector(), iters=1000),
                  port=free_port(), autostart=False)
    assert c2.host.password != first
