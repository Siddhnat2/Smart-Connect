"""wss:// works, and clients REJECT an untrusted certificate (no silent downgrade)."""
import asyncio
import datetime
import ipaddress
import ssl
import tempfile

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from rsupport.host import HostAgent
from rsupport.relay import serve_relay
from rsupport.viewer import ViewerClient

from .conftest import FakeCapturer, RecInjector


def make_cert(tmp):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .sign(key, hashes.SHA256()))
    crt, k = f"{tmp}/c.pem", f"{tmp}/k.pem"
    open(crt, "wb").write(cert.public_bytes(serialization.Encoding.PEM))
    open(k, "wb").write(key.private_bytes(serialization.Encoding.PEM,
                                          serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    return crt, k


def test_wss_with_trusted_cert_and_rejection_of_untrusted():
    async def go():
        tmp = tempfile.mkdtemp()
        crt, key = make_cert(tmp)
        sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        sctx.load_cert_chain(crt, key)
        relay, server = await serve_relay("localhost", 0, "", False, sctx)
        port = server.sockets[0].getsockname()[1]
        url = f"wss://localhost:{port}"
        trust = ssl.create_default_context(cafile=crt)

        ready = asyncio.Event()
        host = HostAgent(url, "111222333", "pw-pw-pw-1", unattended=True, capturer=FakeCapturer(),
                         injector=RecInjector(), iters=1000, ssl_ctx=trust,
                         status_cb=lambda s, d="": ready.set() if s == "waiting" else None)
        task = asyncio.create_task(host.run())
        await asyncio.wait_for(ready.wait(), 8)

        got = asyncio.Event()
        v = ViewerClient(url, "111222333", "pw-pw-pw-1", ssl_ctx=trust,
                         on_frame=lambda *a: got.set())
        await v.connect()
        t = asyncio.create_task(v.run())
        await asyncio.wait_for(got.wait(), 8)
        await v.close(); await asyncio.wait_for(t, 5)

        # default trust store does not know our self-signed CA => must fail, not downgrade
        with pytest.raises(ssl.SSLError):
            await ViewerClient(url, "111222333", "pw-pw-pw-1").connect()

        host.stop(); task.cancel()
        try:
            await task
        except BaseException:
            pass
        server.close(); await server.wait_closed()
    asyncio.run(asyncio.wait_for(go(), 40))
