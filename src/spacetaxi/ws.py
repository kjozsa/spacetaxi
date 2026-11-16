"""A small WebSocket (RFC 6455) server and client on `asyncio`, standard library only.

Text and binary frames of any length, fragmented messages, masking from clients, ping/pong and
the close handshake. The server also answers plain HTTP `GET`s on the same port, so one port
serves both the pages and the sockets.

Server: `await serve(ws_routes, http_handler, host, port)`, where `ws_routes` maps a path to an
`async def handler(ws, path)` and `http_handler(path)` returns `(status, content_type, body)` or
None for a 404. Client: `ws = await connect("ws://host:port/bot")`. Both sides talk through a
`WebSocket`: `await ws.send(text)`, `await ws.recv()` (None once the socket is closed),
`await ws.close()`.
"""

import asyncio
import base64
import hashlib
import os
import struct
from urllib.parse import urlsplit

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

CONT, TEXT, BINARY, CLOSE, PING, PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA

MAX_MESSAGE = 16 * 1024 * 1024  # refuse anything bigger, so a bad peer cannot eat the memory

REASONS = {101: "Switching Protocols", 200: "OK", 400: "Bad Request", 404: "Not Found",
           426: "Upgrade Required"}


class WebSocketError(Exception):
    """A protocol violation by the peer, or a frame too big."""


def accept_key(key: str) -> str:
    """The `Sec-WebSocket-Accept` value for a `Sec-WebSocket-Key`."""
    digest = hashlib.sha1((key + GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def apply_mask(mask: bytes, data: bytes) -> bytes:
    """XOR `data` with the 4-byte `mask`, repeated (masking and unmasking are the same)."""
    if not data:
        return b""
    n = len(data)
    repeated = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(repeated, "big")).to_bytes(n, "big")


def encode_frame(opcode: int, payload: bytes, mask: bool, fin: bool = True) -> bytes:
    """One frame. Clients must mask (`mask=True`), servers must not."""
    head = bytearray([(0x80 if fin else 0) | opcode])
    n = len(payload)
    bit = 0x80 if mask else 0
    if n < 126:
        head.append(bit | n)
    elif n < 1 << 16:
        head.append(bit | 126)
        head += struct.pack("!H", n)
    else:
        head.append(bit | 127)
        head += struct.pack("!Q", n)
    if mask:
        key = os.urandom(4)
        return bytes(head) + key + apply_mask(key, payload)
    return bytes(head) + payload


async def read_frame(reader: asyncio.StreamReader) -> tuple[bool, int, bytes, bool]:
    """The next frame as `(fin, opcode, payload, masked)`, the payload unmasked.
    Raises `asyncio.IncompleteReadError` when the stream ends."""
    b0, b1 = await reader.readexactly(2)
    if b0 & 0x70:
        raise WebSocketError("reserved bits set")
    fin, opcode = bool(b0 & 0x80), b0 & 0x0F
    masked, n = bool(b1 & 0x80), b1 & 0x7F
    if n == 126:
        (n,) = struct.unpack("!H", await reader.readexactly(2))
    elif n == 127:
        (n,) = struct.unpack("!Q", await reader.readexactly(8))
    if n > MAX_MESSAGE:
        raise WebSocketError(f"frame of {n} bytes is too big")
    if opcode >= 0x8 and (n > 125 or not fin):
        raise WebSocketError("bad control frame")
    key = await reader.readexactly(4) if masked else None
    payload = await reader.readexactly(n)
    if key:
        payload = apply_mask(key, payload)
    return fin, opcode, payload, masked


class WebSocket:
    """One open WebSocket. `client=True` on the client side: its frames are masked, and it
    expects unmasked frames from the server (and the other way round)."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 client: bool, path: str = "/"):
        self.reader = reader
        self.writer = writer
        self.client = client
        self.path = path
        self.closed = False
        self._close_sent = False
        self._lock = asyncio.Lock()

    @property
    def peer(self) -> str:
        peer = self.writer.get_extra_info("peername")
        return f"{peer[0]}:{peer[1]}" if peer else "?"

    async def _send_frame(self, opcode: int, payload: bytes) -> None:
        async with self._lock:
            self.writer.write(encode_frame(opcode, payload, mask=self.client))
            await self.writer.drain()

    async def send(self, message: str | bytes) -> None:
        """Send one message: a text frame for `str`, a binary frame for `bytes`.
        Raises `ConnectionError` once the socket is closed."""
        if self.closed or self._close_sent:
            raise ConnectionError("WebSocket is closed")
        if isinstance(message, str):
            await self._send_frame(TEXT, message.encode("utf-8"))
        else:
            await self._send_frame(BINARY, bytes(message))

    async def ping(self, payload: bytes = b"") -> None:
        await self._send_frame(PING, payload)

    async def recv(self) -> str | bytes | None:
        """The next message (text as `str`, binary as `bytes`), answering pings on the way;
        None once the socket is closed."""
        parts: list[bytes] = []
        kind = None
        while not self.closed:
            try:
                fin, opcode, payload, masked = await read_frame(self.reader)
                if masked == self.client:
                    raise WebSocketError("frame masked the wrong way")
            except (asyncio.IncompleteReadError, ConnectionError, WebSocketError, OSError) as e:
                await self._abort(1002 if isinstance(e, WebSocketError) else None)
                return None
            if opcode == PING:
                try:
                    await self._send_frame(PONG, payload)
                except (ConnectionError, OSError):
                    await self._abort(None)
                    return None
            elif opcode == PONG:
                pass
            elif opcode == CLOSE:
                code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else 1000
                await self._finish_close(code)
                return None
            elif opcode in (TEXT, BINARY) and kind is None:
                kind, parts = opcode, [payload]
            elif opcode == CONT and kind is not None:
                parts.append(payload)
            else:
                await self._abort(1002)
                return None
            if kind is not None and fin and opcode in (TEXT, BINARY, CONT):
                data = b"".join(parts)
                if kind == BINARY:
                    return data
                try:
                    return data.decode("utf-8")
                except UnicodeDecodeError:
                    await self._abort(1007)
                    return None
            if sum(len(p) for p in parts) > MAX_MESSAGE:
                await self._abort(1009)
                return None
        return None

    async def _finish_close(self, code: int) -> None:
        """The peer sent a close: echo it (if we have not sent ours) and drop the connection."""
        if not self._close_sent:
            self._close_sent = True
            try:
                await self._send_frame(CLOSE, struct.pack("!H", code))
            except (ConnectionError, OSError):
                pass
        await self._abort(None)

    async def _abort(self, code: int | None) -> None:
        if code is not None and not self._close_sent:
            self._close_sent = True
            try:
                await self._send_frame(CLOSE, struct.pack("!H", code))
            except (ConnectionError, OSError):
                pass
        self.closed = True
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except (ConnectionError, OSError):
            pass

    async def close(self, code: int = 1000, timeout: float = 2.0) -> None:
        """Send a close frame, wait (up to `timeout` s) for the peer's close, then disconnect."""
        if self.closed:
            return
        if not self._close_sent:
            self._close_sent = True
            try:
                await self._send_frame(CLOSE, struct.pack("!H", code))
            except (ConnectionError, OSError):
                await self._abort(None)
                return
        try:
            async with asyncio.timeout(timeout):
                while not self.closed:
                    fin, opcode, payload, masked = await read_frame(self.reader)
                    if opcode == CLOSE:
                        break
        except (TimeoutError, asyncio.IncompleteReadError, ConnectionError, WebSocketError,
                OSError):
            pass
        await self._abort(None)


async def read_request(reader: asyncio.StreamReader) -> tuple[str, str, dict[str, str]]:
    """An HTTP request head: `(method, path, headers)`, header names lowercased."""
    head = await reader.readuntil(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    method, path, _ = lines[0].split(" ", 2)
    headers = {}
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if sep:
            headers[name.strip().lower()] = value.strip()
    return method, path, headers


def http_response(status: int, content_type: str, body: bytes,
                  extra: dict[str, str] | None = None) -> bytes:
    lines = [f"HTTP/1.1 {status} {REASONS.get(status, '')}",
             f"Content-Type: {content_type}", f"Content-Length: {len(body)}",
             "Cache-Control: no-store", "Connection: close"]
    lines += [f"{k}: {v}" for k, v in (extra or {}).items()]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body


async def serve(ws_routes: dict, http_handler=None, host: str | None = None,
                port: int = 8000) -> asyncio.Server:
    """Start a server answering WebSocket upgrades on the paths of `ws_routes` and other `GET`s
    with `http_handler(path) -> (status, content_type, body) | None`. Returns the started
    `asyncio.Server`; `host=None` listens on every interface."""

    async def on_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            try:
                method, target, headers = await asyncio.wait_for(read_request(reader), 10)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError,
                    TimeoutError, ConnectionError, OSError):
                return
            path = target.split("?", 1)[0]
            upgrade = headers.get("upgrade", "").lower() == "websocket"
            if upgrade and path in ws_routes:
                key = headers.get("sec-websocket-key")
                if method != "GET" or not key or headers.get("sec-websocket-version") != "13":
                    writer.write(http_response(400, "text/plain", b"bad WebSocket handshake\n"))
                    await writer.drain()
                    return
                writer.write(
                    b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                    b"Connection: Upgrade\r\nSec-WebSocket-Accept: "
                    + accept_key(key).encode("ascii") + b"\r\n\r\n")
                await writer.drain()
                ws = WebSocket(reader, writer, client=False, path=path)
                try:
                    await ws_routes[path](ws, path)
                finally:
                    await ws.close()
                return
            if path in ws_routes:
                writer.write(http_response(426, "text/plain", b"WebSocket only\n",
                                           {"Upgrade": "websocket"}))
            else:
                page = http_handler(path) if http_handler and method == "GET" else None
                if page is None:
                    writer.write(http_response(404, "text/plain", b"not found\n"))
                else:
                    status, content_type, body = page
                    writer.write(http_response(status, content_type, body))
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    return await asyncio.start_server(on_connection, host, port, reuse_address=True)


async def connect(url: str, timeout: float = 10.0) -> WebSocket:
    """Open a client WebSocket to `ws://host:port/path`. Raises `ConnectionError` (or
    `OSError`) when the server cannot be reached or refuses the handshake."""
    parts = urlsplit(url)
    if parts.scheme != "ws" or not parts.hostname:
        raise ValueError(f"not a ws:// URL: {url!r}")
    port = parts.port or 80
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    async with asyncio.timeout(timeout):
        reader, writer = await asyncio.open_connection(parts.hostname, port)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        host = parts.hostname if port == 80 else f"{parts.hostname}:{port}"
        writer.write(
            f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n".encode("latin-1"))
        await writer.drain()
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except asyncio.IncompleteReadError:
            writer.close()
            raise ConnectionError(f"{url}: the server closed the connection") from None
    lines = head.decode("latin-1").split("\r\n")
    headers = {}
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if sep:
            headers[name.strip().lower()] = value.strip()
    status = lines[0].split(" ", 2)
    if len(status) < 2 or status[1] != "101" or \
            headers.get("sec-websocket-accept") != accept_key(key):
        writer.close()
        raise ConnectionError(f"{url}: WebSocket handshake refused ({lines[0]})")
    return WebSocket(reader, writer, client=True, path=parts.path or "/")
