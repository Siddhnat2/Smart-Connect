"""Mutual authentication + end-to-end encryption (relay never sees plaintext).

Handshake (all carried inside binary frames through the relay):

  host   -> viewer : challenge {salt, hn(16 random bytes), iters}
  viewer -> host   : auth {user, vn(16 random bytes), proof_v}
  host   -> viewer : auth_ok {proof_h}            (or auth_fail)

  key      = PBKDF2-HMAC-SHA256(user \\0 password, salt, iters)
  proof_v  = HMAC(key, "rs1|viewer|user|hn|vn")
  proof_h  = HMAC(key, "rs1|host|user|hn|vn")

Both sides prove knowledge of the password without sending it, and the viewer
verifies the host too (a rogue relay cannot impersonate the host).
Session keys: HKDF-SHA256(key, salt=hn|vn) -> two AES-256-GCM keys, one per direction.
Nonces are implicit 64-bit counters, so replayed / dropped / reordered frames fail.
"""
import hashlib
import hmac
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PBKDF2_ITERS = 300_000
MIN_ITERS = 100_000        # viewers refuse weaker parameters (tests monkeypatch this)
MAX_ITERS = 5_000_000


class CryptoError(Exception):
    pass


def derive_key(username: str, password: str, salt: bytes, iters: int = PBKDF2_ITERS) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", username.encode() + b"\0" + password.encode(), salt, iters, dklen=32
    )


def make_proof(key: bytes, role: bytes, username: str, hn: bytes, vn: bytes) -> bytes:
    msg = b"rs1|" + role + b"|" + username.encode() + b"|" + hn + b"|" + vn
    return hmac.new(key, msg, hashlib.sha256).digest()


def ct_equal(a: bytes, b: bytes) -> bool:
    return hmac.compare_digest(a, b)


class SecureChannel:
    def __init__(self, key: bytes, hn: bytes, vn: bytes, is_host: bool):
        okm = HKDF(
            algorithm=hashes.SHA256(), length=64, salt=hn + vn, info=b"rs1-session-keys"
        ).derive(key)
        k_h2v, k_v2h = okm[:32], okm[32:]
        self._tx = AESGCM(k_h2v if is_host else k_v2h)
        self._rx = AESGCM(k_v2h if is_host else k_h2v)
        self._tx_ctr = 0
        self._rx_ctr = 0

    @staticmethod
    def _nonce(ctr: int) -> bytes:
        return b"\0\0\0\0" + struct.pack(">Q", ctr)

    def seal(self, plaintext: bytes) -> bytes:
        ct = self._tx.encrypt(self._nonce(self._tx_ctr), plaintext, None)
        self._tx_ctr += 1
        return ct

    def open(self, ciphertext: bytes) -> bytes:
        try:
            pt = self._rx.decrypt(self._nonce(self._rx_ctr), ciphertext, None)
        except InvalidTag as e:
            raise CryptoError("bad frame (tampered, replayed or out of order)") from e
        self._rx_ctr += 1
        return pt
