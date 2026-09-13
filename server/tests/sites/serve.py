"""Tiny local sites the browser tool is tested against: a sign-in wall, a suggestion picker, a
cookie banner, a page with a header "Anmelden" and a footer "Partner anmelden", a slow page and a
file upload. Served by a threaded HTTP server on a free port; pytest fixtures in `fixtures.py`."""

from __future__ import annotations

import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

SITES_DIR = Path(__file__).resolve().parent
USER, PASSWORD = "jayden", "ola-demo"


class _Handler(SimpleHTTPRequestHandler):
    def log_message(self, *a, **k) -> None:  # quiet
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/slow"):
            time.sleep(3.0)  # a server that takes its time before the first byte
            self.path = "/slow.html"
        if self.path.startswith("/welcome"):
            if "ola_session=ok" not in self.headers.get("Cookie", ""):
                self.send_response(302)
                self.send_header("Location", "/signin.html")
                self.end_headers()
                return
            self.path = "/welcome.html"
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        if self.path.startswith("/login"):
            ok = form.get("user", [""])[0] == USER and form.get("password", [""])[0] == PASSWORD
            self.send_response(302)
            if ok:
                self.send_header("Set-Cookie", "ola_session=ok; Path=/")
            self.send_header("Location", "/welcome" if ok else "/signin.html?error=1")
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()


class SiteServer:
    def __init__(self) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, directory=str(SITES_DIR)))
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self) -> "SiteServer":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def url(self, path: str = "/") -> str:
        return f"http://127.0.0.1:{self.port}{path}"


if __name__ == "__main__":
    s = SiteServer().start()
    print(s.url())
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        s.stop()
