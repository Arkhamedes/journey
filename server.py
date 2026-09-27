"""Local web UI for the productivity journal (stdlib only).

Run via: ./journal.py serve [--port 8765]
Binds to 127.0.0.1 — the journal is personal, so it is never exposed to the network.
"""

import json
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import jcore

WEB_DIR = Path(__file__).resolve().parent / "web"


def load_entries():
    con = jcore.connect()
    try:
        return jcore.all_entries(con)
    finally:
        con.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "journey"

    def _send(self, status, body, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status=200):
        self._send(status, json.dumps(obj).encode(), "application/json")

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        try:
            if url.path in ("/", "/index.html"):
                self._send(200, (WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/config":
                self._json({"config": jcore.load_config(), "today": date.today().isoformat()})
            elif url.path == "/api/entry":
                d = date.fromisoformat(q["date"][0])
                con = jcore.connect()
                try:
                    entry = jcore.get_entry(con, d)
                finally:
                    con.close()
                self._json({"date": d.isoformat(), "entry": entry})
            elif url.path == "/api/week":
                today = date.today()
                if "date" in q:
                    ws = jcore.week_start(date.fromisoformat(q["date"][0]))
                else:
                    ws = jcore.default_report_week(today)
                self._json(jcore.week_report_data(load_entries(), ws, jcore.load_config(), today))
            elif url.path == "/api/stats":
                entries = load_entries()
                recent = []
                for d in sorted(entries)[-14:]:
                    e = entries[d]
                    recent.append({
                        "date": d.isoformat(),
                        "productivity": e["metrics"].get("productivity"),
                        "win": (e["sections"].get("Top wins today") or "").split("\n")[0],
                    })
                self._json({
                    "stats": jcore.stats_data(entries, jcore.load_config(), date.today()),
                    "recent": recent,
                })
            else:
                self._json({"error": "not found"}, 404)
        except (KeyError, ValueError) as err:
            self._json({"error": str(err)}, 400)

    def do_POST(self):
        if urlparse(self.path).path != "/api/entry":
            self._json({"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            d = date.fromisoformat(payload["date"])
            cfg = jcore.load_config()
            metrics = {}
            for m in cfg["metrics"]:
                val = jcore.coerce_metric(m, payload.get("metrics", {}).get(m["key"]))
                if val is not None:
                    metrics[m["key"]] = val
            if "productivity" not in metrics:
                raise ValueError("a productivity rating is required")
            allowed = {p["label"] for p in cfg["prompts"]} | {"Journal"}
            sections = {}
            for key, val in (payload.get("sections") or {}).items():
                if key in allowed and isinstance(val, str) and val.strip():
                    sections[key] = val.strip()
            con = jcore.connect()
            try:
                jcore.upsert_entry(con, d, metrics, sections)
                entries = jcore.all_entries(con)
            finally:
                con.close()
            current, longest = jcore.streaks(set(entries), d)
            wavg = jcore.week_avg(entries, jcore.week_start(d))
            self._json({"ok": True, "streak": current, "longest": longest,
                        "week_avg": round(wavg, 2) if wavg is not None else None})
        except (KeyError, ValueError, json.JSONDecodeError) as err:
            self._json({"error": str(err)}, 400)


def serve(port=8765):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Journey running at http://127.0.0.1:{port}  (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    serve()
