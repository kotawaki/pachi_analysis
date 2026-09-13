"""Local-only server for the mobile 10 Practice Log page."""
from __future__ import annotations

import argparse
import json
import threading
import uuid
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
STORE_DIR = ROOT / "data" / "local_practice_log"
STORE = STORE_DIR / "practice_log.json"
LOCK = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_store() -> dict:
    if not STORE.exists():
        return {"version": 1, "sessions": []}
    try:
        value = json.loads(STORE.read_text(encoding="utf-8"))
        if isinstance(value, dict) and isinstance(value.get("sessions"), list):
            return value
    except (OSError, json.JSONDecodeError):
        pass
    return {"version": 1, "sessions": []}


def save_store(value: dict) -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STORE)


def json_body(handler: SimpleHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length)
    value = json.loads(raw.decode("utf-8")) if raw else {}
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DOCS), **kwargs)

    def send_json(self, status: int, value: dict) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/practice-log":
            date = parse_qs(parsed.query).get("date", [None])[0]
            with LOCK:
                sessions = load_store()["sessions"]
            if date:
                sessions = [s for s in sessions if s.get("date") == date]
            self.send_json(200, {"version": 1, "sessions": sessions})
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            payload = json_body(self)
            with LOCK:
                store = load_store()
                if parsed.path == "/api/practice-log/session":
                    session_id = uuid.uuid4().hex
                    stamp = now()
                    session = {
                        "session_id": session_id,
                        "date": str(payload["date"]),
                        "machine": str(payload["machine"]).zfill(3),
                        "started_at": stamp,
                        "start_spins": int(payload["start_spins"]),
                        "events": [],
                    }
                    session["events"].append({
                        "event_id": uuid.uuid4().hex,
                        "session_id": session_id,
                        "date": session["date"],
                        "machine": session["machine"],
                        "timestamp": stamp,
                        "spins": session["start_spins"],
                        "event_type": "SESSION_START",
                        "event_name": "実戦開始",
                        "during_jitan": False,
                        "memo": "",
                        "created_at": stamp,
                        "updated_at": stamp,
                    })
                    store["sessions"].append(session)
                    save_store(store)
                    self.send_json(201, {"session": session})
                    return
                if parsed.path == "/api/practice-log/event":
                    sid = str(payload["session_id"])
                    session = next(s for s in store["sessions"] if s["session_id"] == sid)
                    stamp = now()
                    event = {
                        "event_id": uuid.uuid4().hex,
                        "session_id": sid,
                        "date": session["date"],
                        "machine": session["machine"],
                        "timestamp": str(payload.get("timestamp") or stamp),
                        "spins": int(payload["spins"]),
                        "event_type": str(payload.get("event_type") or "ACTION"),
                        "event_name": str(payload["event_name"]),
                        "during_jitan": bool(payload.get("during_jitan", False)),
                        "memo": str(payload.get("memo", "")),
                        "created_at": stamp,
                        "updated_at": stamp,
                    }
                    session["events"].append(event)
                    save_store(store)
                    self.send_json(201, {"event": event})
                    return
        except (KeyError, ValueError, StopIteration, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})
            return
        self.send_json(404, {"error": "not found"})

    def do_PATCH(self) -> None:  # noqa: N802
        prefix = "/api/practice-log/event/"
        if not self.path.startswith(prefix):
            self.send_json(404, {"error": "not found"})
            return
        event_id = self.path[len(prefix):]
        try:
            payload = json_body(self)
            with LOCK:
                store = load_store()
                for session in store["sessions"]:
                    for event in session["events"]:
                        if event["event_id"] == event_id:
                            for key in ("spins", "event_name", "during_jitan", "memo"):
                                if key in payload:
                                    event[key] = int(payload[key]) if key == "spins" else payload[key]
                            event["updated_at"] = now()
                            save_store(store)
                            self.send_json(200, {"event": event})
                            return
            self.send_json(404, {"error": "event not found"})
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})

    def do_DELETE(self) -> None:  # noqa: N802
        prefix = "/api/practice-log/event/"
        if not self.path.startswith(prefix):
            self.send_json(404, {"error": "not found"})
            return
        event_id = self.path[len(prefix):]
        with LOCK:
            store = load_store()
            for session in store["sessions"]:
                before = len(session["events"])
                session["events"] = [e for e in session["events"] if e["event_id"] != event_id]
                if len(session["events"]) != before:
                    save_store(store)
                    self.send_json(200, {"deleted": event_id})
                    return
        self.send_json(404, {"error": "event not found"})


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the local 10 Practice Log")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8777)
    args = parser.parse_args()
    print(f"Practice Log: http://127.0.0.1:{args.port}/practice_log/")
    print(f"LAN access: http://<this-PC-LAN-IP>:{args.port}/practice_log/")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
