from __future__ import annotations

import html
import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from .config import Config
from .ripper import JobRunner
from .store import StateStore, parse_metadata

STYLE = """
body { font-family: Inter, system-ui, sans-serif; margin: 0; background: #0f172a; color: #e2e8f0; }
a { color: #93c5fd; }
main { max-width: 1100px; margin: 0 auto; padding: 2rem; }
.grid { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }
.card { background: #111827; border: 1px solid #1f2937; border-radius: 16px; padding: 1rem 1.25rem; box-shadow: 0 10px 30px rgba(0,0,0,.18); }
pre, textarea { width: 100%; background: #020617; color: #cbd5e1; border: 1px solid #334155; border-radius: 10px; padding: .75rem; box-sizing: border-box; }
textarea { min-height: 8rem; }
button { background: #2563eb; color: white; border: none; border-radius: 999px; padding: .6rem 1rem; margin: .25rem .4rem .25rem 0; cursor: pointer; }
button.secondary { background: #334155; }
.badge { display: inline-block; border-radius: 999px; padding: .2rem .65rem; font-size: .85rem; text-transform: capitalize; }
.badge.running { background: #1d4ed8; }
.badge.queued { background: #a16207; }
.badge.idle, .badge.done { background: #166534; }
.badge.error { background: #b91c1c; }
small { color: #94a3b8; }
form.inline { display: inline; }
"""


def render_page(store: StateStore, message: str = "") -> bytes:
    drives = store.list_drives()
    jobs = store.list_jobs()
    cards: list[str] = []
    for drive in drives:
        latest = store.latest_job_for_drive(str(drive["device"]))
        metadata = json.dumps(json.loads(str(drive["metadata_json"])), indent=2)
        cards.append(
            f"""
            <section class='card'>
              <h2>{html.escape(str(drive['device']))}</h2>
              <p><span class='badge {html.escape(str(drive['status']))}'>{html.escape(str(drive['status']))}</span>
              <span class='badge {html.escape(str(drive['disc_type']))}'>{html.escape(str(drive['disc_type']))}</span></p>
              <p><small>Last seen {html.escape(str(drive['last_seen']))}</small></p>
              <p>{html.escape(str(drive['last_error'] or 'No errors recorded.'))}</p>
              <form method='post'>
                <input type='hidden' name='action' value='save-metadata'>
                <input type='hidden' name='device' value='{html.escape(str(drive['device']))}'>
                <label>Metadata JSON</label>
                <textarea name='metadata'>{html.escape(metadata)}</textarea>
                <div>
                  <button type='submit'>Save metadata</button>
                </div>
              </form>
              <form method='post' class='inline'>
                <input type='hidden' name='action' value='restart'>
                <input type='hidden' name='device' value='{html.escape(str(drive['device']))}'>
                <button type='submit'>Restart job</button>
              </form>
              <form method='post' class='inline'>
                <input type='hidden' name='action' value='eject'>
                <input type='hidden' name='device' value='{html.escape(str(drive['device']))}'>
                <button type='submit' class='secondary'>Eject drive</button>
              </form>
              <pre>{html.escape(str(latest['command'] if latest and latest['command'] else 'Awaiting job'))}</pre>
            </section>
            """
        )
    rows = "".join(
        f"<tr><td>{job['id']}</td><td>{html.escape(str(job['device']))}</td><td>{html.escape(str(job['disc_type']))}</td><td>{html.escape(str(job['status']))}</td><td>{html.escape(str(job['error'] or ''))}</td><td>{html.escape(str(job['output_path'] or ''))}</td></tr>"
        for job in jobs
    )
    body = f"""
    <!doctype html>
    <html lang='en'>
      <head>
        <meta charset='utf-8'>
        <title>jack</title>
        <meta http-equiv='refresh' content='10'>
        <style>{STYLE}</style>
      </head>
      <body>
        <main>
          <h1>jack</h1>
          <p>Minimal multi-drive autoripper using udev, MakeMKV, whipper, and ffmpeg.</p>
          <p>{html.escape(message)}</p>
          <div class='grid'>{''.join(cards) or "<section class='card'><p>No drives have reported media yet.</p></section>"}</div>
          <section class='card'>
            <h2>Recent jobs</h2>
            <table>
              <thead><tr><th>ID</th><th>Drive</th><th>Type</th><th>Status</th><th>Error</th><th>Output</th></tr></thead>
              <tbody>{rows}</tbody>
            </table>
          </section>
        </main>
      </body>
    </html>
    """
    return body.encode()


class JackHandler(BaseHTTPRequestHandler):
    store: StateStore
    runner: JobRunner

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = render_page(self.store)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        params = parse_qs(self.rfile.read(length).decode())
        action = params.get("action", [""])[0]
        device = params.get("device", [""])[0]
        message = ""
        try:
            if action == "eject":
                self.runner.eject(device)
                message = f"Ejected {device}"
            elif action == "restart":
                job_id = self.runner.restart_drive(device)
                message = f"Queued restart for {device} as job {job_id}" if job_id else f"No prior job found for {device}"
            elif action == "save-metadata":
                metadata_text = params.get("metadata", ["{}"])[0]
                parsed = json.dumps(parse_metadata(metadata_text), indent=2, sort_keys=True)
                self.store.set_drive_metadata(device, parsed)
                message = f"Saved metadata for {device}"
            else:
                raise ValueError("Unknown action")
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
        body = render_page(self.store, message)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        return


class Dispatcher(threading.Thread):
    def __init__(self, config: Config, runner: JobRunner):
        super().__init__(daemon=True)
        self.config = config
        self.runner = runner
        self.stop_event = threading.Event()

    def run(self) -> None:
        while not self.stop_event.is_set():
            self.runner.dispatch()
            self.stop_event.wait(self.config.poll_interval)


def serve(config: Config, store: StateStore, runner: JobRunner) -> None:
    Dispatcher(config, runner).start()
    server = ThreadingHTTPServer((config.host, config.port), JackHandler)
    JackHandler.store = store
    JackHandler.runner = runner
    server.serve_forever()
