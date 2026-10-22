"""Screen capture + dirty-rectangle JPEG encoding."""
import io
import time

from PIL import Image, ImageChops

from .protocol import FRAME_HDR


class MssCapturer:
    """Captures one monitor with `mss`. Must always be called from the same thread."""

    def __init__(self, monitor: int = 1, max_width: int = 1920):
        self.monitor = monitor
        self.max_width = max_width
        self._sct = None
        self.geometry = (0, 0, 1, 1)  # real monitor rect (left, top, w, h) for input mapping

    def grab(self) -> Image.Image:
        if self._sct is None:
            import mss
            self._sct = getattr(mss, "MSS", None) and mss.MSS() or mss.mss()  # new + old mss API
        mon = self._sct.monitors[min(self.monitor, len(self._sct.monitors) - 1)]
        shot = self._sct.grab(mon)
        self.geometry = (mon["left"], mon["top"], shot.width, shot.height)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        if img.width > self.max_width:
            h = round(img.height * self.max_width / img.width)
            img = img.resize((self.max_width, h), Image.BILINEAR)
        return img


class FrameEncoder:
    """Sends only the bounding box of what changed (plus a periodic full refresh)."""

    def __init__(self, full_refresh_s: float = 10.0):
        self.prev = None
        self.full_refresh_s = full_refresh_s
        self._last_full = 0.0

    def encode(self, img: Image.Image, quality: int):
        now = time.monotonic()
        w, h = img.size
        if self.prev is None or self.prev.size != img.size:
            box = (0, 0, w, h)
        else:
            box = ImageChops.difference(self.prev, img).getbbox()
            if box is None:
                return None
            if now - self._last_full > self.full_refresh_s:
                box = (0, 0, w, h)
        if box == (0, 0, w, h):
            self._last_full = now
        buf = io.BytesIO()
        img.crop(box).save(buf, "JPEG", quality=quality)
        self.prev = img
        x0, y0, x1, y1 = box
        return FRAME_HDR.pack(w, h, x0, y0, x1 - x0, y1 - y0) + buf.getvalue()
