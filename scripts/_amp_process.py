"""Standalone Amp setup/query supervisor, never an embedding-process reaper."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import signal
import sys
import time


def run_owned(action, timeout=900):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        print("kilix amp: owned setup supervision is unavailable", file=sys.stderr)
        return 1
    stopped = False

    def stop(_number, _frame):
        nonlocal stopped
        stopped = True

    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(number, stop)
    parent = os.getppid()
    child = os.fork()
    if child == 0:
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(number, signal.SIG_DFL)
        try:
            status = action()
        except SystemExit as error:
            status = error.code if isinstance(error.code, int) else 1
        except BaseException as error:
            print(f"kilix amp: setup/query failed: {error}", file=sys.stderr)
            status = 1
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
        os._exit(status)
    status = 1
    deadline = time.monotonic() + timeout
    try:
        while not stopped and os.getppid() == parent:
            if time.monotonic() >= deadline:
                stopped = True
                break
            pid, raw = os.waitpid(child, os.WNOHANG)
            if pid:
                status = os.waitstatus_to_exitcode(raw)
                break
            time.sleep(.01)
    finally:
        # Kill direct owned children first. Subreaping adopts their escaped
        # descendants, which are then killed/reaped in the following rounds.
        deadline = time.monotonic() + 4
        while True:
            for task in Path('/proc/self/task').iterdir():
                for word in (task/'children').read_text().split():
                    try:
                        os.kill(int(word), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            try:
                while os.waitpid(-1, os.WNOHANG)[0]:
                    pass
            except ChildProcessError:
                return status if not stopped else 1
            if time.monotonic() >= deadline:
                print("kilix amp: owned setup cleanup was not confirmed", file=sys.stderr)
                return 1
            time.sleep(.01)
