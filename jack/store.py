from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS drives (
    device TEXT PRIMARY KEY,
    disc_type TEXT NOT NULL DEFAULT 'unknown',
    status TEXT NOT NULL DEFAULT 'idle',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    last_error TEXT,
    last_seen TEXT NOT NULL,
    active_job_id INTEGER,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device TEXT NOT NULL,
    disc_type TEXT NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    output_path TEXT,
    command TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def recover_interrupted_jobs(self) -> None:
        now = utcnow()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, device FROM jobs WHERE status = 'running'"
            ).fetchall()
            if not rows:
                return
            conn.execute(
                "UPDATE jobs SET status='error', error=?, updated_at=?, finished_at=? WHERE status='running'",
                ("Service restarted while job was active", now, now),
            )
            for row in rows:
                conn.execute(
                    "UPDATE drives SET status='idle', active_job_id=NULL, last_error=?, updated_at=? WHERE device=?",
                    ("Service restarted while job was active", now, row["device"]),
                )

    def upsert_drive(
        self,
        device: str,
        disc_type: str,
        *,
        status: str | None = None,
        metadata_json: str | None = None,
        last_error: str | None = None,
        active_job_id: int | None = None,
    ) -> None:
        now = utcnow()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT metadata_json, status, last_error, active_job_id FROM drives WHERE device=?",
                (device,),
            ).fetchone()
            metadata_json = metadata_json if metadata_json is not None else (row["metadata_json"] if row else "{}")
            status = status if status is not None else (row["status"] if row else "idle")
            last_error = last_error if last_error is not None else (row["last_error"] if row else None)
            active_job_id = active_job_id if active_job_id is not None else (row["active_job_id"] if row else None)
            conn.execute(
                """
                INSERT INTO drives(device, disc_type, status, metadata_json, last_error, last_seen, active_job_id, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device) DO UPDATE SET
                    disc_type=excluded.disc_type,
                    status=excluded.status,
                    metadata_json=excluded.metadata_json,
                    last_error=excluded.last_error,
                    last_seen=excluded.last_seen,
                    active_job_id=excluded.active_job_id,
                    updated_at=excluded.updated_at
                """,
                (device, disc_type, status, metadata_json, last_error, now, active_job_id, now),
            )

    def set_drive_metadata(self, device: str, metadata_json: str) -> None:
        now = utcnow()
        with self.connect() as conn:
            conn.execute(
                "UPDATE drives SET metadata_json=?, updated_at=? WHERE device=?",
                (metadata_json, now, device),
            )
            conn.execute(
                "UPDATE jobs SET metadata_json=?, updated_at=? WHERE device=? AND status='queued'",
                (metadata_json, now, device),
            )

    def enqueue_job(self, device: str, disc_type: str, metadata_json: str = "{}", source: str = "udev", force: bool = False) -> int:
        now = utcnow()
        with self.connect() as conn:
            if not force:
                existing = conn.execute(
                    "SELECT id FROM jobs WHERE device=? AND status IN ('queued', 'running') ORDER BY id DESC LIMIT 1",
                    (device,),
                ).fetchone()
                if existing:
                    return int(existing["id"])
            cur = conn.execute(
                """
                INSERT INTO jobs(device, disc_type, status, source, metadata_json, created_at, updated_at)
                VALUES(?, ?, 'queued', ?, ?, ?, ?)
                """,
                (device, disc_type, source, metadata_json, now, now),
            )
            job_id = int(cur.lastrowid)
            conn.execute(
                "UPDATE drives SET status='queued', disc_type=?, active_job_id=?, updated_at=? WHERE device=?",
                (disc_type, job_id, now, device),
            )
            return job_id

    def claim_queued_jobs(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at ASC"
            ).fetchall()

    def mark_job_running(self, job_id: int, command: str) -> sqlite3.Row:
        now = utcnow()
        with self.connect() as conn:
            conn.execute(
                "UPDATE jobs SET status='running', command=?, started_at=?, updated_at=? WHERE id=?",
                (command, now, now, job_id),
            )
            job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            conn.execute(
                "UPDATE drives SET status='running', active_job_id=?, last_error=NULL, updated_at=? WHERE device=?",
                (job_id, now, job["device"]),
            )
            return job

    def finish_job(self, job_id: int, status: str, *, output_path: str | None = None, error: str | None = None) -> sqlite3.Row:
        now = utcnow()
        with self.connect() as conn:
            conn.execute(
                "UPDATE jobs SET status=?, output_path=COALESCE(?, output_path), error=?, finished_at=?, updated_at=? WHERE id=?",
                (status, output_path, error, now, now, job_id),
            )
            job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            drive_status = "idle" if status == "done" else "error"
            conn.execute(
                "UPDATE drives SET status=?, active_job_id=NULL, last_error=?, updated_at=? WHERE device=?",
                (drive_status, error, now, job["device"]),
            )
            return job

    def set_job_error(self, job_id: int, message: str) -> sqlite3.Row:
        return self.finish_job(job_id, "error", error=message)

    def get_drive(self, device: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM drives WHERE device=?",
                (device,),
            ).fetchone()

    def get_job(self, job_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    def latest_job_for_drive(self, device: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM jobs WHERE device=? ORDER BY id DESC LIMIT 1",
                (device,),
            ).fetchone()

    def list_drives(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM drives ORDER BY device ASC"
            ).fetchall()

    def get_setting(self, key: str, default: str) -> str:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if row:
                return row["value"]
            return default

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def restart_job(self, job_id: int) -> int:
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(f"Unknown job {job_id}")
        drive = self.get_drive(job["device"])
        metadata_json = drive["metadata_json"] if drive else job["metadata_json"]
        return self.enqueue_job(job["device"], job["disc_type"], metadata_json=metadata_json, source="restart", force=True)


def parse_metadata(text: str) -> dict[str, str]:
    text = (text or "").strip() or "{}"
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("metadata must be a JSON object")
    return {str(key): str(value) for key, value in data.items() if value not in (None, "")}
