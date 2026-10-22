"""Live test with a REAL screen: Xvfb virtual display + real mss capture + real pynput input.

Run:  python tests/live_xvfb_test.py        (needs Xvfb and python3-tk)
Starts: Xvfb, a target Tk window, the host agent (direct mode, no relay) (as separate processes),
then connects a viewer, checks the screenshot, clicks and types into the Tk window and
verifies the Tk window really received those events.
"""
import asyncio
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from rsupport.viewer import ViewerClient  # noqa: E402

DISPLAY = ":97"
W, H = 800, 600


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


TARGET = r'''
import json, sys, tkinter as tk
log = sys.argv[1]
root = tk.Tk(); root.geometry("400x300+0+0"); root.configure(bg="#d81b60")
c = tk.Canvas(root, bg="#d81b60", highlightthickness=0); c.pack(fill="both", expand=True)
def w(ev):
    open(log, "a").write(json.dumps(ev) + "\n")
c.bind("<ButtonPress-1>", lambda e: w({"click": [e.x_root, e.y_root]}))
c.bind("<ButtonPress-3>", lambda e: w({"rclick": [e.x_root, e.y_root]}))
root.bind("<KeyPress>", lambda e: w({"key": e.keysym}))
root.update(); root.focus_force(); c.focus_force()
root.mainloop()
'''


async def main():
    env = dict(os.environ, DISPLAY=DISPLAY, PYTHONPATH=ROOT)
    tmp = tempfile.mkdtemp()
    log = os.path.join(tmp, "events.log")
    open(log, "w").close()
    procs = []

    def spawn(*cmd, **kw):
        p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)
        procs.append(p); return p

    try:
        spawn("Xvfb", DISPLAY, "-screen", "0", f"{W}x{H}x24", "-nolisten", "tcp")
        time.sleep(1.5)
        spawn(sys.executable, "-c", TARGET, log)
        time.sleep(1.0)
        port = free_port()
        relay_url = f"ws://127.0.0.1:{port}"      # DIRECT mode: no relay process at all
        host = spawn(sys.executable, "-m", "rsupport", "host", "--unattended",
                     "--port", str(port),
                     "--username", "424242424", "--password", "Live-Test-Pw1", "--fps", "20")
        time.sleep(2.0)

        fb = {}
        got = asyncio.Event()

        def on_frame(sw, sh, x, y, w, h, jpeg):
            fb.setdefault("img", Image.new("RGB", (sw, sh)))
            fb["img"].paste(Image.open(io.BytesIO(jpeg)).convert("RGB"), (x, y))
            fb["size"] = (sw, sh); got.set()

        v = ViewerClient(relay_url, "424242424", "Live-Test-Pw1", direct=True, on_frame=on_frame)
        await v.connect()
        t = asyncio.create_task(v.run())
        await asyncio.wait_for(got.wait(), 15)
        await asyncio.sleep(1.0)
        img = fb["img"]
        print("stream size:", fb["size"])
        assert fb["size"] == (W, H)
        px = img.getpixel((200, 150))            # inside the pink Tk window
        assert abs(px[0] - 0xd8) < 40 and abs(px[1] - 0x1b) < 40, f"unexpected pixel {px}"
        assert img.getpixel((700, 500)) != px    # outside the window
        img.save(os.path.join(tmp, "live_snapshot.png"))
        print("OK  real screen captured and streamed")

        # real mouse: move + left click at (0.25, 0.25) => (200,150) on an 800x600 screen
        await v.send_input({"t": "move", "x": 0.25, "y": 0.25})
        await v.send_input({"t": "btn", "b": "left", "d": True, "x": 0.25, "y": 0.25})
        await v.send_input({"t": "btn", "b": "left", "d": False, "x": 0.25, "y": 0.25})
        await v.send_input({"t": "btn", "b": "right", "d": True, "x": 0.5, "y": 0.5})
        await v.send_input({"t": "btn", "b": "right", "d": False, "x": 0.5, "y": 0.5})
        # real keyboard
        for ch in "hi":
            await v.send_input({"t": "key", "k": ch, "d": True})
            await v.send_input({"t": "key", "k": ch, "d": False})
        await v.send_input({"t": "key", "k": "Return", "d": True})
        await v.send_input({"t": "key", "k": "Return", "d": False})
        await asyncio.sleep(1.5)
        events = [json.loads(l) for l in open(log) if l.strip()]
        print("events seen by the target window:", events)
        clicks = [e["click"] for e in events if "click" in e]
        assert clicks and abs(clicks[0][0] - 200) <= 2 and abs(clicks[0][1] - 150) <= 2, clicks
        assert any("rclick" in e for e in events)
        keys = [e["key"] for e in events if "key" in e]
        assert keys[:3] == ["h", "i", "Return"], keys
        print("OK  real mouse + keyboard injection works")

        # visual feedback: the window should now show up in a later frame too (stream still alive)
        await v.close(); await asyncio.wait_for(t, 5)
        print("ALL LIVE CHECKS PASSED")
        print("snapshot saved at", os.path.join(tmp, "live_snapshot.png"))
        return os.path.join(tmp, "live_snapshot.png")
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try: p.wait(3)
            except Exception: p.kill()


if __name__ == "__main__":
    asyncio.run(main())
