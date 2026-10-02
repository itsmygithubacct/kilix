#!/usr/bin/python3
"""Dedicated Linux owner for bootstrap commands before provider acquisition."""
import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

stopping = False


def stop(_signal, _frame):
    global stopping
    stopping = True


def reap():
    children = Path(f'/proc/self/task/{os.getpid()}/children')
    while True:
        for value in children.read_text().split():
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
    libc = ctypes.CDLL(None,use_errno=True)
    if (libc.prctl(36,1,0,0,0) != 0 or libc.prctl(1,signal.SIGTERM,0,0,0) != 0
            or libc.prctl(38,1,0,0,0) != 0 or os.getppid() != parent or parent == 1):
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
