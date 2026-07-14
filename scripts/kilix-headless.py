#!/usr/bin/env python3
"""Run `kilix run` on a HEADLESS host (no real terminal, e.g. over SSH).

Sets KILIX_NO_PANE=1 (QW3) so apprun skips the local pane entirely — no Term,
no pane x11grab; the network tiers x11grab the display themselves, so exactly
one capture runs. The pty wrapper remains for compatibility (some hosts still
run an older apprun that insists on a pixel-size-reporting terminal); deps in
a no-sudo prefix (Debian) are picked up by sourcing stream-env.sh; on a
system-installed host (Fedora) that file is absent and system paths are used.

Usage:  scripts/kilix-headless.py [--serve|--lan] [--hls] [--mse] [--webrtc] \\
                                   [--audio] [--debug] [--size WxH] [--fps N] \\
                                   command [args…]
Read connect details from the session's connect.txt (set KILIX_SESSION=<name>
to make its runtime dir predictable).
"""
import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios

HERE = os.path.dirname(os.path.abspath(__file__))
APPRUN = os.path.abspath(os.path.join(HERE, "..", "config", "apprun.py"))

os.umask(0o077)
env = dict(os.environ, KILIX_NO_PANE="1")
storage = os.environ.get(
    "KILIX_STORAGE_HOME", os.path.expanduser("~/.local/gpu_terminal/kilix"))
envfile = os.path.join(os.environ.get("KILIX_DATA_HOME",
                                     os.path.join(storage, "data")),
                       "stream-env.sh")
if os.path.exists(envfile):          # no-sudo Debian prefix: import its exports
    out = subprocess.run(["bash", "-c", f". '{envfile}'; env"],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "=" in line and not line.startswith("BASH_FUNC"):
            k, v = line.split("=", 1)
            env[k] = v

master, slave = pty.openpty()
# 100x30 cells @ 1000x600 px — apprun's --size overrides the actual capture size.
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 1000, 600))
p = subprocess.Popen(["python3", APPRUN, *sys.argv[1:]],
                     stdin=slave, stdout=slave, stderr=slave,
                     start_new_session=True, env=env)
os.close(slave)


def _stop(*_):
    # no-pane apprun reads no input: signal it instead of injecting Ctrl+Q
    # (its SIGTERM handler exits cleanly through the supervisor teardown)
    try:
        p.terminate()
    except OSError:
        pass


signal.signal(signal.SIGINT, _stop)
signal.signal(signal.SIGTERM, _stop)
try:
    while p.poll() is None:
        r, _, _ = select.select([master], [], [], 0.5)
        if r:
            try:
                if not os.read(master, 65536):
                    break
            except OSError:
                break
finally:
    if p.poll() is None:
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            try:
                os.killpg(os.getpgid(p.pid), 9)
            except Exception:
                pass
sys.exit(p.returncode or 0)
