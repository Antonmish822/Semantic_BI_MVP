"""Shared bounds for blocking HTTP services and credential-bearing requests."""
from http.server import ThreadingHTTPServer
from threading import BoundedSemaphore
import sys
import time
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener


class RefuseRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# A new destination must receive its own explicit credentials and authorization.
open_upstream = build_opener(ProxyHandler({}), RefuseRedirect()).open


def read_bounded(stream, limit, deadline=None):
    if limit <= 0:
        raise ValueError('Response byte limit must be positive')
    length = stream.headers.get('Content-Length')
    if length is not None and not 0 <= int(length) <= limit:
        raise ValueError('Upstream response too large')
    data = bytearray()
    deadline = deadline if deadline is not None else time.monotonic() + 60
    sock = getattr(getattr(getattr(stream, "fp", None), "raw", None), "_sock", None)
    read = getattr(stream, "read1", stream.read)
    while len(data) <= limit:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Upstream response deadline exceeded")
        if sock is not None and sock.fileno() >= 0:
            sock.settimeout(min(sock.gettimeout() or remaining, remaining))
        chunk = read(min(64 * 1024, limit + 1 - len(data)))
        if not chunk:
            return bytes(data)
        data.extend(chunk)
    raise ValueError("Upstream response too large")


class BoundedHTTPServer(ThreadingHTTPServer):
    request_queue_size = 64

    def __init__(self, address, handler, workers=32):
        self.slots = BoundedSemaphore(workers)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        request.settimeout(15)
        if not self.slots.acquire(blocking=False):
            try:
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nRetry-After: 1\r\nContent-Length: 0\r\n\r\n')
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if not isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            print(f"HTTP request failed ({type(exc).__name__})", file=sys.stderr)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()
