"""The stdlib WebSocket layer (`spacetaxi.ws`): handshake key, frames, a live server and client."""

import asyncio
import urllib.request

import pytest

from spacetaxi import ws


def test_accept_key_rfc_example():
    assert ws.accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


async def decode(data: bytes):
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return await ws.read_frame(reader)


@pytest.mark.parametrize("size", [10, 200, 70_000])
@pytest.mark.parametrize("mask", [False, True])
def test_frame_round_trip(size, mask):
    payload = bytes(i * 7 % 256 for i in range(size))
    data = ws.encode_frame(ws.TEXT, payload, mask=mask)
    header = 2 + (2 if 126 <= size < 65536 else 8 if size >= 65536 else 0) + (4 if mask else 0)
    assert len(data) == header + size
    if mask:
        assert data[header:] != payload
    fin, opcode, out, masked = asyncio.run(decode(data))
    assert (fin, opcode, out, masked) == (True, ws.TEXT, payload, mask)


def test_rfc_unmasked_and_masked_hello():
    # RFC 6455 section 5.7 examples.
    assert asyncio.run(decode(bytes.fromhex("810548656c6c6f")))[2] == b"Hello"
    assert asyncio.run(decode(bytes.fromhex("818537fa213d7f9f4d5158")))[2] == b"Hello"


async def echo_server():
    async def echo(sock, path):
        while (msg := await sock.recv()) is not None:
            await sock.send(msg)

    return await ws.serve({"/echo": echo}, lambda p: (200, "text/plain", b"hi") if p == "/"
                          else None, "127.0.0.1", 0)


def test_server_and_client_talk():
    async def main():
        server = await echo_server()
        port = server.sockets[0].getsockname()[1]
        sock = await ws.connect(f"ws://127.0.0.1:{port}/echo")
        sent = ["x" * n for n in (10, 200, 70_000)] + ["ünïcode ✓"]
        for text in sent:
            await sock.send(text)
            assert await sock.recv() == text
        await sock.send(b"\x00\x01")
        assert await sock.recv() == b"\x00\x01"
        await sock.ping(b"p")  # the server answers with a pong, which recv skips
        await sock.send("after ping")
        assert await sock.recv() == "after ping"
        # a fragmented message from the client
        sock.writer.write(ws.encode_frame(ws.TEXT, b"frag", mask=True, fin=False)
                          + ws.encode_frame(ws.CONT, b"ments", mask=True))
        assert await sock.recv() == "fragments"
        body = await asyncio.to_thread(
            lambda: urllib.request.urlopen(f"http://127.0.0.1:{port}/").read())
        assert body == b"hi"
        await sock.close()
        assert sock.closed
        with pytest.raises(ConnectionError):
            await sock.send("closed")
        server.close()
        await server.wait_closed()

    asyncio.run(main())


def test_server_rejects_unmasked_client_frames_and_unknown_paths():
    async def main():
        server = await echo_server()
        port = server.sockets[0].getsockname()[1]
        with pytest.raises(ConnectionError):
            await ws.connect(f"ws://127.0.0.1:{port}/nope")
        sock = await ws.connect(f"ws://127.0.0.1:{port}/echo")
        sock.client = False  # now it sends unmasked frames: a protocol error for the server
        await sock.send("bad")
        sock.client = True
        assert await sock.recv() is None
        server.close()
        await server.wait_closed()

    asyncio.run(main())
