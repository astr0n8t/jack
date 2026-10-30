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
:root {
  --bg: #0c0d0c; --panel: #111311; --fg: #c9cbc4; --dim: #6f736b; --line: #2e322d;
  --green: #8fbf6f; --amber: #d9a945; --red: #df6b5f; --blue: #79a6d2; --hl: #1a1d19;
}
* { box-sizing: border-box; border-radius: 0 !important; }
html { background: var(--bg); }
body { margin: 0; background: var(--bg); color: var(--fg); font: 13px/1.5 "JetBrains Mono", "IBM Plex Mono", "SF Mono", Menlo, Consolas, ui-monospace, monospace; }
a { color: var(--blue); }
h1, h2, h3, p { margin: 0; font-size: inherit; font-weight: inherit; }
.bar { display: flex; flex-wrap: wrap; align-items: center; gap: .25rem 1.25rem; padding: .35rem 1rem; background: var(--fg); color: var(--bg); }
.bar b { font-weight: 700; }
.spacer { flex: 1; }
.bar button { color: var(--bg); border-color: var(--bg); padding: 0 .5rem; margin: 0; }
.bar button:hover { background: var(--bg); color: var(--fg); }
main { max-width: 1200px; margin: 0 auto; padding: 1.25rem 1rem 3rem; }
.msg { border: 1px solid var(--amber); color: var(--amber); padding: .35rem .75rem; margin-bottom: 1.25rem; overflow-wrap: anywhere; }
.msg::before { content: "> "; }
.grid { display: grid; gap: 1.5rem 1rem; grid-template-columns: repeat(auto-fit, minmax(min(420px, 100%), 1fr)); margin-bottom: 1.5rem; }
.panel { position: relative; border: 1px solid var(--line); background: var(--panel); padding: 1rem .9rem .8rem; min-width: 0; }
.panel > .title { position: absolute; top: -.75em; left: .6rem; right: .6rem; display: flex; justify-content: space-between; gap: 1rem; pointer-events: none; }
.panel > .title > * { background: var(--bg); padding: 0 .4rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; pointer-events: auto; }
.panel > .title h2 { color: var(--fg); font-weight: 700; }
.kv { display: grid; grid-template-columns: 7ch 1fr; gap: .1rem 1ch; margin-bottom: .75rem; }
.kv dt { color: var(--dim); }
.kv dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.kv dd.err { color: var(--red); white-space: pre-wrap; max-height: 10rem; overflow-y: auto; }
.dim { color: var(--dim); }
pre, textarea { width: 100%; margin: 0; background: var(--bg); color: var(--fg); border: 1px solid var(--line); padding: .5rem .6rem; font: inherit; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 12rem; overflow-y: auto; color: var(--dim); }
textarea { min-height: 10rem; resize: vertical; }
textarea:focus, input:focus-visible, button:focus-visible, summary:focus-visible { outline: 1px solid var(--green); outline-offset: 1px; }
.tag { text-transform: uppercase; font-weight: 700; }
.tag::before { content: "["; color: var(--dim); font-weight: 400; }
.tag::after { content: "]"; color: var(--dim); font-weight: 400; }
.tag.idle, .tag.done, .tag.on { color: var(--green); }
.tag.queued { color: var(--amber); }
.tag.running { color: var(--blue); }
.tag.running .dot { animation: blink 1s steps(1) infinite; }
.tag.error, .tag.off { color: var(--red); }
@keyframes blink { 50% { opacity: 0; } }
.actions { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; margin-top: .75rem; }
form.inline { display: inline; }
button { font: inherit; text-transform: lowercase; background: transparent; color: var(--fg); border: 1px solid var(--line); padding: .1rem .7rem; cursor: pointer; }
button::before { content: "[ "; color: var(--dim); }
button::after { content: " ]"; color: var(--dim); }
button:hover { background: var(--fg); color: var(--bg); border-color: var(--fg); }
button.primary { color: var(--green); border-color: var(--green); }
button.primary:hover { background: var(--green); color: var(--bg); }
button.danger { color: var(--red); border-color: var(--red); }
button.danger:hover { background: var(--red); color: var(--bg); }
details { margin-top: .75rem; border-top: 1px dashed var(--line); padding-top: .6rem; }
summary { cursor: pointer; color: var(--dim); list-style: none; }
summary::-webkit-details-marker { display: none; }
summary::before { content: "+ "; }
details[open] > summary::before { content: "- "; }
summary:hover { color: var(--fg); }
details[open] > summary { margin-bottom: .5rem; }
h3 { color: var(--dim); text-transform: uppercase; margin: .25rem 0 .4rem; }
h3::before { content: "## "; }
.table-wrap { width: 100%; overflow-x: auto; border: 1px solid var(--line); }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: .2rem .6rem; vertical-align: top; white-space: nowrap; }
th { color: var(--dim); font-weight: 400; text-transform: uppercase; border-bottom: 1px solid var(--line); background: var(--bg); }
tbody tr:nth-child(even) { background: var(--hl); }
tbody tr:hover { background: #222620; }
td.num { text-align: right; color: var(--dim); }
td.long { white-space: normal; overflow-wrap: anywhere; min-width: 12rem; max-width: 36rem; }
td.long div { max-height: 6rem; overflow-y: auto; white-space: pre-wrap; }
td.err { color: var(--red); }
td.empty { color: var(--dim); text-align: center; padding: .75rem; }
input[type=checkbox] { appearance: none; width: 3ch; height: 1.2em; margin: 0; cursor: pointer; font: inherit; color: var(--dim); vertical-align: middle; }
input[type=checkbox]::before { content: "[ ]"; }
input[type=checkbox]:checked { color: var(--green); }
input[type=checkbox]:checked::before { content: "[x]"; }
.footer { position: fixed; bottom: 0; left: 0; right: 0; display: flex; gap: 1.5rem; padding: .2rem 1rem; border-top: 1px solid var(--line); background: var(--bg); color: var(--dim); overflow: hidden; white-space: nowrap; }
.footer b { color: var(--fg); font-weight: 400; }
@media (max-width: 600px) { main { padding: 1.25rem .6rem 3rem; } .bar { padding: .35rem .6rem; } .bar .sub { display: none; } }
"""

REFRESH_SCRIPT = """
(function () {
  var dirty = false;
  document.addEventListener('input', function () { dirty = true; });
  document.addEventListener('change', function () { dirty = true; });
  setInterval(function () {
    var busy = dirty || document.querySelector('details[open]') ||
      (document.activeElement && /TEXTAREA|INPUT/.test(document.activeElement.tagName));
    if (!busy) { location.replace('/'); }
  }, 10000);
})();
"""


def _safe_metadata(text: str) -> dict[str, object]:
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _esc(value: object) -> str:
    return html.escape(str(value))


def _ts(value: object) -> str:
    """Trim an ISO timestamp to `YYYY-MM-DD HH:MM:SS`."""
    text = str(value or "")
    return text[:19].replace("T", " ") if len(text) >= 19 else text


def _tag(status: object) -> str:
    status = str(status)
    dot = "<span class='dot'>*</span> " if status == "running" else ""
    return f"<span class='tag {_esc(status)}'>{dot}{_esc(status)}</span>"


def _size(value: object) -> str:
    text = str(value)
    if not text.isdigit():
        return _esc(text)
    size = float(text)
    for unit in ("B", "K", "M", "G"):
        if size < 1024:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}T"


def _button(action: str, label: str, device: str = "", css: str = "") -> str:
    device_field = (
        f"<input type='hidden' name='device' value='{_esc(device)}'>" if device else ""
    )
    return (
        "<form method='post' class='inline'>"
        f"<input type='hidden' name='action' value='{action}'>{device_field}"
        f"<button type='submit' class='{css}'>{label}</button></form>"
    )


def _render_tracks(device: str, metadata: dict[str, object]) -> str:
    tracks = metadata.get("makemkv_tracks", [])
    if not isinstance(tracks, list) or not tracks:
        return ""
    selected = {
        str(track_id)
        for track_id in metadata.get("selected_tracks", [])
        if str(track_id).isdigit()
    }
    rows = []
    for track in tracks:
        if not isinstance(track, dict):
            continue
        track_id = str(track.get("id", "")).strip()
        if not track_id.isdigit():
            continue
        checked = "checked" if track_id in selected else ""
        rows.append(
            "<tr>"
            f"<td><input type='checkbox' name='track' value='{_esc(track_id)}' {checked} "
            f"aria-label='Rip title {_esc(track_id)}'></td>"
            f"<td class='num'>{_esc(track_id)}</td>"
            f"<td class='long'>{_esc(track.get('name', ''))}</td>"
            f"<td>{_esc(track.get('duration', ''))}</td>"
            f"<td class='num'>{_esc(track.get('chapters', ''))}</td>"
            f"<td class='num'>{_size(track.get('filesize_bytes', ''))}</td>"
            "</tr>"
        )
    if not rows:
        return ""
    return f"""
              <form method='post'>
                <input type='hidden' name='action' value='queue-selected-tracks'>
                <input type='hidden' name='device' value='{_esc(device)}'>
                <h3>makemkv titles</h3>
                <div class='table-wrap'><table>
                  <thead><tr><th>rip</th><th>id</th><th>name</th><th>len</th><th>ch</th><th>size</th></tr></thead>
                  <tbody>{''.join(rows)}</tbody>
                </table></div>
                <div class='actions'><button type='submit' class='primary'>rip selected</button></div>
              </form>"""


def _render_drive(store: StateStore, drive) -> str:
    device = str(drive["device"])
    latest = store.latest_job_for_drive(device)
    metadata_data = _safe_metadata(str(drive["metadata_json"]))
    metadata = json.dumps(metadata_data, indent=2)
    error = drive["last_error"]
    error_markup = (
        f"<dd class='err'>{_esc(error)}</dd>" if error else "<dd class='dim'>none</dd>"
    )
    if latest:
        job_markup = f"<dd>#{_esc(latest['id'])} {_tag(latest['status'])}</dd>"
        command = latest["command"]
    else:
        job_markup = "<dd class='dim'>none</dd>"
        command = None
    command_markup = (
        f"<pre>$ {_esc(command)}</pre>" if command else "<pre>$ awaiting job_</pre>"
    )
    return f"""
            <section class='panel'>
              <div class='title'><h2>{_esc(device)}</h2>{_tag(drive['status'])}</div>
              <dl class='kv'>
                <dt>type</dt><dd>{_esc(drive['disc_type'])}</dd>
                <dt>seen</dt><dd>{_esc(_ts(drive['last_seen']))}</dd>
                <dt>job</dt>{job_markup}
                <dt>error</dt>{error_markup}
              </dl>
              {command_markup}
              {_render_tracks(device, metadata_data)}
              <div class='actions'>
                {_button('restart', 'restart', device, 'primary')}
                {_button('eject', 'eject', device, 'danger')}
              </div>
              <details>
                <summary>metadata.json</summary>
                <form method='post'>
                  <input type='hidden' name='action' value='save-metadata'>
                  <input type='hidden' name='device' value='{_esc(device)}'>
                  <textarea name='metadata' spellcheck='false' aria-label='Metadata JSON for {_esc(device)}'>{_esc(metadata)}</textarea>
                  <div class='actions'><button type='submit' class='primary'>save metadata</button></div>
                </form>
              </details>
            </section>"""


def render_page(store: StateStore, message: str = "") -> bytes:
    drives = store.list_drives()
    jobs = store.list_jobs()

    auto_rip_enabled = store.get_setting("auto_rip_enabled", "true") == "true"
    auto_rip_tag = (
        "<span class='tag on'>on</span>" if auto_rip_enabled else "<span class='tag off'>off</span>"
    )

    panels = "".join(_render_drive(store, drive) for drive in drives) or (
        "<section class='panel'><div class='title'><h2>drives</h2></div>"
        "<p class='dim'>no drives have reported media yet. insert a disc_</p></section>"
    )

    rows = "".join(
        "<tr>"
        f"<td class='num'>{_esc(job['id'])}</td>"
        f"<td>{_esc(job['device'])}</td>"
        f"<td>{_esc(job['disc_type'])}</td>"
        f"<td>{_tag(job['status'])}</td>"
        f"<td class='dim'>{_esc(_ts(job['updated_at']))}</td>"
        f"<td class='long err'><div>{_esc(job['error'] or '')}</div></td>"
        f"<td class='long'>{_esc(job['output_path'] or '')}</td>"
        "</tr>"
        for job in jobs
    ) or "<tr><td colspan='7' class='empty'>no jobs yet</td></tr>"

    counts: dict[str, int] = {}
    for drive in drives:
        counts[str(drive["status"])] = counts.get(str(drive["status"]), 0) + 1
    summary = "".join(
        f"<span>{_esc(status)} <b>{count}</b></span>" for status, count in sorted(counts.items())
    )
    message_markup = f"<div class='msg' role='status'>{_esc(message)}</div>" if message else ""

    body = f"""
    <!doctype html>
    <html lang='en'>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width, initial-scale=1'>
        <title>jack</title>
        <noscript><meta http-equiv='refresh' content='10; url=/'></noscript>
        <style>{STYLE}</style>
      </head>
      <body>
        <header class='bar'>
          <b>jack</b>
          <span class='sub'>multi-drive autoripper</span>
          <span class='spacer'></span>
          <span>auto-rip {auto_rip_tag}</span>
          {_button('toggle-auto-rip', 'toggle')}
        </header>
        <main>
          {message_markup}
          <div class='grid'>{panels}</div>
          <section class='panel'>
            <div class='title'><h2>recent jobs</h2><span class='dim'>{len(jobs)}</span></div>
            <div class='table-wrap'><table>
              <thead><tr><th>id</th><th>drive</th><th>type</th><th>status</th><th>updated</th><th>error</th><th>output</th></tr></thead>
              <tbody>{rows}</tbody>
            </table></div>
          </section>
        </main>
        <footer class='footer'>
          <span>drives <b>{len(drives)}</b></span>{summary}
          <span class='spacer'></span>
          <span>refresh 10s</span>
        </footer>
        <script>{REFRESH_SCRIPT}</script>
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
            elif action == "toggle-auto-rip":
                current = self.store.get_setting("auto_rip_enabled", "true")
                new_val = "false" if current == "true" else "true"
                self.store.set_setting("auto_rip_enabled", new_val)
                message = f"Auto-rip is now {'Enabled' if new_val ==
                                             'true' else 'Disabled'}"
            elif action == "restart":
                job_id = self.runner.restart_drive(device)
                message = f"Queued restart for {device} as job {
                    job_id}" if job_id else f"No prior job found for {device}"
            elif action == "save-metadata":
                metadata_text = params.get("metadata", ["{}"])[0]
                parsed = json.dumps(parse_metadata(
                    metadata_text), indent=2, sort_keys=True)
                self.store.set_drive_metadata(device, parsed)
                message = f"Saved metadata for {device}"
            elif action == "queue-selected-tracks":
                drive = self.store.get_drive(device)
                if drive is None:
                    raise ValueError(f"Unknown drive {device}")
                metadata = _safe_metadata(str(drive["metadata_json"]))
                available = {
                    str(track.get("id"))
                    for track in metadata.get("makemkv_tracks", [])
                    if isinstance(track, dict) and str(track.get("id", "")).isdigit()
                }
                selected = [track_id for track_id in params.get(
                    "track", []) if track_id in available]
                if not selected:
                    raise ValueError("Select at least one track")
                metadata["selected_tracks"] = [
                    int(track_id) for track_id in selected]
                metadata_json = json.dumps(metadata, indent=2, sort_keys=True)
                self.store.set_drive_metadata(device, metadata_json)
                latest = self.store.latest_job_for_drive(device)
                if latest is None or latest["status"] not in ("queued", "running"):
                    job_id = self.store.enqueue_job(device, str(
                        drive["disc_type"]), metadata_json=metadata_json, source="web", force=True)
                    message = f"Queued selected tracks for {
                        device} as job {job_id}"
                else:
                    message = f"Updated selected tracks for {device}"
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
