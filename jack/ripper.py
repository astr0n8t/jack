from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import unicodedata
import xml.etree.ElementTree as ET
from csv import reader
from datetime import datetime
from pathlib import Path
from typing import Mapping
from urllib import request

from .config import Config
from .store import StateStore


def classify_disc(env: Mapping[str, str]) -> str:
    audio_tracks = int(env.get("ID_CDROM_MEDIA_TRACK_COUNT_AUDIO") or "0")
    data_tracks = int(env.get("ID_CDROM_MEDIA_TRACK_COUNT_DATA") or "0")
    if audio_tracks and not data_tracks:
        return "audio"
    return "video"


def _run(command: list[str]) -> str | None:
    try:
        completed = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return completed.stdout


def find_mountpoint(device: str) -> Path | None:
    output = _run(["findmnt", "--json", device])
    if not output:
        return None
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return None
    for mount in data.get("filesystems", []):
        target = str(mount.get("target") or "").strip()
        if not target:
            continue
        mountpoint = Path(target)
        if mountpoint.is_dir():
            return mountpoint
    return None


def _clean_video_title(title: str) -> str:
    value = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().strip()
    for fragment in (" - Blu-rayTM", " Blu-rayTM", " - BLU-RAYTM", " - BLU-RAY", " - Blu-ray"):
        value = value.replace(fragment, "")
    return " ".join(value.split())


def _bluray_title(mountpoint: Path) -> tuple[str, str] | None:
    xml_file = mountpoint / "BDMV" / "META" / "DL" / "bdmt_eng.xml"
    if not xml_file.exists():
        return None
    try:
        root = ET.parse(xml_file).getroot()
    except ET.ParseError:
        return None
    title = ""
    for node in root.iter():
        if node.tag.endswith("name") and node.text:
            title = node.text
            break
    if not title:
        return None
    year = datetime.fromtimestamp(xml_file.stat().st_mtime).strftime("%Y")
    return (_clean_video_title(title), year)


def _dvd_label(device: str, env: Mapping[str, str]) -> str | None:
    for key in ("ID_FS_LABEL", "ID_FS_LABEL_ENC"):
        value = str(env.get(key) or "").strip()
        if value:
            return value.replace("_", " ").strip()
    output = _run(["blkid", "-o", "value", "-s", "LABEL", device])
    if output:
        return output.strip().replace("_", " ").strip()
    return None


def identify_video_metadata(device: str, env: Mapping[str, str]) -> dict[str, str]:
    mounted = find_mountpoint(device)
    mounted_here = False
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if mounted is None:
        temp_dir = tempfile.TemporaryDirectory(prefix="jack-mount-")
        target = Path(temp_dir.name)
        if _run(["mount", "--source", device, "--target", str(target), "-o", "ro"]) is None:
            temp_dir.cleanup()
            return {}
        mounted = target
        mounted_here = True
    if mounted is None:
        return {}
    try:
        metadata: dict[str, str] = {}
        if (mounted / "BDMV").is_dir():
            bluray = _bluray_title(mounted)
            if bluray:
                title, year = bluray
                if title:
                    metadata["title"] = title
                metadata["disctype"] = "bluray"
                metadata["year"] = year
            else:
                metadata["disctype"] = "bluray"
        elif (mounted / "VIDEO_TS").is_dir():
            metadata["disctype"] = "dvd"
            label = _dvd_label(device, env)
            if label:
                metadata["title"] = _clean_video_title(label)
        return metadata
    finally:
        if mounted_here:
            _run(["umount", str(mounted)])
        if temp_dir is not None:
            temp_dir.cleanup()


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
        ]
    target = "all"
    try:
        metadata = json.loads(str(job.get("metadata_json") or "{}"))
    except json.JSONDecodeError:
        metadata = {}
    if isinstance(metadata, dict):
        selected = metadata.get("selected_tracks")
        if isinstance(selected, list):
            track_ids = [str(int(track)) for track in selected if str(track).isdigit()]
            if track_ids:
                target = ",".join(track_ids)
    return ["makemkvcon", "mkv", f"dev:{device}", target, str(output_dir)]


def discover_makemkv_tracks(device: str) -> list[dict[str, object]]:
    try:
        completed = subprocess.run(
            ["makemkvcon", "--robot", "--messages=-stdout", "info", f"dev:{device}"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    by_title: dict[int, dict[str, object]] = {}
    for line in completed.stdout.splitlines():
        if not line.startswith("TINFO:"):
            continue
        fields = list(reader([line.removeprefix("TINFO:")], skipinitialspace=False))[0]
        if len(fields) < 4:
            continue
        try:
            title_id = int(fields[0])
            info_id = int(fields[1])
        except ValueError:
            continue
        value = fields[3].strip()
        track = by_title.setdefault(title_id, {"id": title_id, "name": f"Title {title_id}"})
        if info_id == 27 and value:
            track["name"] = value
        elif info_id == 9 and value:
            track["duration"] = value
        elif info_id == 8 and value.isdigit():
            track["chapters"] = int(value)
        elif info_id == 11 and value.isdigit():
            track["filesize_bytes"] = int(value)
    return [by_title[key] for key in sorted(by_title)]


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
