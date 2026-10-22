"""Application bootstrap: logging, single-instance guard, error dialog, event loop."""
import logging
import logging.handlers
import os
import sys
import traceback

from PySide6.QtCore import QDir, QLockFile, QStandardPaths
from PySide6.QtWidgets import QApplication, QMessageBox

from .. import __version__
from ..inputctl import platform_init
from .theme import app_icon

log = logging.getLogger("rsupport")


def _setup_logging():
    d = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or os.path.expanduser("~")
    try:
        os.makedirs(d, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(os.path.join(d, "remotesupport.log"),
                                                       maxBytes=1_000_000, backupCount=2,
                                                       encoding="utf-8")
    except OSError:
        handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)


def make_app():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("RemoteSupport")
    app.setOrganizationName("RemoteSupport")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    return app


def run_app() -> int:
    platform_init()
    app = make_app()
    _setup_logging()

    def excepthook(et, ev, tb):
        log.error("unhandled exception\n%s", "".join(traceback.format_exception(et, ev, tb)))
        QMessageBox.critical(None, "RemoteSupport", f"Unexpected error:\n{ev}\n\nDetails were saved to the log file.")
    sys.excepthook = excepthook

    lock = QLockFile(os.path.join(QDir.tempPath(), "remotesupport.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(150):
        QMessageBox.information(None, "RemoteSupport", "RemoteSupport is already running on this computer.")
        return 0

    from .main_window import MainWindow
    win = MainWindow()
    win.show()
    log.info("RemoteSupport %s started", __version__)
    rc = app.exec()
    lock.unlock()
    return rc


def selftest() -> int:
    """Used by CI / after packaging: build the whole UI offscreen, process events, exit 0."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings, QTimer
    app = make_app()
    from .main_window import MainWindow
    import tempfile
    s = QSettings(os.path.join(tempfile.mkdtemp(), "t.ini"), QSettings.IniFormat)
    win = MainWindow(settings=s, autostart=False, port=48765)
    win.show()
    for _ in range(5):
        app.processEvents()
    win.close()
    print("selftest ok", __version__)
    return 0
