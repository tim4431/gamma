"""gzip for whole JSON answers only. The API's big reads are JSON (a page's
subtree, the library listing, search results) and compress several times
over. Everything else goes out as it is: streamed replies (a compressor
would hold their lines back), files (uploads with their range requests,
PDFs, the app's assets) and anything already encoded. Starlette's
GZipMiddleware compresses streams too, hence this one: it takes an
``application/json`` body whose Content-Length is known up front (a
JSONResponse; a stream has none). The session middleware relays such a body
in pieces, so it is collected before it is compressed."""

import gzip

import anyio
from starlette.datastructures import Headers, MutableHeaders

MIN_SIZE = 1024       # bytes; smaller bodies go as they are
LEVEL = 3             # zlib level: most of level 6's ratio at a fraction of its time
THREAD_ABOVE = 256 * 1024  # compress bigger bodies in a worker thread, off the event loop


class JsonGzip:
    """ASGI middleware: gzip a 200 ``application/json`` response of a known
    length of at least MIN_SIZE bytes, without a Content-Encoding or
    ranges, when the client accepts gzip (such a response says ``Vary:
    Accept-Encoding`` either way). Anything else passes through untouched,
    message by message."""

    def __init__(self, app):
        self.app = app

    def _eligible(self, message) -> bool:
        headers = Headers(raw=message["headers"])
        length = headers.get("content-length", "")
        return (message["status"] == 200
                and headers.get("content-type", "").startswith("application/json")
                and length.isdigit() and int(length) >= MIN_SIZE
                and "content-encoding" not in headers and "content-range" not in headers
                and "accept-ranges" not in headers)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] == "HEAD":
            await self.app(scope, receive, send)
            return
        accepts = "gzip" in Headers(scope=scope).get("accept-encoding", "")
        start, parts = None, []  # an eligible response, until its body is complete

        async def send_maybe_compressed(message):
            nonlocal start
            if message["type"] == "http.response.start" and self._eligible(message):
                start = message
                return
            if start is None or message["type"] != "http.response.body":
                await send(message)
                return
            parts.append(message.get("body", b""))
            if message.get("more_body"):
                return
            body = b"".join(parts)
            headers = MutableHeaders(raw=start["headers"])
            headers.add_vary_header("Accept-Encoding")
            if accepts:
                if len(body) > THREAD_ABOVE:
                    body = await anyio.to_thread.run_sync(self._compress, body)
                else:
                    body = self._compress(body)
                headers["Content-Encoding"] = "gzip"
                headers["Content-Length"] = str(len(body))
            await send(start)
            await send({"type": "http.response.body", "body": body})

        await self.app(scope, receive, send_maybe_compressed)

    def _compress(self, body: bytes) -> bytes:
        return gzip.compress(body, compresslevel=LEVEL, mtime=0)
