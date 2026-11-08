#!/usr/bin/env python3
"""Own a detector helper's descendants and report unproved cleanup."""
import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def children():
    return [int(pid) for pid in (Path('/proc/self/task') / str(os.getpid()) / 'children').read_text().split()]


def reap():
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def signal_group(pid, number):
    try:
        os.killpg(pid, number)
    except ProcessLookupError:
        pass


def group_alive(pid):
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    return True


def finish_group(group):
    for number in (signal.SIGTERM, signal.SIGKILL):
        signal_group(group, number)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            reap()
            if not group_alive(group):
                return True
            time.sleep(.02)
    reap()
    return not group_alive(group)


def main():
    if len(sys.argv) < 3:
        raise ValueError('expected owned marker path and command')
    marker, command = Path(sys.argv[1]), sys.argv[2:]
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), 'cannot own detector helper descendants')
    # Verify proc visibility before launching any child.
    if children():
        raise RuntimeError('unexpected pre-existing child')
    child = None
    received = None
    spawning = False
    def interrupted(number, _frame):
        nonlocal received
        if received is None:
            received = number
        if child is not None:
            signal_group(child.pid, number)
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, interrupted)
    try:
        if received is not None:
            return 128 + received
        spawning = True
        try:
            child = subprocess.Popen(command, start_new_session=True)
        finally:
            spawning = False
        if received is not None:
            signal_group(child.pid, received)
        deadline = None
        while child.poll() is None:
            if received is not None and deadline is None:
                deadline = time.monotonic() + 3
            if deadline is not None and time.monotonic() >= deadline:
                signal_group(child.pid, signal.SIGKILL)
                break
            time.sleep(.02)
        status = child.wait(timeout=3)
        group_clean = finish_group(child.pid)
        reap()
        if not group_clean or children():
            raise RuntimeError('owned detector helper descendants remain')
        if received is not None:
            return 128 + received
        return status if status >= 0 else 128 - status
    except BaseException:
        # On any uncertain cleanup, the parent must retain its source pool.
        # Even an escaped child can still have files open there.
        marker.write_text('cleanup unproved\n')
        raise


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print('detector source carrier supervisor: '+str(error), file=sys.stderr)
        raise SystemExit(125)
