from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Mapping
from urllib import request

from .config import Config
from .store import StateStore, parse_metadata


def classify_disc(env: Mapping[str, str]) -> str:
    audio_tracks = int(env.get("ID_CDROM_MEDIA_TRACK_COUNT_AUDIO") or "0")
    data_tracks = int(env.get("ID_CDROM_MEDIA_TRACK_COUNT_DATA") or "0")
    if audio_tracks and not data_tracks:
        return "audio"
    return "video"


def build_output_dir(base: Path, disc_type: str, device: str, job_id: int) -> Path:
    safe_device = device.replace("/", "_").strip("_") or "drive"
    return base / disc_type / safe_device / str(job_id)


def build_command(job: Mapping[str, object], output_dir: Path) -> list[str]:
    device = str(job["device"])
    disc_type = str(job["disc_type"])
    if disc_type == "audio":
        return [
            "whipper",
            "cd",
            "rip",
            "--device",
            device,
            "--output-directory",
            str(output_dir),
        ]
    return ["makemkvcon", "mkv", f"dev:{device}", "all", str(output_dir)]


def ffmpeg_metadata_args(metadata: Mapping[str, str]) -> list[str]:
    args: list[str] = []
    for key, value in metadata.items():
        args.extend(["-metadata", f"{key}={value}"])
    return args


def apply_audio_metadata(output_dir: Path, metadata_json: str) -> None:
    metadata = parse_metadata(metadata_json)
    if not metadata:
        return
    for flac in sorted(output_dir.rglob("*.flac")):
        tmp = flac.with_suffix(".retag.flac")
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(flac),
            "-map_metadata",
            "0",
            "-codec",
            "copy",
            *ffmpeg_metadata_args(metadata),
            str(tmp),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        tmp.replace(flac)


class JobRunner:
    def __init__(self, config: Config, store: StateStore):
        self.config = config
        self.store = store
        self.lock = threading.Lock()
        self.processes: dict[str, tuple[int, subprocess.Popen[str]]] = {}
        self.starting: set[str] = set()

    def is_busy(self, device: str) -> bool:
        with self.lock:
            return device in self.processes or device in self.starting

    def restart_drive(self, device: str) -> int | None:
        latest = self.store.latest_job_for_drive(device)
        if latest is None:
            return None
        with self.lock:
            running = self.processes.get(device)
        if running:
            job_id, process = running
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            self.store.set_job_error(job_id, "Restart requested from web UI")
        return self.store.restart_job(int(latest["id"]))

    def eject(self, device: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["eject", device], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def dispatch(self) -> None:
        for job in self.store.claim_queued_jobs():
            device = str(job["device"])
            if self.is_busy(device):
                continue
            with self.lock:
                if device in self.processes or device in self.starting:
                    continue
                self.starting.add(device)
            thread = threading.Thread(target=self._run_job, args=(int(job["id"]),), daemon=True)
            thread.start()

    def _run_job(self, job_id: int) -> None:
        job = self.store.get_job(job_id)
        if job is None:
            return
        output_dir = build_output_dir(self.config.output_dir, str(job["disc_type"]), str(job["device"]), job_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        command = build_command(job, output_dir)
        job = self.store.mark_job_running(job_id, " ".join(command))
        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            with self.lock:
                self.starting.discard(str(job["device"]))
                self.processes[str(job["device"])] = (job_id, process)
            stdout, _ = process.communicate()
            if process.returncode != 0:
                raise RuntimeError(stdout.strip() or f"Command failed with exit code {process.returncode}")
            if str(job["disc_type"]) == "audio":
                apply_audio_metadata(output_dir, str(job["metadata_json"]))
            finished = self.store.finish_job(job_id, "done", output_path=str(output_dir))
            self.send_webhook("completed", finished)
        except Exception as exc:  # noqa: BLE001
            finished = self.store.finish_job(job_id, "error", output_path=str(output_dir), error=str(exc))
            self.send_webhook("error", finished)
        finally:
            with self.lock:
                self.starting.discard(str(job["device"]))
                if process is not None:
                    self.processes.pop(str(job["device"]), None)

    def send_webhook(self, event: str, job: Mapping[str, object]) -> None:
        url = self.config.webhook_url
        if event == "completed":
            url = self.config.webhook_success_url or url
        elif event == "error":
            url = self.config.webhook_error_url or url
        if not url:
            return
        payload = json.dumps({"event": event, "job": dict(job)}).encode()
        req = request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            request.urlopen(req, timeout=10).read()
        except Exception:
            return
