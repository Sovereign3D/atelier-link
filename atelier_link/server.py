"""Read-only local server that hands mesh data to the Atelier plugin in Studio."""

import hmac
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

APP_NAME = "Atelier Link"
VERSION = "1.0.0"
LONG_POLL_SECONDS = 5.0
MAX_WRONG_CODES = 5
LOCK_SECONDS = 30.0


class _Store:
    def __init__(self):
        self.lock = threading.Condition()
        self.revision = 0
        self.objects = {}
        self.textures = {}
        self.changed_at = {}
        self.removed_at = {}
        self.closed = False
        self.wrong_codes = 0
        self.locked_until = 0.0

    def is_locked(self):
        with self.lock:
            return time.monotonic() < self.locked_until

    def check_code(self, supplied, pair_code):
        """Lock check, comparison and wrong-code count in one step, so parallel guesses can't slip past the lock."""
        with self.lock:
            if time.monotonic() < self.locked_until:
                return "locked"
            if hmac.compare_digest(supplied.encode("utf-8"), pair_code.encode("utf-8")):
                return "ok"
            self.wrong_codes += 1
            if self.wrong_codes % MAX_WRONG_CODES == 0:
                rounds = self.wrong_codes // MAX_WRONG_CODES
                self.locked_until = time.monotonic() + LOCK_SECONDS * (2 ** (rounds - 1))
            return "bad"

    def record_wrong_code(self):
        with self.lock:
            self.wrong_codes += 1
            if self.wrong_codes % MAX_WRONG_CODES == 0:
                rounds = self.wrong_codes // MAX_WRONG_CODES
                self.locked_until = time.monotonic() + LOCK_SECONDS * (2 ** (rounds - 1))

    def publish(self, objects, textures):
        with self.lock:
            self.revision += 1
            names = set()
            for data in objects:
                name = data["name"]
                names.add(name)
                if self.objects.get(name) is not data:
                    self.objects[name] = data
                    self.changed_at[name] = self.revision
                self.removed_at.pop(name, None)
            for name in list(self.objects):
                if name not in names:
                    del self.objects[name]
                    self.changed_at.pop(name, None)
                    self.textures.pop(name, None)
                    self.removed_at[name] = self.revision
            self.textures.update(textures)
            self.lock.notify_all()

    def changes_since(self, since):
        with self.lock:
            self.lock.wait_for(lambda: self.revision > since or self.closed, LONG_POLL_SECONDS)
            return {
                "revision": self.revision,
                "objects": [d for n, d in self.objects.items() if self.changed_at.get(n, 0) > since],
                "removed": sorted(n for n, r in self.removed_at.items() if r > since),
            }

    def close(self):
        with self.lock:
            self.closed = True
            self.lock.notify_all()


def _make_handler(store, pair_code, port):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "AtelierLink"
        sys_version = ""

        def log_message(self, *args):
            return

        def _send(self, status, body=None):
            payload = json.dumps(body if body is not None else {}).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.headers.get("Host", "") not in allowed_hosts:
                return self._send(403, {"error": "host"})
            # Studio never sends Origin or Sec-Fetch-*; a web page in a browser does. Refuse those without counting
            # them, so a website can't lock Studio out by sending wrong codes.
            if self.headers.get("Origin") or self.headers.get("Sec-Fetch-Site"):
                return self._send(403, {"error": "browser"})
            supplied = self.headers.get("X-Atelier-Pair", "")
            if not supplied:
                return self._send(401, {"error": "pair"})
            verdict = store.check_code(supplied, pair_code)
            if verdict == "locked":
                return self._send(429, {"error": "locked"})
            if verdict != "ok":
                return self._send(401, {"error": "pair"})

            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path == "/status":
                with store.lock:
                    return self._send(200, {
                        "app": APP_NAME,
                        "version": VERSION,
                        "revision": store.revision,
                        "objects": sorted(store.objects),
                    })
            if url.path == "/changes":
                try:
                    since = int(query.get("since", ["0"])[0])
                except ValueError:
                    return self._send(400, {"error": "since"})
                return self._send(200, store.changes_since(since))
            if url.path == "/texture":
                name = query.get("name", [""])[0]
                with store.lock:
                    texture = store.textures.get(name)
                if texture is None:
                    return self._send(404, {"error": "texture"})
                return self._send(200, texture)
            return self._send(404, {"error": "path"})

        def do_POST(self):
            self._send(405, {"error": "method"})

        do_PUT = do_DELETE = do_PATCH = do_POST

    return Handler


class _ExclusiveServer(ThreadingHTTPServer):
    """Refuses to share the port, so two Blender windows can't both claim it."""

    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class LinkServer:
    def __init__(self, port, pair_code):
        self.port = port
        self.pair_code = pair_code
        self._store = _Store()
        self._httpd = None
        self._thread = None

    @property
    def address(self):
        return self._httpd.server_address if self._httpd else None

    @property
    def locked(self):
        return self._store.is_locked()

    @property
    def running(self):
        return self._httpd is not None

    def start(self):
        if self._httpd:
            return
        handler = _make_handler(self._store, self.pair_code, self.port)
        self._httpd = _ExclusiveServer(("127.0.0.1", self.port), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="AtelierLink", daemon=True)
        self._thread.start()

    def stop(self):
        if not self._httpd:
            return
        self._store.close()
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        self._thread = None
        self._store = _Store()

    def publish(self, objects, textures):
        self._store.publish(objects, textures)
