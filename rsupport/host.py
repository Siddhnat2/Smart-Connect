"""Host agent: runs on the *client's* computer.

It only ever makes an OUTBOUND connection to your relay (works behind NAT/firewalls).
Safety properties (by design):
  * nothing is shared until the technician proves knowledge of the username+password;
  * unless the owner started it with --unattended, a consent prompt must be accepted;
  * the GUI shows a permanent "session active" banner with a Disconnect button;
  * repeated wrong passwords lock the agent out for a while.
"""
import asyncio
import hmac
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from .capture import FrameEncoder, MssCapturer
from .crypto import (PBKDF2_ITERS, CryptoError, SecureChannel, ct_equal, derive_key,
                     make_proof)
from .protocol import MAX_MSG, T_CTRL, T_FRAME, T_INPUT, b64d, b64e, jb

log = logging.getLogger("rsupport.host")


def _clamp(v, lo=0.0, hi=1.0):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, v))


class HostAgent:
    def __init__(self, relay_url, username, password, *, relay_key="", unattended=False,
                 consent_cb=None, status_cb=None, capturer=None, injector=None, fps=12,
                 quality=60, max_width=1920, monitor=1, iters=PBKDF2_ITERS,
                 max_failures=5, lockout_s=60.0, ssl_ctx=None):
        self.relay_url, self.username, self.relay_key = relay_url, username, relay_key
        self.unattended, self.consent_cb, self.status_cb = unattended, consent_cb, status_cb
        self.fps, self.quality = fps, quality
        self.iters, self.max_failures, self.lockout_s = iters, max_failures, lockout_s
        self.ssl_ctx = ssl_ctx
        self.capturer = capturer or MssCapturer(monitor, max_width)
        self._injector = injector
        self._cap_exec = ThreadPoolExecutor(1, thread_name_prefix="capture")  # mss is thread-bound
        self._fail = 0
        self._locked_until = 0.0
        self._session = None
        self._stop = False
        self._task = None
        self.set_password(password)

    # -- public API ------------------------------------------------------
    def set_password(self, password: str):
        self._salt = os.urandom(16)
        self._key = derive_key(self.username, password, self._salt, self.iters)

    def _status(self, state: str, detail: str = ""):
        log.info("status: %s %s", state, detail)
        if self.status_cb:
            try:
                self.status_cb(state, detail)
            except Exception:
                log.exception("status callback failed")

    def request_disconnect(self):
        """Thread-safe: end the active session immediately (GUI 'Disconnect' button)."""
        loop = self._loop

        def _do():
            s = self._session
            if s:
                asyncio.ensure_future(s.stop())
        loop.call_soon_threadsafe(_do)

    async def run(self):
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.current_task()
        backoff = 1.0
        while not self._stop:
            try:
                self._status("connecting")
                kw = {}
                if self.relay_url.startswith("wss://") and self.ssl_ctx is not None:
                    kw["ssl"] = self.ssl_ctx
                async with connect(self.relay_url, max_size=MAX_MSG, compression=None,
                                   ping_interval=20, ping_timeout=20, open_timeout=15,
                                   **kw) as ws:
                    await self._connection(ws)
                    backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as e:  # network down, relay restarting, id in use...
                self._status("error", f"{type(e).__name__}: {e}")
            if self._stop:
                break
            await asyncio.sleep(backoff)
            backoff = min(30.0, backoff * 2)

    async def listen(self, bind="0.0.0.0", port=8765, ssl_ctx=None):
        """DIRECT mode - no relay: the technician connects straight to this computer.

        Returns the running server (caller keeps it alive with `await server.serve_forever()`).
        Traffic is still end-to-end encrypted with keys derived from the password handshake.
        """
        self._loop = asyncio.get_running_loop()

        async def handler(ws):
            if self._session is not None:           # one technician at a time
                await ws.close(1008, "busy")
                return
            session = _Session(self, ws, direct=True)
            self._session = session
            session.start()
            try:
                async for msg in ws:
                    if isinstance(msg, bytes):
                        session.feed(msg)
            except ConnectionClosed:
                pass
            finally:
                session.peer_gone = True
                await session.stop()
                if self._session is session:
                    self._session = None
                self._status("waiting", "Ready - waiting for the technician")

        server = await serve(handler, bind, port, ssl=ssl_ctx, max_size=MAX_MSG,
                             compression=None, ping_interval=20, ping_timeout=20)
        self.port = server.sockets[0].getsockname()[1]
        self._status("waiting", "Ready - waiting for the technician")
        return server

    def stop(self):
        self._stop = True
        if self._task:
            self._loop.call_soon_threadsafe(self._task.cancel)

    # -- relay connection ------------------------------------------------
    async def _connection(self, ws):
        await ws.send(json.dumps({"t": "hello", "role": "host", "id": self.username,
                                  "key": self.relay_key}))
        reply = json.loads(await asyncio.wait_for(ws.recv(), 10))
        if reply.get("t") != "ok":
            raise RuntimeError(f"relay refused: {reply.get('code', reply)}")
        self._status("waiting", "Ready - waiting for the technician")
        session = None
        try:
            async for msg in ws:
                if isinstance(msg, str):
                    c = json.loads(msg)
                    if c.get("t") == "peer_joined":
                        if session:
                            await session.stop()
                        session = _Session(self, ws)
                        self._session = session
                        session.start()
                    elif c.get("t") == "peer_left" and session:
                        session.peer_gone = True
                        await session.stop()
                        session = self._session = None
                        self._status("waiting", "Technician disconnected")
                elif session:
                    session.feed(msg)
        finally:
            if session:
                session.peer_gone = True
                await session.stop()
            self._session = None
        self._status("connecting", "Relay connection closed - reconnecting")

    # -- helpers used by sessions ------------------------------------------
    def _grab_encode(self, enc: FrameEncoder, quality: int):
        return enc.encode(self.capturer.grab(), quality)  # runs in the capture thread

    def injector(self):
        if self._injector is None:
            from .inputctl import PynputInjector
            self._injector = PynputInjector(lambda: self.capturer.geometry)
        return self._injector


class _Session:
    def __init__(self, agent: HostAgent, ws, direct: bool = False):
        self.a, self.ws, self.direct = agent, ws, direct
        self.q: asyncio.Queue = asyncio.Queue()
        self.chan = None
        self.peer_gone = False
        self._task = None
        self._stream_task = None
        self._send_lock = asyncio.Lock()

    def start(self):
        self._task = asyncio.create_task(self._run())

    def feed(self, msg):
        self.q.put_nowait(msg)

    async def stop(self):
        for t in (self._stream_task, self._task):
            if t and not t.done() and t is not asyncio.current_task():
                t.cancel()
        for t in (self._stream_task, self._task):
            if t and t is not asyncio.current_task():
                try:
                    await t
                except BaseException:
                    pass

    async def _send_plain(self, obj):
        await self.ws.send(jb(obj))

    async def _send_enc(self, typ: int, body: bytes):
        async with self._send_lock:  # seal+send must stay in order (implicit nonce counter)
            await self.ws.send(self.chan.seal(bytes([typ]) + body))

    async def _kick(self):
        try:
            if self.direct:
                await self.ws.close(1000, "session_ended")
            else:
                await self.ws.send(json.dumps({"t": "kick"}))
        except Exception:
            pass

    async def _run(self):
        a = self.a
        try:
            if time.monotonic() < a._locked_until:
                await self._send_plain({"t": "error", "code": "locked"})
                return
            a._status("authenticating", "Technician is connecting...")
            hn = os.urandom(16)
            await self._send_plain({"t": "challenge", "v": 1, "salt": b64e(a._salt),
                                    "hn": b64e(hn), "iters": a.iters})
            m = json.loads(await asyncio.wait_for(self.q.get(), 20))
            if m.get("t") != "auth":
                return
            vn, proof = b64d(m["vn"]), b64d(m["proof"])
            user_ok = ct_equal(str(m.get("user", "")).encode(), a.username.encode())
            proof_ok = len(vn) == 16 and ct_equal(proof, make_proof(a._key, b"viewer",
                                                                    a.username, hn, vn))
            if not (user_ok and proof_ok):
                a._fail += 1
                if a._fail >= a.max_failures:
                    a._locked_until = time.monotonic() + a.lockout_s
                    a._fail = 0
                    a._status("waiting", "Too many wrong passwords - temporarily locked")
                else:
                    a._status("waiting", "A connection attempt with a wrong password was rejected")
                await self._send_plain({"t": "auth_fail"})
                return
            a._fail = 0
            await self._send_plain({"t": "auth_ok",
                                    "proof": b64e(make_proof(a._key, b"host", a.username, hn, vn))})
            self.chan = SecureChannel(a._key, hn, vn, is_host=True)

            if not a.unattended:
                a._status("awaiting_consent", "Technician wants to connect")
                allowed = False
                if a.consent_cb:
                    try:
                        allowed = bool(await asyncio.wait_for(a.consent_cb({"user": a.username}), 90))
                    except asyncio.TimeoutError:
                        allowed = False
                if not allowed:
                    await self._send_enc(T_CTRL, jb({"t": "denied"}))
                    a._status("waiting", "Connection was not allowed")
                    return
            await self._send_enc(T_CTRL, jb({"t": "accepted"}))
            a._status("active", "Technician is connected and can see / control this computer")
            self._stream_task = asyncio.create_task(self._stream())

            while True:
                pt = self.chan.open(await self.q.get())
                typ, body = pt[0], pt[1:]
                if typ == T_INPUT:
                    self._handle_input(json.loads(body))
                elif typ == T_CTRL and json.loads(body).get("t") == "bye":
                    return
        except asyncio.CancelledError:
            pass
        except (CryptoError, ValueError, KeyError, asyncio.TimeoutError, ConnectionClosed) as e:
            log.warning("session ended: %s: %s", type(e).__name__, e)
        except Exception:
            log.exception("session crashed")
        finally:
            if self._stream_task and not self._stream_task.done():
                self._stream_task.cancel()
            if a._injector is not None:
                try:
                    a._injector.release_all()
                except Exception:
                    pass
            if not self.peer_gone:
                if self.chan is not None:
                    try:
                        await self._send_enc(T_CTRL, jb({"t": "bye"}))
                    except Exception:
                        pass
                await self._kick()
                a._status("waiting", "Session ended")

    async def _stream(self):
        a = self.a
        loop = asyncio.get_running_loop()
        enc, quality = FrameEncoder(), a.quality
        interval = 1.0 / a.fps
        while True:
            t0 = loop.time()
            try:
                payload = await loop.run_in_executor(a._cap_exec, a._grab_encode, enc, quality)
            except Exception as e:  # e.g. secure desktop / locked screen
                log.warning("capture failed: %s", e)
                await asyncio.sleep(1.0)
                continue
            if payload is not None:
                ts = loop.time()
                await self._send_enc(T_FRAME, payload)
                dt = loop.time() - ts
                if dt > 0.25:      # slow link: trade quality for latency
                    quality = max(30, quality - 10)
                elif dt < 0.05:
                    quality = min(a.quality, quality + 5)
            await asyncio.sleep(max(0.0, interval - (loop.time() - t0)))

    def _handle_input(self, m: dict):
        inj = self.a.injector()
        t = m.get("t")
        if t == "move":
            inj.move(_clamp(m.get("x")), _clamp(m.get("y")))
        elif t == "btn" and m.get("b") in ("left", "right", "middle"):
            inj.button(m["b"], bool(m.get("d")), _clamp(m.get("x")), _clamp(m.get("y")))
        elif t == "scroll":
            inj.scroll(int(_clamp(m.get("dx", 0), -20, 20)), int(_clamp(m.get("dy", 0), -20, 20)))
        elif t == "key" and isinstance(m.get("k"), str) and 0 < len(m["k"]) <= 32:
            inj.key(m["k"], bool(m.get("d")))
