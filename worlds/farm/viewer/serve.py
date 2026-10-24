"""Serve the viewer and follow a run log while it is being written.

    python worlds/farm/viewer/serve.py RUN.jsonl [--port 8000]

Open the printed address. The run can be started before or after this server: the page keeps
asking for the bytes after the last row it has, so everything the farmers do shows up as it is
written. This is a local tool; the log is a file on this machine, so a static host such as
Vercel cannot see it. To share a finished run, host the viewer folder and the log file
statically and open `index.html?log=run.jsonl`.
"""

from __future__ import annotations

import argparse
import functools
import http.server
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent


class Handler(http.server.SimpleHTTPRequestHandler):
    log: Path

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path != "/run.jsonl":
            return super().do_GET()
        try:
            start = int(parse_qs(url.query).get("from", ["0"])[0])
            data = self.log.read_bytes()
        except (ValueError, FileNotFoundError):
            self.send_error(404 if not self.log.exists() else 400)
            return
        data = data[start : data.rfind(b"\n") + 1] if start < len(data) else b""  # whole lines only
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Log-Offset", str(start))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:  # keep the terminal quiet
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("log", type=Path, help="the run log (.jsonl) being written")
    parser.add_argument("--port", type=int, default=8000)
    arguments = parser.parse_args()
    handler = type("LogHandler", (Handler,), {"log": arguments.log})
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", arguments.port), functools.partial(handler, directory=str(ROOT))
    )
    print(f"Watching {arguments.log}: http://127.0.0.1:{arguments.port}/?log=/run.jsonl&live=1")
    server.serve_forever()


if __name__ == "__main__":
    main()
