"""LIVE end-to-end test of the PySide6 app with a REAL screen (Xvfb): real mss capture, real pynput
mouse/keyboard injection, the real Qt remote window. Run:  python tests/live_qt_test.py"""
import os
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DISPLAY = ":96"
os.environ["DISPLAY"] = DISPLAY            # capture + injection use the virtual screen...
os.environ["QT_QPA_PLATFORM"] = "offscreen"  # ...while Qt renders the app windows off-screen

from PySide6.QtCore import QPoint, QSettings, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from rsupport.ui.app import make_app  # noqa: E402
from rsupport.ui.main_window import MainWindow  # noqa: E402
from tests.live_xvfb_test import TARGET  # noqa: E402


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def wait_for(cond, timeout=15, what=""):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("timeout: " + what)


def main():
    out = os.environ.get("SHOT_DIR", tempfile.mkdtemp())
    tmp = tempfile.mkdtemp()
    log = os.path.join(tmp, "events.log"); open(log, "w").close()
    procs = [subprocess.Popen(["Xvfb", DISPLAY, "-screen", "0", "800x600x24", "-nolisten", "tcp"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
    try:
        time.sleep(1.5)
        procs.append(subprocess.Popen([sys.executable, "-c", TARGET, log], env=dict(os.environ)))
        time.sleep(1.0)

        MainWindow.on_consent = lambda self, fut: fut.set_result(True)     # the client clicks Allow
        app = make_app()
        port = free_port()
        client = MainWindow(QSettings(os.path.join(tmp, "c.ini"), QSettings.IniFormat),
                            agent_kwargs=dict(fps=20), port=port, autostart=True)
        tech = MainWindow(QSettings(os.path.join(tmp, "t.ini"), QSettings.IniFormat), autostart=False,
                          port=free_port())
        client.show(); tech.show()
        wait_for(lambda: client.state.property("kind") == "ok", what="client ready")

        tech.show_page(1)
        tech.r_addr.setText(f"127.0.0.1:{port}")
        tech.r_id.setText(client.host.username)
        tech.r_pw.setText(client.host.password)
        tech.connect_btn.click()
        wait_for(lambda: tech.remote_win is not None, what="remote window")
        view = tech.remote_win.view
        wait_for(lambda: view.fb is not None, what="first frame")
        time.sleep(1.0); QApplication.processEvents()
        fb = view.fb
        print("remote screen:", fb.width(), "x", fb.height())
        assert (fb.width(), fb.height()) == (800, 600)
        c = fb.pixelColor(200, 150)
        assert abs(c.red() - 0xd8) < 40 and abs(c.green() - 0x1b) < 40, c.name()
        print("OK  real screen streamed into the Qt window")

        r = view.target_rect()
        pos = QPoint(r.x() + int(0.25 * r.width()), r.y() + int(0.25 * r.height()))
        QTest.mouseMove(view, pos)
        QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, pos)
        QTest.keyClick(view, Qt.Key_H)
        QTest.keyClick(view, Qt.Key_I)
        QTest.keyClick(view, Qt.Key_Return)
        wait_for(lambda: len(open(log).read().splitlines()) >= 4, what="events on target")
        import json
        events = [json.loads(l) for l in open(log) if l.strip()]
        print("target window received:", events)
        click = [e["click"] for e in events if "click" in e][0]
        assert abs(click[0] - 200) <= 3 and abs(click[1] - 150) <= 3, click
        assert [e["key"] for e in events if "key" in e][:3] == ["h", "i", "Return"]
        print("OK  real mouse + keyboard injection through the Qt window")

        os.makedirs(out, exist_ok=True)
        tech.remote_win.grab().save(os.path.join(out, "remote_window.png"))
        wait_for(lambda: client.state.property("kind") == "danger", what="client banner")
        client.show_page(0); QApplication.processEvents()
        client.grab().save(os.path.join(out, "client_connected.png"))
        client.state.action.click()
        wait_for(lambda: tech.remote_win is None, what="disconnect")
        print("OK  client Disconnect button ended the session")
        for w in (client, tech):
            w._quitting = True; w.close()
        wait_for(lambda: True, 0.5)
        print("ALL LIVE QT CHECKS PASSED; screenshots in", out)
    finally:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    main()
