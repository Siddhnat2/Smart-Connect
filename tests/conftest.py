import asyncio
import contextlib

import pytest
from PIL import Image, ImageDraw

from rsupport import crypto
from rsupport.host import HostAgent
from rsupport.inputctl import Injector
from rsupport.relay import serve_relay
from rsupport.viewer import ViewerClient


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    monkeypatch.setattr(crypto, "MIN_ITERS", 100)


class FakeCapturer:
    """Deterministic 'screen': dark background + a coloured square we can move."""
    def __init__(self, w=320, h=200):
        self.w, self.h = w, h
        self.geometry = (0, 0, w, h)
        self.sq = (10, 10, "red")

    def grab(self):
        img = Image.new("RGB", (self.w, self.h), (20, 20, 30))
        x, y, c = self.sq
        ImageDraw.Draw(img).rectangle([x, y, x + 40, y + 40], fill=c)
        return img


class RecInjector(Injector):
    def __init__(self):
        self.calls = []
    def move(self, x, y): self.calls.append(("move", round(x, 3), round(y, 3)))
    def button(self, n, d, x, y): self.calls.append(("btn", n, d, round(x, 3), round(y, 3)))
    def scroll(self, dx, dy): self.calls.append(("scroll", dx, dy))
    def key(self, n, d): self.calls.append(("key", n, d))
    def release_all(self): self.calls.append(("release_all",))


class Stack:
    pass


@contextlib.asynccontextmanager
async def stack(relay_key="", unattended=True, consent=None, username="123456789",
                password="s3cret-Pass", **host_kw):
    s = Stack()
    s.relay, server = await serve_relay("127.0.0.1", 0, relay_key)
    port = server.sockets[0].getsockname()[1]
    s.url = f"ws://127.0.0.1:{port}"
    s.cap, s.inj = FakeCapturer(), RecInjector()
    s.statuses = []
    s.username, s.password, s.relay_key = username, password, relay_key
    waiting = asyncio.Event()

    def on_status(state, detail=""):
        s.statuses.append(state)
        if state == "waiting":
            waiting.set()

    s.host = HostAgent(s.url, username, password, relay_key=relay_key, unattended=unattended,
                       consent_cb=consent, status_cb=on_status, capturer=s.cap,
                       injector=s.inj, fps=30, iters=1000, **host_kw)
    task = asyncio.create_task(s.host.run())
    await asyncio.wait_for(waiting.wait(), 5)

    def viewer(password=None, username=None, key=None, on_frame=None, **kw):
        return ViewerClient(s.url, username or s.username,
                            password if password is not None else s.password,
                            relay_key=s.relay_key if key is None else key,
                            on_frame=on_frame, **kw)
    s.viewer = viewer
    try:
        yield s
    finally:
        s.host.stop()
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
        server.close()
        await server.wait_closed()
