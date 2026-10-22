"""python -m rsupport            -> the desktop app (both sides)
python -m rsupport host       -> headless console agent (no GUI), direct mode
python -m rsupport snapshot   -> headless: connect, save one screenshot, exit
python -m rsupport relay      -> optional relay server (not needed for direct mode)"""
import argparse
import asyncio
import io
import logging
import sys

from . import __version__
from .config import default_relay_key, gen_password, gen_username


def main(argv=None):
    p = argparse.ArgumentParser(prog="rsupport", description="Self-hosted remote support tool")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("selftest", help="build the UI offscreen and exit (used by CI)")

    h = sub.add_parser("host", help="headless console agent for the client computer (direct mode)")
    h.add_argument("--port", type=int, default=8765)
    h.add_argument("--relay", default=None, help="use a relay server instead of direct mode")
    h.add_argument("--relay-key", default=default_relay_key())
    h.add_argument("--username"); h.add_argument("--password")
    h.add_argument("--unattended", action="store_true", help="skip the 'Allow?' prompt (your own machines only)")
    h.add_argument("--fps", type=int, default=12)
    h.add_argument("--quality", type=int, default=60)
    h.add_argument("--max-width", type=int, default=1920)
    h.add_argument("--monitor", type=int, default=1)

    s = sub.add_parser("snapshot", help="connect, save one screenshot, exit")
    s.add_argument("--address", required=True, help="IP:port of the client computer")
    s.add_argument("--username", required=True); s.add_argument("--password", required=True)
    s.add_argument("--out", default="snapshot.png")

    r = sub.add_parser("relay", help="optional relay server")
    r.add_argument("--host", default="0.0.0.0"); r.add_argument("--port", type=int, default=8765)
    r.add_argument("--relay-key", default=default_relay_key())
    r.add_argument("--cert"); r.add_argument("--key")
    r.add_argument("--trust-proxy", action="store_true")

    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    if a.cmd is None:
        from .ui.app import run_app
        return run_app()

    if a.cmd == "selftest":
        from .ui.app import selftest
        return selftest()
    if a.cmd == "relay":
        from .relay import main as relay_main
        relay_main(a)
    elif a.cmd == "host":
        from .host import HostAgent
        from .inputctl import platform_init
        platform_init()
        user, pw = a.username or gen_username(), a.password or gen_password()
        print(f"\n  ID: {user}\n  Password: {pw}\n")

        async def consent(info):
            ans = await asyncio.get_running_loop().run_in_executor(
                None, input, "Allow the technician to connect? [y/N] ")
            return ans.strip().lower() == "y"
        agent = HostAgent(a.relay, user, pw, relay_key=a.relay_key, unattended=a.unattended,
                          consent_cb=consent, fps=a.fps, quality=a.quality,
                          max_width=a.max_width, monitor=a.monitor)

        async def go():
            if a.relay:
                await agent.run()
            else:
                server = await agent.listen("0.0.0.0", a.port)
                from .config import local_ips
                print("  Address:", ", ".join(f"{ip}:{agent.port}" for ip in local_ips()), "\n")
                await server.serve_forever()
        try:
            asyncio.run(go())
        except KeyboardInterrupt:
            pass
    elif a.cmd == "snapshot":
        from PIL import Image
        from .viewer import ViewerClient

        async def snap():
            fb, got = {}, asyncio.Event()

            def on_frame(sw, sh, x, y, w, h, jpeg):
                fb.setdefault("img", Image.new("RGB", (sw, sh)))
                fb["img"].paste(Image.open(io.BytesIO(jpeg)).convert("RGB"), (x, y))
                got.set()
            addr = a.address if "://" in a.address else "ws://" + a.address
            c = ViewerClient(addr, a.username, a.password, on_frame=on_frame, direct=True)
            await c.connect()
            t = asyncio.create_task(c.run())
            await asyncio.wait_for(got.wait(), 20)
            await asyncio.sleep(0.5)
            fb["img"].save(a.out)
            print("saved", a.out)
            await c.close()
            await t
        asyncio.run(snap())


if __name__ == "__main__":
    sys.exit(main())
