from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jack.config import Config
from jack.ripper import JobRunner, build_command, classify_disc, ffmpeg_metadata_args
from jack.store import StateStore, parse_metadata


class DiscClassificationTests(unittest.TestCase):
    def test_audio_disc_is_detected_from_udev_env(self) -> None:
        self.assertEqual(
            classify_disc({"ID_CDROM_MEDIA_TRACK_COUNT_AUDIO": "10", "ID_CDROM_MEDIA_TRACK_COUNT_DATA": "0"}),
            "audio",
        )

    def test_video_disc_is_default(self) -> None:
        self.assertEqual(classify_disc({}), "video")


class StoreTests(unittest.TestCase):
    def test_restart_job_uses_updated_drive_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "jack.db")
            store.upsert_drive("/dev/sr0", "audio", metadata_json='{"artist":"one"}')
            job_id = store.enqueue_job("/dev/sr0", "audio", metadata_json='{"artist":"one"}')
            store.set_drive_metadata("/dev/sr0", '{"artist":"two"}')
            restarted = store.restart_job(job_id)
            self.assertEqual(json.loads(store.get_job(restarted)["metadata_json"]), {"artist": "two"})

    def test_parse_metadata_requires_object(self) -> None:
        with self.assertRaises(ValueError):
            parse_metadata("[]")


class RunnerTests(unittest.TestCase):
    def test_missing_binary_marks_job_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            store = StateStore(base / "jack.db")
            store.upsert_drive("/dev/sr0", "audio", metadata_json="{}")
            job_id = store.enqueue_job("/dev/sr0", "audio", metadata_json="{}")
            runner = JobRunner(
                Config(
                    state_dir=base,
                    output_dir=base / "output",
                    host="127.0.0.1",
                    port=8080,
                    webhook_url=None,
                    webhook_success_url=None,
                    webhook_error_url=None,
                    poll_interval=0.1,
                ),
                store,
            )
            with mock.patch("subprocess.Popen", side_effect=FileNotFoundError("missing")):
                runner._run_job(job_id)
            self.assertEqual(store.get_job(job_id)["status"], "error")


class CommandTests(unittest.TestCase):
    def test_audio_command_uses_whipper(self) -> None:
        job = {"device": "/dev/sr0", "disc_type": "audio"}
        self.assertEqual(
            build_command(job, Path("/tmp/out")),
            ["whipper", "cd", "rip", "--device", "/dev/sr0", "--output-directory", "/tmp/out"],
        )

    def test_ffmpeg_metadata_args(self) -> None:
        self.assertEqual(ffmpeg_metadata_args({"artist": "A", "album": "B"}), ["-metadata", "artist=A", "-metadata", "album=B"])


if __name__ == "__main__":
    unittest.main()
