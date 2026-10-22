"""Viewer core (technician side): connect, authenticate, receive frames, send input.

GUI-free so it can be tested headlessly; viewer_gui.py wraps it in a Tk window.
"""
import asyncio
import json
import os

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from . import crypto
from .crypto import CryptoError, SecureChannel, ct_equal, derive_key, make_proof
from .protocol import (FRAME_HDR, MAX_MSG, T_CTRL, T_FRAME, T_INPUT, b64d, b64e, jb)


class ViewerError(Exception):
    pass


class RelayError(ViewerError):
    MESSAGES = {
        "not_found": "No computer with that ID is online right now.",
        "busy": "That computer already has a technician connected.",
        "rate_limited": "Too many attempts - wait a minute and try again.",
        "bad_relay_key": "The relay rejected the relay key.",
        "id_in_use": "ID already in use.",
        "bad_hello": "Invalid ID.",
    }

    def __init__(self, code):
        self.code = code
        super().__init__(self.MESSAGES.get(code, f"Relay error: {code}"))


class AuthError(ViewerError):
    pass


class DeniedError(ViewerError):
    pass


class ViewerClient:
    def __init__(self, relay_url, username, password, *, relay_key="", on_frame=None,
                 ssl_ctx=None, consent_timeout=120.0, direct=False):
        self.relay_url, self.username, self.password = relay_url, username, password
        self.relay_key, self.on_frame, self.ssl_ctx = relay_key, on_frame, ssl_ctx
        self.consent_timeout = consent_timeout
        self.direct = direct   # True: connecting straight to the host, no relay
        self.ws = None
        self.chan = None
        self._send_lock = asyncio.Lock()
        self.close_reason = ""

    async def _recv_json(self, timeout):
        msg = await asyncio.wait_for(self.ws.recv(), timeout)
        return msg, json.loads(msg)

    async def connect(self):
        kw = {}
        if self.relay_url.startswith("wss://") and self.ssl_ctx is not None:
            kw["ssl"] = self.ssl_ctx
        self.ws = await connect(self.relay_url, max_size=MAX_MSG, compression=None,
                                ping_interval=20, ping_timeout=20, open_timeout=15, **kw)
        try:
            await self._handshake()
        except BaseException:
            await self.close()
            raise

    async def _handshake(self):
        ws = self.ws
        try:
            if not self.direct:
                await ws.send(json.dumps({"t": "hello", "role": "viewer", "id": self.username,
                                          "key": self.relay_key}))
                _, m = await self._recv_json(15)
                if m.get("t") == "error":
                    raise RelayError(m.get("code"))
                if m.get("t") != "paired":
                    raise ViewerError("unexpected relay reply")

            raw, ch = await self._recv_json(20)          # challenge from the host
            if isinstance(raw, str):                      # relay text => something went wrong
                raise ViewerError("The computer went offline.")
            if ch.get("t") == "error":
                raise ViewerError("The computer is temporarily locked after failed attempts."
                                  if ch.get("code") == "locked" else f"Host error: {ch.get('code')}")
            iters = int(ch["iters"])
            if not (crypto.MIN_ITERS <= iters <= crypto.MAX_ITERS):
                raise ViewerError("Host sent unsafe key-derivation parameters.")
            salt, hn = b64d(ch["salt"]), b64d(ch["hn"])
            if len(hn) != 16:
                raise ViewerError("Malformed challenge.")
            key = await asyncio.to_thread(derive_key, self.username, self.password, salt, iters)
            vn = os.urandom(16)
            await ws.send(jb({"t": "auth", "user": self.username, "vn": b64e(vn),
                              "proof": b64e(make_proof(key, b"viewer", self.username, hn, vn))}))

            _, r = await self._recv_json(20)
            if r.get("t") == "auth_fail":
                raise AuthError("Wrong ID or password.")
            if r.get("t") != "auth_ok":
                raise ViewerError(f"Unexpected reply: {r.get('t')}")
            if not ct_equal(b64d(r["proof"]), make_proof(key, b"host", self.username, hn, vn)):
                raise AuthError("The other side could not prove it knows the password.")
            self.chan = SecureChannel(key, hn, vn, is_host=False)

            pt = self.chan.open(await asyncio.wait_for(ws.recv(), self.consent_timeout))
            msg = json.loads(pt[1:])
            if msg.get("t") == "denied":
                raise DeniedError("The person at the computer did not allow the connection.")
            if msg.get("t") != "accepted":
                raise ViewerError("Unexpected reply while waiting for permission.")
        except asyncio.TimeoutError:
            raise ViewerError("Timed out waiting for the computer to respond.")
        except ConnectionClosed as e:
            busy = getattr(getattr(e, "rcvd", None), "reason", "") == "busy"
            raise ViewerError("That computer already has a technician connected." if busy
                              else "Connection closed during setup.")
        except (KeyError, ValueError, CryptoError) as e:
            raise ViewerError(f"Malformed response: {e}")

    async def run(self):
        """Receive loop. Returns when the session ends."""
        try:
            async for raw in self.ws:
                if isinstance(raw, str):
                    continue
                pt = self.chan.open(raw)
                typ, body = pt[0], pt[1:]
                if typ == T_FRAME and self.on_frame:
                    hdr = FRAME_HDR.unpack_from(body)
                    self.on_frame(*hdr, body[FRAME_HDR.size:])
                elif typ == T_CTRL and json.loads(body).get("t") == "bye":
                    self.close_reason = "The session was ended by the other side."
                    return
        except ConnectionClosed:
            self.close_reason = self.close_reason or "Connection lost."
        except CryptoError:
            self.close_reason = "Security error: a corrupted frame was received."
        finally:
            await self.close()

    async def send_input(self, event: dict):
        if self.ws is None or self.chan is None:
            return
        async with self._send_lock:
            await self.ws.send(self.chan.seal(bytes([T_INPUT]) + jb(event)))

    async def close(self):
        if self.ws is not None:
            try:
                if self.chan is not None:
                    async with self._send_lock:
                        await self.ws.send(self.chan.seal(bytes([T_CTRL]) + jb({"t": "bye"})))
            except Exception:
                pass
            try:
                await self.ws.close()
            except Exception:
                pass
