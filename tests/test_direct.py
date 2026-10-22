"""DIRECT mode: no relay at all - the viewer connects straight to the host."""
import asyncio
import contextlib
import io

import pytest
from PIL import Image

from rsupport.host import HostAgent
from rsupport.viewer import AuthError, DeniedError, ViewerClient, ViewerError

from .conftest import FakeCapturer, RecInjector


@contextlib.asynccontextmanager
async def direct_host(unattended=True, consent=None, **kw):
    cap, inj, statuses = FakeCapturer(), RecInjector(), []
    host = HostAgent(None, "314159265", "Direct-Pw-42", unattended=unattended, consent_cb=consent,
                     status_cb=lambda s, d="": statuses.append(s), capturer=cap, injector=inj,
                     fps=30, iters=1000, **kw)
    server = await host.listen("127.0.0.1", 0)
    url = f"ws://127.0.0.1:{host.port}"
    try:
        yield host, url, inj, statuses
    finally:
        server.close()
        await server.wait_closed()


def viewer(url, pw="Direct-Pw-42", user="314159265", on_frame=None):
    return ViewerClient(url, user, pw, direct=True, on_frame=on_frame)


def run(c):
    return asyncio.run(asyncio.wait_for(c, 40))


def test_direct_connect_frames_input_and_reconnect():
    async def go():
        async with direct_host() as (host, url, inj, st):
            got, fb = asyncio.Event(), {}

            def on_frame(sw, sh, x, y, w, h, jpeg):
                fb.setdefault("i", Image.new("RGB", (sw, sh)))
                fb["i"].paste(Image.open(io.BytesIO(jpeg)).convert("RGB"), (x, y)); got.set()
            v = viewer(url, on_frame=on_frame)
            await v.connect()
            t = asyncio.create_task(v.run())
            await asyncio.wait_for(got.wait(), 8)
            assert fb["i"].getpixel((20, 20))[0] > 200            # the red square arrived
            await v.send_input({"t": "move", "x": 0.5, "y": 0.5})
            await v.send_input({"t": "key", "k": "a", "d": True})
            await asyncio.sleep(0.4)
            assert ("move", 0.5, 0.5) in inj.calls and ("key", "a", True) in inj.calls
            await v.close(); await asyncio.wait_for(t, 5)
            await asyncio.sleep(0.3)
            assert ("release_all",) in inj.calls
            v2 = viewer(url, on_frame=lambda *a: None)             # host accepts a new session
            await v2.connect(); await v2.close()
    run(go())


def test_direct_wrong_password_lockout_busy_and_consent():
    async def go():
        async with direct_host(max_failures=3, lockout_s=30) as (host, url, inj, st):
            for _ in range(3):
                with pytest.raises(AuthError):
                    await viewer(url, pw="wrong").connect()
            with pytest.raises(ViewerError) as e:
                await viewer(url).connect()
            assert "locked" in str(e.value).lower()
            assert "active" not in st

        async def deny(info): return False
        async with direct_host(unattended=False, consent=deny) as (host, url, inj, st):
            with pytest.raises(DeniedError):
                await viewer(url).connect()

        async with direct_host() as (host, url, inj, st):
            v1 = viewer(url, on_frame=lambda *a: None)
            await v1.connect()
            t = asyncio.create_task(v1.run())
            with pytest.raises(ViewerError) as e:
                await viewer(url).connect()
            assert "already" in str(e.value)
            host.request_disconnect()                              # owner hits DISCONNECT NOW
            await asyncio.wait_for(t, 8)
            assert "ended" in v1.close_reason.lower()
    run(go())
