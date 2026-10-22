"""Glue between Qt (GUI thread) and asyncio (network thread).

Controllers live in the GUI thread; their coroutines run on a background asyncio loop and
report back through Qt signals (always connect those to bound methods of GUI objects so Qt
delivers them on the GUI thread).
"""
import asyncio
import threading
from concurrent.futures import Future

from PySide6.QtCore import QObject, Signal

from ..host import HostAgent
from ..viewer import ViewerClient, ViewerError

DEFAULT_PORT = 8765
QUALITY_PRESETS = {            # key: (fps, jpeg quality, max stream width)
    "fast": (10, 40, 1280),
    "balanced": (12, 60, 1920),
    "sharp": (15, 80, 2560),
}


def normalize_address(addr: str) -> str:
    """'1.2.3.4' -> 'ws://1.2.3.4:8765', '1.2.3.4:9000' -> 'ws://1.2.3.4:9000'."""
    addr = addr.strip()
    scheme = ""
    if "://" in addr:
        scheme, addr = addr.split("://", 1)
        scheme += "://"
    scheme = scheme or "ws://"
    hostpart = addr.split("/", 1)[0]
    if not (hostpart.startswith("[") or ":" in hostpart):
        addr = hostpart + f":{DEFAULT_PORT}" + addr[len(hostpart):]
    return scheme + addr


def friendly_error(e: Exception) -> str:
    name = type(e).__name__
    if isinstance(e, OSError) or name in ("TimeoutError", "InvalidURI", "InvalidHandshake"):
        return ("Could not reach that address. Check it is correct, that RemoteSupport is open and "
                "sharing on the other computer, and that you are on the same network or VPN "
                "(for example Tailscale).")
    return f"{name}: {e}"


class AsyncRunner:
    """An asyncio event loop running on a background thread."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._t = threading.Thread(target=self._run, daemon=True, name="rsupport-net")
        self._t.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro) -> Future:
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def shutdown(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._t.join(timeout=2)


class HostController(QObject):
    """The 'share this computer' side."""
    status = Signal(str, str)        # state, detail
    consent = Signal(object)         # concurrent Future[bool] to resolve from the GUI thread
    started = Signal(int)
    start_failed = Signal(str)

    def __init__(self, runner, username, password, agent_kwargs=None):
        super().__init__()
        self.runner, self.username, self.password = runner, username, password
        self.agent_kwargs = dict(agent_kwargs or {})
        self.agent = None
        self.server = None

    async def _consent_cb(self, info):
        fut: Future = Future()
        self.consent.emit(fut)
        return await asyncio.wrap_future(fut)

    async def _start(self, port):
        self.agent = HostAgent(None, self.username, self.password, unattended=False,
                               consent_cb=self._consent_cb,
                               status_cb=lambda s, d="": self.status.emit(s, d),
                               **self.agent_kwargs)
        try:
            self.server = await self.agent.listen("0.0.0.0", port)
        except OSError as e:
            self.agent = None
            self.start_failed.emit(f"Cannot use port {port}: {e.strerror or e}")
            return
        self.started.emit(self.agent.port)

    async def _stop(self):
        if self.agent and self.agent._session:
            await self.agent._session.stop()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        self.agent = None
        self.status.emit("stopped", "Sharing is turned off")

    def start(self, port):
        self.runner.submit(self._start(port))

    def stop(self):
        self.runner.submit(self._stop())

    def restart(self, port):
        async def go():
            await self._stop()
            await self._start(port)
        self.runner.submit(go())

    def set_password(self, pw):
        self.password = pw
        if self.agent:
            self.runner.loop.call_soon_threadsafe(self.agent.set_password, pw)

    def apply_quality(self, preset: str):
        fps, quality, width = QUALITY_PRESETS.get(preset, QUALITY_PRESETS["balanced"])
        self.agent_kwargs.update(fps=fps, quality=quality, max_width=width)
        a = self.agent
        if a is not None:
            a.fps, a.quality = fps, quality
            if hasattr(a.capturer, "max_width"):
                a.capturer.max_width = width

    def disconnect_technician(self):
        if self.agent:
            self.agent.request_disconnect()


class ViewerController(QObject):
    """The 'connect to a computer' side."""
    frame = Signal(int, int, int, int, int, int, bytes)
    opened = Signal()
    failed = Signal(str)
    ended = Signal(str)

    def __init__(self, runner):
        super().__init__()
        self.runner = runner
        self.client = None

    def connect_to(self, address, username, password):
        client = ViewerClient(normalize_address(address), username.replace(" ", ""), password,
                              direct=True, on_frame=lambda *a: self.frame.emit(*a))
        self.client = client

        async def go():
            try:
                await client.connect()
            except ViewerError as e:
                self.client = None
                self.failed.emit(str(e))
                return
            except Exception as e:
                self.client = None
                self.failed.emit(friendly_error(e))
                return
            self.opened.emit()
            await client.run()
            self.client = None
            self.ended.emit(client.close_reason or "Session ended.")
        self.runner.submit(go())

    def send(self, event: dict):
        c = self.client
        if c is not None:
            self.runner.submit(c.send_input(event))

    def disconnect(self):
        c = self.client
        if c is not None:
            self.runner.submit(c.close())
