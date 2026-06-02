from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jack import __main__
from jack import container
from jack.config import Config
from jack.ripper import VIDEO_TRACK_SCAN_SOURCE, JobRunner, build_command, classify_disc, discover_makemkv_tracks, identify_video_metadata
from jack.store import StateStore, parse_metadata


class DiscClassificationTests(unittest.TestCase):
    def test_audio_disc_is_detected_from_udev_env(self) -> None:
        self.assertEqual(
            classify_disc({"ID_CDROM_MEDIA_TRACK_COUNT_AUDIO": "10", "ID_CDROM_MEDIA_TRACK_COUNT_DATA": "0"}),
            "audio",
        )

    def test_video_disc_is_default(self) -> None:
        self.assertEqual(classify_disc({}), "video")

    def test_identify_bluray_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mount = Path(tmp)
            xml_path = mount / "BDMV" / "META" / "DL" / "bdmt_eng.xml"
            xml_path.parent.mkdir(parents=True, exist_ok=True)
            xml_path.write_text(
                "<disclib><discinfo><title><name>EXAMPLE MOVIE - BLU-RAY</name></title></discinfo></disclib>",
                encoding="utf-8",
            )
            with mock.patch("jack.ripper.find_mountpoint", return_value=mount):
                metadata = identify_video_metadata("/dev/sr0", {})
            self.assertEqual(metadata["disctype"], "bluray")
            self.assertEqual(metadata["title"], "EXAMPLE MOVIE")

    def test_identify_dvd_metadata_uses_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mount = Path(tmp)
            (mount / "VIDEO_TS").mkdir(parents=True, exist_ok=True)
            with mock.patch("jack.ripper.find_mountpoint", return_value=mount):
                metadata = identify_video_metadata("/dev/sr0", {"ID_FS_LABEL": "MY_DISC_TITLE"})
            self.assertEqual(metadata, {"disctype": "dvd", "title": "MY DISC TITLE"})


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

    def test_video_track_scan_job_populates_drive_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            store = StateStore(base / "jack.db")
            store.upsert_drive("/dev/sr0", "video", metadata_json='{"title":"Movie"}')
            job_id = store.enqueue_job(
                "/dev/sr0",
                "video",
                metadata_json='{"title":"Movie"}',
                source=VIDEO_TRACK_SCAN_SOURCE,
            )
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
            with mock.patch("jack.ripper.discover_makemkv_tracks", return_value=[{"id": 0, "name": "Main"}]):
                runner._run_job(job_id)
            job = store.get_job(job_id)
            self.assertEqual(job["status"], "done")
            drive = store.get_drive("/dev/sr0")
            self.assertEqual(
                json.loads(str(drive["metadata_json"])),
                {"makemkv_tracks": [{"id": 0, "name": "Main"}], "selected_tracks": [0], "title": "Movie"},
            )

    def test_video_track_scan_clears_starting_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            store = StateStore(base / "jack.db")
            store.upsert_drive("/dev/sr0", "video", metadata_json='{"title":"Movie"}')
            job_id = store.enqueue_job(
                "/dev/sr0",
                "video",
                metadata_json='{"title":"Movie"}',
                source=VIDEO_TRACK_SCAN_SOURCE,
            )
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
            runner.starting.add("/dev/sr0")
            with mock.patch("jack.ripper.discover_makemkv_tracks", return_value=[{"id": 0, "name": "Main"}]):
                runner._run_job(job_id)
            self.assertNotIn("/dev/sr0", runner.starting)


class CommandTests(unittest.TestCase):
    def test_audio_command_uses_whipper(self) -> None:
        job = {"device": "/dev/sr0", "disc_type": "audio"}
        self.assertEqual(
            build_command(job, Path("/tmp/out")),
            ["whipper", "cd", "rip", "--device", "/dev/sr0", "--output-directory", "/tmp/out"],
        )

    def test_udev_event_applies_video_identification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {}, clear=True):
                with mock.patch("jack.__main__.load_config") as load_config:
                    load_config.return_value = Config(
                        state_dir=Path(tmp),
                        output_dir=Path(tmp) / "output",
                        host="127.0.0.1",
                        port=8080,
                        webhook_url=None,
                        webhook_success_url=None,
                        webhook_error_url=None,
                        poll_interval=0.1,
                    )
                    with mock.patch("jack.__main__.identify_video_metadata", return_value={"title": "Movie", "disctype": "dvd"}):
                        code = __main__.command_udev(mock.Mock(device="/dev/sr0", disc_type="video"))
            self.assertEqual(code, 0)
            store = StateStore(Path(tmp) / "jack.db")
            drive = store.get_drive("/dev/sr0")
            self.assertEqual(
                json.loads(str(drive["metadata_json"])),
                {"disctype": "dvd", "title": "Movie"},
            )
            job = store.latest_job_for_drive("/dev/sr0")
            self.assertIsNotNone(job)
            self.assertEqual(job["source"], VIDEO_TRACK_SCAN_SOURCE)

    def test_udev_event_queues_audio_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {}, clear=True):
                with mock.patch("jack.__main__.load_config") as load_config:
                    load_config.return_value = Config(
                        state_dir=Path(tmp),
                        output_dir=Path(tmp) / "output",
                        host="127.0.0.1",
                        port=8080,
                        webhook_url=None,
                        webhook_success_url=None,
                        webhook_error_url=None,
                        poll_interval=0.1,
                    )
                    code = __main__.command_udev(mock.Mock(device="/dev/sr0", disc_type="audio"))
            self.assertEqual(code, 0)
            store = StateStore(Path(tmp) / "jack.db")
            job = store.latest_job_for_drive("/dev/sr0")
            self.assertIsNotNone(job)

    def test_build_command_uses_selected_video_tracks(self) -> None:
        job = {"device": "/dev/sr0", "disc_type": "video", "metadata_json": '{"selected_tracks":[1,5]}'}
        self.assertEqual(build_command(job, Path("/tmp/out")), ["makemkvcon", "mkv", "dev:/dev/sr0", "1,5", "/tmp/out"])

    def test_build_command_accepts_sqlite_row_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "jack.db")
            store.upsert_drive("/dev/sr0", "video", metadata_json='{"selected_tracks":[2,4]}')
            job_id = store.enqueue_job("/dev/sr0", "video", metadata_json='{"selected_tracks":[2,4]}')
            job = store.get_job(job_id)
            self.assertIsNotNone(job)
            self.assertEqual(
                build_command(job, Path("/tmp/out")),  # type: ignore[arg-type]
                ["makemkvcon", "mkv", "dev:/dev/sr0", "2,4", "/tmp/out"],
            )

    def test_discover_makemkv_tracks_parses_titles(self) -> None:
        output = (
            'TINFO:0,27,0,"Main Feature"\n'
            'TINFO:0,9,0,"02:01:23"\n'
            "TINFO:0,8,0,\"16\"\n"
            "TINFO:1,27,0,\"Bonus\"\n"
        )
        completed = mock.Mock(stdout=output, stderr="", returncode=0)
        with mock.patch("subprocess.run", return_value=completed):
            tracks = discover_makemkv_tracks("/dev/sr0")
        self.assertEqual(
            tracks,
            [
                {"id": 0, "name": "Main Feature", "duration": "02:01:23", "chapters": 16},
                {"id": 1, "name": "Bonus"},
            ],
        )


class ContainerRunnerTests(unittest.TestCase):
    @mock.patch("jack.container.os.execvp")
    @mock.patch("jack.container._run")
    def test_container_starts_udev_and_execs_serve(self, run_mock: mock.Mock, execvp_mock: mock.Mock) -> None:
        container.main()
        self.assertEqual(
            run_mock.call_args_list,
            [
                mock.call("/lib/systemd/systemd-udevd", "--daemon"),
                mock.call("udevadm", "control", "--reload"),
                mock.call("udevadm", "trigger", "--subsystem-match=block", "--action=change"),
            ],
        )
        execvp_mock.assert_called_once_with(container.sys.executable, [container.sys.executable, "-m", "jack", "serve"])


class PackagingTests(unittest.TestCase):
    def test_udev_rule_uses_media_state_filter(self) -> None:
        rules_path = Path(__file__).resolve().parents[1] / "packaging" / "99-jack.rules"
        rules = rules_path.read_text(encoding="utf-8")
        self.assertIn('ENV{ID_CDROM_MEDIA_STATE}!="blank"', rules)
        self.assertIn('KERNEL=="sr[0-9]*"', rules)
        self.assertNotIn('ENV{DISK_MEDIA_CHANGE}=="1"', rules)


if __name__ == "__main__":
    unittest.main()
