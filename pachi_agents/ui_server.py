"""標準ライブラリだけで動くPachi Agents読み取り専用UIサーバー。"""
from __future__ import annotations
import argparse, json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from .ui_data import load_dashboard_data

class PachiAgentsHandler(SimpleHTTPRequestHandler):
    data_root: Path
    mode = "production"
    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path == "/api/dashboard":
            body = json.dumps(load_dashboard_data(self.data_root, self.mode), ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

def serve(data_root: str | Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    ui_root = Path(__file__).with_name("ui")
    handler_type = type("ConfiguredPachiAgentsHandler", (PachiAgentsHandler,), {"data_root": Path(data_root)})
    handler = partial(handler_type, directory=str(ui_root))
    ThreadingHTTPServer((host, port), handler).serve_forever()

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default=str(Path(__file__).with_name("data")))
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve(args.data_root, port=args.port)

if __name__ == "__main__":
    main()
