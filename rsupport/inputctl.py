"""Keyboard / mouse injection on the host (pynput)."""
import sys


def platform_init():
    """Make the process DPI-aware on Windows so capture and input coordinates match."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


class Injector:
    """Interface. x/y are normalised 0..1 within the shared monitor."""

    def move(self, x, y): ...
    def button(self, name, down, x, y): ...
    def scroll(self, dx, dy): ...
    def key(self, name, down): ...
    def release_all(self): ...


_SPECIAL = {
    "Return": "enter", "KP_Enter": "enter", "BackSpace": "backspace", "Tab": "tab",
    "Escape": "esc", "Delete": "delete", "Insert": "insert", "Home": "home", "End": "end",
    "Prior": "page_up", "Next": "page_down", "Up": "up", "Down": "down", "Left": "left",
    "Right": "right", "space": "space", "Caps_Lock": "caps_lock", "Menu": "menu",
    "Shift_L": "shift", "Shift_R": "shift_r", "Control_L": "ctrl", "Control_R": "ctrl_r",
    "Alt_L": "alt", "Alt_R": "alt_r", "Meta_L": "alt", "Meta_R": "alt_r",
    "Super_L": "cmd", "Super_R": "cmd_r", "Win_L": "cmd", "Win_R": "cmd_r",
    "Num_Lock": "num_lock", "Print": "print_screen", "Scroll_Lock": "scroll_lock",
}
_SPECIAL.update({f"F{i}": f"f{i}" for i in range(1, 13)})


class PynputInjector(Injector):
    def __init__(self, geometry_fn):
        from pynput import keyboard, mouse  # needs a display; imported lazily
        self._kb, self._ms = keyboard, mouse
        self._k = keyboard.Controller()
        self._m = mouse.Controller()
        self._geometry = geometry_fn
        self._down_keys = {}
        self._down_btns = set()

    def _btn(self, name):
        return {"left": self._ms.Button.left, "right": self._ms.Button.right,
                "middle": self._ms.Button.middle}[name]

    def _resolve(self, name):
        if len(name) == 1:
            return name
        attr = _SPECIAL.get(name)
        return getattr(self._kb.Key, attr, None) if attr else None

    def move(self, x, y):
        left, top, w, h = self._geometry()
        self._m.position = (left + int(x * (w - 1)), top + int(y * (h - 1)))

    def button(self, name, down, x, y):
        self.move(x, y)
        b = self._btn(name)
        if down:
            self._m.press(b)
            self._down_btns.add(name)
        else:
            self._m.release(b)
            self._down_btns.discard(name)

    def scroll(self, dx, dy):
        self._m.scroll(dx, dy)

    def key(self, name, down):
        k = self._resolve(name)
        if k is None:
            return
        if down:
            self._k.press(k)
            self._down_keys[name] = k
        else:
            self._k.release(k)
            self._down_keys.pop(name, None)

    def release_all(self):
        """Never leave a modifier or mouse button stuck when a session ends."""
        for k in list(self._down_keys.values()):
            try:
                self._k.release(k)
            except Exception:
                pass
        self._down_keys.clear()
        for n in list(self._down_btns):
            try:
                self._m.release(self._btn(n))
            except Exception:
                pass
        self._down_btns.clear()
