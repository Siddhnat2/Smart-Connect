"""Wire-format constants and helpers shared by relay, host and viewer.

Transport: WebSocket (wss:// in production).

Relay <-> peer control messages are *text* frames (JSON).
Peer <-> peer traffic is always *binary* frames, which the relay forwards verbatim
and can never read once the handshake is complete (AES-256-GCM, see crypto.py).
A text frame sent by a viewer is ignored by the relay, so a peer can never
spoof relay control messages.

Inner message types (first byte of the decrypted payload):
    0x01  FRAME   host -> viewer   header ">HHHHHH" (stream_w, stream_h, x, y, w, h) + JPEG
    0x02  INPUT   viewer -> host   JSON (mouse / keyboard events, coordinates normalised 0..1)
    0x03  CTRL    both             JSON (accepted, bye, ...)
"""
import base64
import json
import struct

T_FRAME = 1
T_INPUT = 2
T_CTRL = 3

FRAME_HDR = struct.Struct(">HHHHHH")
MAX_MSG = 16 * 1024 * 1024  # 16 MiB websocket message cap


def jb(obj) -> bytes:
    return json.dumps(obj, separators=(",", ":")).encode()


def b64e(b: bytes) -> str:
    return base64.b64encode(b).decode()


def b64d(s: str) -> bytes:
    return base64.b64decode(s.encode(), validate=True)
