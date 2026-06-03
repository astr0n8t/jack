from __future__ import annotations

import os
import subprocess
import sys


def _run(*command: str) -> None:
    subprocess.run(list(command), check=False)



def main() -> None:
    #_run("/usr/local/bin/udev-start", "start")
    _run("/lib/systemd/systemd-udevd", "--daemon")
    _run("udevadm", "control", "--reload")
    _run("udevadm", "trigger", "--subsystem-match=block", "--action=change")
    os.execvp(sys.executable, [sys.executable, "-m", "jack", "serve"])
