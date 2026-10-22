import asyncio
import io

import pytest
from PIL import Image

from rsupport.crypto import CryptoError, SecureChannel
from rsupport.viewer import AuthError, DeniedError, RelayError, ViewerError

from .conftest import stack


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 40))


class FrameSink:
    def __init__(self):
        self.frames = []
        self.fb = None
        self.event = asyncio.Event()

    def __call__(self, sw, sh, x, y, w, h, jpeg):
        if self.fb is None:
            self.fb = Image.new("RGB", (sw, sh))
        self.fb.paste(Image.open(io.BytesIO(jpeg)).convert("RGB"), (x, y))
        self.frames.append((sw, sh, x, y, w, h, len(jpeg)))
        self.event.set()

    async def wait(self, n=1):
        while len(self.frames) < n:
            self.event.clear()
            await asyncio.wait_for(self.event.wait(), 10)


def near(px, rgb, tol=40):
    return all(abs(a - b) <= tol for a, b in zip(px, rgb))


# ---------------------------------------------------------------- happy path
def test_login_frames_and_delta_updates():
    async def go():
        async with stack() as s:
            sink = FrameSink()
            v = s.viewer(on_frame=sink)
            await v.connect()
            t = asyncio.create_task(v.run())
            await sink.wait(1)
            sw, sh, x, y, w, h, _ = sink.frames[0]
            assert (sw, sh, x, y, w, h) == (320, 200, 0, 0, 320, 200)   # first frame = full
            assert near(sink.fb.getpixel((20, 20)), (255, 0, 0))         # red square
            assert near(sink.fb.getpixel((200, 150)), (20, 20, 30))

            n = len(sink.frames)
            await asyncio.sleep(0.4)
            assert len(sink.frames) == n                                  # static screen => no traffic

            s.cap.sq = (200, 120, "lime")                                 # change the screen
            await sink.wait(n + 1)
            await asyncio.sleep(0.2)
            partial = [f for f in sink.frames[n:] if f[4] * f[5] < 320 * 200]
            assert partial, "expected a dirty-rect (partial) update"
            assert near(sink.fb.getpixel((220, 140)), (0, 255, 0))
            assert near(sink.fb.getpixel((20, 20)), (20, 20, 30))         # old square erased
            await v.close()
            await asyncio.wait_for(t, 5)
    run(go())


def test_input_events_reach_injector_and_are_sanitised():
    async def go():
        async with stack() as s:
            sink = FrameSink()
            v = s.viewer(on_frame=sink)
            await v.connect()
            t = asyncio.create_task(v.run())
            await sink.wait(1)
            await v.send_input({"t": "move", "x": 0.5, "y": 0.25})
            await v.send_input({"t": "btn", "b": "left", "d": True, "x": 0.1, "y": 0.2})
            await v.send_input({"t": "btn", "b": "left", "d": False, "x": 0.1, "y": 0.2})
            await v.send_input({"t": "scroll", "dx": 0, "dy": -3})
            await v.send_input({"t": "key", "k": "a", "d": True})
            await v.send_input({"t": "key", "k": "Return", "d": False})
            await v.send_input({"t": "move", "x": 99, "y": -5})             # clamped
            await v.send_input({"t": "btn", "b": "evil", "d": True})        # ignored
            await v.send_input({"t": "key", "k": "x" * 100, "d": True})     # ignored
            await v.send_input({"t": "scroll", "dx": 0, "dy": 10 ** 6})     # clamped
            await asyncio.sleep(0.5)
            c = s.inj.calls
            assert ("move", 0.5, 0.25) in c
            assert ("btn", "left", True, 0.1, 0.2) in c
            assert ("btn", "left", False, 0.1, 0.2) in c
            assert ("scroll", 0, -3) in c
            assert ("key", "a", True) in c and ("key", "Return", False) in c
            assert ("move", 1.0, 0.0) in c
            assert not any(x[0] == "btn" and x[1] == "evil" for x in c)
            assert ("scroll", 0, 20) in c
            assert not any(x[0] == "key" and len(x[1]) > 32 for x in c)
            await v.close()
            await asyncio.wait_for(t, 5)
            await asyncio.sleep(0.2)
            assert ("release_all",) in s.inj.calls                          # no stuck keys
    run(go())


# ------------------------------------------------------------------ auth
def test_wrong_password_and_wrong_username_rejected():
    async def go():
        async with stack() as s:
            with pytest.raises(AuthError):
                await s.viewer(password="nope").connect()
            with pytest.raises(AuthError):
                await s.viewer(username="123456789", password="").connect()
            # host never streamed anything / never reached 'active'
            assert "active" not in s.statuses
            # correct credentials still work afterwards
            v = s.viewer(on_frame=FrameSink())
            await v.connect()
            await v.close()
    run(go())


def test_lockout_after_repeated_failures():
    async def go():
        async with stack(max_failures=3, lockout_s=30) as s:
            for _ in range(3):
                with pytest.raises(AuthError):
                    await s.viewer(password="bad").connect()
            with pytest.raises(ViewerError) as e:                            # even the right password
                await s.viewer().connect()
            assert "locked" in str(e.value).lower()
    run(go())


def test_consent_denied_and_accepted():
    async def go():
        async def deny(info):
            return False
        async with stack(unattended=False, consent=deny) as s:
            with pytest.raises(DeniedError):
                await s.viewer().connect()
            assert "active" not in s.statuses

        async def allow(info):
            return True
        async with stack(unattended=False, consent=allow) as s:
            sink = FrameSink()
            v = s.viewer(on_frame=sink)
            await v.connect()
            t = asyncio.create_task(v.run())
            await sink.wait(1)
            await v.close()
            await asyncio.wait_for(t, 5)

        # no consent callback + not unattended => safe default is DENY
        async with stack(unattended=False, consent=None) as s:
            with pytest.raises(DeniedError):
                await s.viewer().connect()
    run(go())


# ------------------------------------------------------------------ relay
def test_relay_errors_rate_limit_and_busy():
    async def go():
        async with stack() as s:
            bad = s.viewer(username="999999999")
            with pytest.raises(RelayError) as e:
                await bad.connect()
            assert e.value.code == "not_found"

            v1 = s.viewer(on_frame=FrameSink())
            await v1.connect()
            t = asyncio.create_task(v1.run())
            with pytest.raises(RelayError) as e:
                await s.viewer().connect()
            assert e.value.code == "busy"
            await v1.close()
            await asyncio.wait_for(t, 5)
            await asyncio.sleep(0.3)
            v2 = s.viewer(on_frame=FrameSink())                              # slot is free again
            await v2.connect()
            await v2.close()

            codes = []
            for _ in range(12):                                              # brute-force ID scan
                try:
                    await s.viewer(username="000000000").connect()
                except RelayError as ex:
                    codes.append(ex.code)
            assert "rate_limited" in codes
    run(go())


def test_relay_key_enforced():
    async def go():
        async with stack(relay_key="topsecret") as s:
            with pytest.raises(RelayError) as e:
                await s.viewer(key="wrong").connect()
            assert e.value.code == "bad_relay_key"
            v = s.viewer(key="topsecret", on_frame=FrameSink())
            await v.connect()
            await v.close()
    run(go())


def test_host_disappearing_ends_viewer_run():
    async def go():
        async with stack() as s:
            sink = FrameSink()
            v = s.viewer(on_frame=sink)
            await v.connect()
            t = asyncio.create_task(v.run())
            await sink.wait(1)
            s.host.stop()
            await asyncio.wait_for(t, 8)       # run() returns instead of hanging
    run(go())


def test_host_disconnect_button_ends_session():
    async def go():
        async with stack() as s:
            sink = FrameSink()
            v = s.viewer(on_frame=sink)
            await v.connect()
            t = asyncio.create_task(v.run())
            await sink.wait(1)
            s.host.request_disconnect()
            await asyncio.wait_for(t, 8)
            assert "ended" in v.close_reason.lower()
            await asyncio.sleep(0.3)
            v2 = s.viewer(on_frame=FrameSink())                              # can reconnect
            await v2.connect()
            await v2.close()
    run(go())


# ---------------------------------------------------------------- crypto
def test_secure_channel_tamper_replay_and_direction():
    key, hn, vn = b"k" * 32, b"h" * 16, b"v" * 16
    host, view = SecureChannel(key, hn, vn, True), SecureChannel(key, hn, vn, False)
    c1 = host.seal(b"hello")
    assert view.open(c1) == b"hello"
    with pytest.raises(CryptoError):                       # replay
        view.open(c1)
    c2 = host.seal(b"second")
    bad = bytearray(c2); bad[0] ^= 1
    with pytest.raises(CryptoError):                       # tamper
        view.open(bytes(bad))
    # a message the host sealed cannot be reflected back to the host
    host2 = SecureChannel(key, hn, vn, True)
    with pytest.raises(CryptoError):
        host2.open(host2.seal(b"x"))
    # different password => different key => cannot decrypt
    other = SecureChannel(b"z" * 32, hn, vn, False)
    with pytest.raises(CryptoError):
        other.open(host.seal(b"secret"))


def test_relay_cannot_read_screen_data():
    """Everything the relay forwards after the handshake is ciphertext."""
    async def go():
        async with stack() as s:
            seen = []
            orig = s.relay.handler

            sink = FrameSink()
            v = s.viewer(on_frame=sink)
            # sniff by wrapping the viewer socket's recv
            await v.connect()
            real_recv = v.ws.recv

            async def spy(*a, **k):
                m = await real_recv(*a, **k)
                seen.append(m)
                return m
            v.ws.recv = spy
            t = asyncio.create_task(v.run())
            await sink.wait(1)
            await v.close()
            await asyncio.wait_for(t, 5)
            blob = b"".join(m for m in seen if isinstance(m, bytes))
            assert blob and b"\xff\xd8\xff" not in blob          # no raw JPEG header on the wire
            assert s.password.encode() not in blob
    run(go())
