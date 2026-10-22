"""Regenerate assets/icon.png and assets/icon.ico from the built-in vector logo (rsupport/ui/theme.py)."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402
from PIL import Image  # noqa: E402

from rsupport.ui.theme import _render, app_svg  # noqa: E402

app = QApplication([])
os.makedirs(os.path.join(ROOT, "assets"), exist_ok=True)
png = os.path.join(ROOT, "assets", "icon.png")
_render(app_svg(), 512, 1).save(png)
Image.open(png).save(os.path.join(ROOT, "assets", "icon.ico"),
                     sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icons written to assets/")
