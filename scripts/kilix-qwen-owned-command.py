#!/usr/bin/python3
"""Dedicated Linux owner for bootstrap commands before provider acquisition."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
from kilix_sdk import hostproc  # noqa: E402

stopping = False


def stop(_signal, _frame):
    global stopping
    stopping = True


def reap():
    while True:
        for value in hostproc.children(os.getpid()):
            try:
                os.kill(int(value),signal.SIGKILL)
            except ProcessLookupError:
                pass
        while True:
            try:
                pid,_status = os.waitpid(-1,os.WNOHANG)
            except ChildProcessError:
                return
            if pid == 0:
                break
        time.sleep(.005)


def main():
    parent = os.getppid()
    for sig in (signal.SIGTERM,signal.SIGINT):
        signal.signal(sig,stop)
    try:
        hostproc.set_child_subreaper()
        hostproc.set_parent_death_signal(signal.SIGTERM)
        hostproc.set_no_new_privs()
    except OSError:
        return 125
    if os.getppid() != parent or parent == 1:
        return 125
    try:
        process = subprocess.Popen(sys.argv[1:],stdin=subprocess.DEVNULL,start_new_session=True)
        while process.poll() is None and not stopping:
            time.sleep(.01)
        return 130 if stopping else process.returncode
    finally:
        reap()


if __name__ == '__main__':
    raise SystemExit(main())
