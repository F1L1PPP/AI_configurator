"""Serve the playground site on http://localhost:8765.

Run from anywhere:

    python playwright_playground/serve.py

Stop with Ctrl+C.
"""

from __future__ import annotations

import http.server
import socketserver
from pathlib import Path

PORT = 8765
SITE_DIR = Path(__file__).parent / "site"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        # One line per request, no extra noise.
        print(f"[serve] {self.address_string()} {format % args}")


class ReusableTCPServer(socketserver.TCPServer):
    # Without this, the OS keeps :8765 in TIME_WAIT for ~30s after Ctrl+C and
    # the next `python serve.py` fails with "Address already in use". Setting
    # this flag tells Python to skip that check — standard for dev servers.
    allow_reuse_address = True


def main() -> None:
    class Handler(QuietHandler):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, directory=str(SITE_DIR), **kwargs)

    with ReusableTCPServer(("127.0.0.1", PORT), Handler) as httpd:
        print(f"[serve] Playwright playground site at http://localhost:{PORT}/")
        print(f"[serve] Serving from {SITE_DIR}")
        print("[serve] Stop with Ctrl+C")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[serve] Shutting down")


if __name__ == "__main__":
    main()
