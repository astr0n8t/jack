from __future__ import annotations

import argparse
import json
import os
import sys

from .config import load_config
from .ripper import VIDEO_TRACK_SCAN_SOURCE, JobRunner, classify_disc, identify_video_metadata
from .store import StateStore
from .web import serve



def command_serve(_args: argparse.Namespace) -> int:
    os.environ["HOME"] = "/root"
    config = load_config()
    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    store = StateStore(config.state_dir / "jack.db")
    store.recover_interrupted_jobs()
    runner = JobRunner(config, store)
    serve(config, store, runner)
    return 0



def command_udev(args: argparse.Namespace) -> int:
    config = load_config()
    store = StateStore(config.state_dir / "jack.db")
    disc_type = args.disc_type or classify_disc(os.environ)
    device = args.device
    metadata: dict[str, object] = {}
    if disc_type == "video":
        metadata.update(identify_video_metadata(device, os.environ))
    metadata_json = json.dumps(metadata, indent=2, sort_keys=True)
    store.upsert_drive(device, disc_type, status="idle", metadata_json=metadata_json)
    if disc_type == "video":
        job_id = store.enqueue_job(device, disc_type, metadata_json=metadata_json, source=VIDEO_TRACK_SCAN_SOURCE)
        print(job_id)
        return 0
    job_id = store.enqueue_job(device, disc_type, metadata_json=metadata_json, source="udev")
    print(job_id)
    return 0



def command_scan(args: argparse.Namespace) -> int:
    config = load_config()
    store = StateStore(config.state_dir / "jack.db")
    try:
        parsed = json.loads(args.metadata or "{}")
    except json.JSONDecodeError as exc:
        print(f"Invalid metadata JSON: {exc}", file=sys.stderr)
        return 1
    if not isinstance(parsed, dict):
        print("Invalid metadata JSON: must be a JSON object", file=sys.stderr)
        return 1
    metadata_json = json.dumps(parsed, indent=2, sort_keys=True)
    store.upsert_drive(args.device, args.disc_type, status="idle", metadata_json=metadata_json)
    job_id = store.enqueue_job(args.device, args.disc_type, metadata_json=metadata_json, source="manual", force=True)
    print(job_id)
    return 0



def command_container(_args: argparse.Namespace) -> int:
    from .container import main

    main()
    return 0



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jack")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Run the web UI and job dispatcher")
    serve_parser.set_defaults(func=command_serve)

    udev_parser = subparsers.add_parser("udev-event", help="Queue a job from a udev rule")
    udev_parser.add_argument("--device", required=True)
    udev_parser.add_argument("--disc-type", choices=["audio", "video"])
    udev_parser.set_defaults(func=command_udev)

    scan_parser = subparsers.add_parser("scan", help="Queue a manual job")
    scan_parser.add_argument("--device", required=True)
    scan_parser.add_argument("--disc-type", required=True, choices=["audio", "video"])
    scan_parser.add_argument("--metadata")
    scan_parser.set_defaults(func=command_scan)

    container_parser = subparsers.add_parser("container", help="Start udev and then run the web service")
    container_parser.set_defaults(func=command_container)
    return parser



def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
