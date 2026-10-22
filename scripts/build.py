#!/usr/bin/env python3
"""Build the single-file RemoteSupport app for the CURRENT operating system.

    python scripts/build.py

Output: dist/RemoteSupport.exe (Windows) | dist/RemoteSupport (Linux) | dist/RemoteSupport.app (macOS)
The same file is used by the client and by the technician. PyInstaller cannot cross-compile, so build on
each OS you need (the GitHub Actions workflow in .github/workflows/build.yml does all three for you).
"""
import os
import platform
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
system = platform.system()

EXCLUDES = ["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQuick", "PySide6.QtQml",
            "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtCharts",
            "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtSql", "PySide6.QtTest",
            "tkinter", "pytest"]
HIDDEN = {"Windows": ["pynput.keyboard._win32", "pynput.mouse._win32"],
          "Darwin": ["pynput.keyboard._darwin", "pynput.mouse._darwin"],
          "Linux": ["pynput.keyboard._xorg", "pynput.mouse._xorg"]}[system]


def run(cmd, **kw):
    print("+", " ".join(cmd))
    return subprocess.run(cmd, check=True, **kw)


def ensure_deps():
    """pip-install only what is missing (so it also works inside managed/locked-down Python installs)."""
    import importlib.util
    need = {"PySide6": "PySide6-Essentials", "websockets": "websockets", "cryptography": "cryptography",
            "PIL": "Pillow", "mss": "mss", "pynput": "pynput", "PyInstaller": "pyinstaller"}
    missing = [pkg for mod, pkg in need.items() if importlib.util.find_spec(mod) is None]
    if missing:
        run([sys.executable, "-m", "pip", "install", "-q", *missing])


def main():
    ensure_deps()
    icon = os.path.join("assets", "icon.ico" if system == "Windows" else "icon.png")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--name", "RemoteSupport",
           "--windowed", "--paths", ".", "--icon", icon, "--add-data", f"assets{os.pathsep}assets"]
    if system != "Darwin":
        cmd.append("--onefile")                      # macOS: a .app bundle is the right deliverable
    else:
        cmd += ["--osx-bundle-identifier", "com.remotesupport.app"]
    for h in HIDDEN:
        cmd += ["--hidden-import", h]
    for e in EXCLUDES:
        cmd += ["--exclude-module", e]
    cmd.append("main.py")
    run(cmd)

    exe = {"Windows": "dist/RemoteSupport.exe", "Linux": "dist/RemoteSupport",
           "Darwin": "dist/RemoteSupport.app/Contents/MacOS/RemoteSupport"}[system]
    print("\nSmoke-testing the built app:", exe)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([exe, "selftest"], env=env, capture_output=True, text=True, timeout=180)
    print(r.stdout.strip(), r.stderr.strip()[-400:])
    if r.returncode != 0:
        sys.exit("selftest of the built app FAILED")
    print(f"\nOK -> {exe}")


if __name__ == "__main__":
    main()
