"""Rendezvous / relay server.

Run this on any machine with a public address (a small VPS is enough).
It pairs a *host* (the client's computer) with a *viewer* (you) by ID and blindly
forwards the binary frames between them.  It never sees passwords or screen data:
everything after the handshake is AES-GCM encrypted end to end.

    python -m rsupport relay --host 0.0.0.0 --port 8765 [--relay-key SECRET]
"""
import asyncio
import json
import hmac
import logging
import re
import time
from collections import defaultdict, deque
from http import HTTPStatus

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from .protocol import MAX_MSG

log = logging.getLogger("rsupport.relay")
ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class Room:
    __slots__ = ("host", "viewer")

    def __init__(self, host):
        self.host = host
        self.viewer = None


def _j(**kw) -> str:
    return json.dumps(kw)


async def _safe_send(ws, data):
    try:
        await ws.send(data)
        return True
    except ConnectionClosed:
        return False


class Relay:
    def __init__(self, relay_key: str = "", trust_proxy: bool = False,
                 max_fail: int = 10, window: float = 60.0):
        self.relay_key = relay_key
        self.trust_proxy = trust_proxy
        self.max_fail = max_fail
        self.window = window
        self.rooms: dict[str, Room] = {}
        self._fails: dict[str, deque] = defaultdict(deque)

    # -- helpers ---------------------------------------------------------
    def client_ip(self, ws) -> str:
        if self.trust_proxy:
            xff = ws.request.headers.get("X-Forwarded-For")
            if xff:
                return xff.split(",")[0].strip()
        ra = ws.remote_address
        return ra[0] if ra else "?"

    def _limited(self, ip: str) -> bool:
        dq = self._fails[ip]
        now = time.monotonic()
        while dq and now - dq[0] > self.window:
            dq.popleft()
        return len(dq) >= self.max_fail

    def _record_fail(self, ip: str):
        self._fails[ip].append(time.monotonic())

    async def _reject(self, ws, code: str):
        await _safe_send(ws, _j(t="error", code=code))
        await ws.close(1008, code)

    # -- main handler ----------------------------------------------------
    async def handler(self, ws):
        ip = self.client_ip(ws)
        try:
            raw = await asyncio.wait_for(ws.recv(), 10)
            hello = json.loads(raw) if isinstance(raw, str) else None
        except Exception:
            await ws.close(1008, "bad hello")
            return
        if (not isinstance(hello, dict) or hello.get("t") != "hello"
                or hello.get("role") not in ("host", "viewer")
                or not isinstance(hello.get("id"), str) or not ID_RE.match(hello["id"])):
            await self._reject(ws, "bad_hello")
            return
        if self.relay_key and not hmac.compare_digest(
                str(hello.get("key", "")).encode(), self.relay_key.encode()):
            self._record_fail(ip)
            await self._reject(ws, "bad_relay_key")
            return
        try:
            if hello["role"] == "host":
                await self._host(ws, hello["id"])
            else:
                await self._viewer(ws, hello["id"], ip)
        except ConnectionClosed:
            pass

    async def _host(self, ws, rid: str):
        if rid in self.rooms:
            await self._reject(ws, "id_in_use")
            return
        room = Room(ws)
        self.rooms[rid] = room
        await ws.send(_j(t="ok"))
        log.info("host online: %s", rid)
        try:
            async for msg in ws:
                if isinstance(msg, bytes):
                    v = room.viewer
                    if v is not None:
                        await _safe_send(v, msg)
                else:  # the only control message a host may send
                    try:
                        if json.loads(msg).get("t") == "kick" and room.viewer is not None:
                            await room.viewer.close(1000, "kicked_by_host")
                    except ValueError:
                        pass
        finally:
            if self.rooms.get(rid) is room:
                del self.rooms[rid]
            if room.viewer is not None:
                try:
                    await room.viewer.close(1001, "host_left")
                except Exception:
                    pass
            log.info("host offline: %s", rid)

    async def _viewer(self, ws, rid: str, ip: str):
        if self._limited(ip):
            await self._reject(ws, "rate_limited")
            return
        room = self.rooms.get(rid)
        if room is None:
            self._record_fail(ip)
            await self._reject(ws, "not_found")
            return
        if room.viewer is not None:
            await self._reject(ws, "busy")
            return
        room.viewer = ws
        log.info("viewer joined: %s", rid)
        try:
            await ws.send(_j(t="paired"))
            if not await _safe_send(room.host, _j(t="peer_joined")):
                return
            async for msg in ws:
                if isinstance(msg, bytes):
                    await _safe_send(room.host, msg)
                # text frames from viewers are ignored (cannot spoof relay messages)
        finally:
            if room.viewer is ws:
                room.viewer = None
                await _safe_send(room.host, _j(t="peer_left"))
            log.info("viewer left: %s", rid)


def _health(connection, request):
    if request.path == "/health":
        return connection.respond(HTTPStatus.OK, "ok\n")
    return None


async def serve_relay(host="0.0.0.0", port=8765, relay_key="", trust_proxy=False,
                      ssl_ctx=None, relay=None):
    relay = relay or Relay(relay_key, trust_proxy)
    server = await serve(relay.handler, host, port, ssl=ssl_ctx, max_size=MAX_MSG,
                         compression=None, ping_interval=20, ping_timeout=20,
                         process_request=_health)
    return relay, server


def main(args):
    import ssl
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    ctx = None
    if args.cert and args.key:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(args.cert, args.key)

    async def run():
        _, server = await serve_relay(args.host, args.port, args.relay_key,
                                      args.trust_proxy, ctx)
        log.info("relay listening on %s:%s (%s)", args.host, args.port,
                 "wss" if ctx else "ws - put TLS in front of me in production")
        await server.serve_forever()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
