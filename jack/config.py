from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    state_dir: Path
    output_dir: Path
    host: str
    port: int
    webhook_url: str | None
    webhook_success_url: str | None
    webhook_error_url: str | None
    poll_interval: float
    chown_user: str | None
    chown_group: str | None



def load_config() -> Config:
    state_dir = Path(os.environ.get("JACK_STATE_DIR", str(Path.cwd() / ".jack")))
    output_dir = Path(os.environ.get("JACK_OUTPUT_DIR", str(Path.cwd() / "output")))
    return Config(
        state_dir=state_dir,
        output_dir=output_dir,
        host=os.environ.get("JACK_HOST", "0.0.0.0"),
        port=int(os.environ.get("JACK_PORT", "8080")),
        webhook_url=os.environ.get("JACK_WEBHOOK_URL"),
        webhook_success_url=os.environ.get("JACK_WEBHOOK_SUCCESS_URL"),
        webhook_error_url=os.environ.get("JACK_WEBHOOK_ERROR_URL"),
        poll_interval=float(os.environ.get("JACK_POLL_INTERVAL", "2")),
        chown_user=os.environ.get("JACK_CHOWN_USER"),
        chown_group=os.environ.get("JACK_CHOWN_GROUP"),
    )
