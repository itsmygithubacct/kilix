"""config/apprun.py process cleanup regressions, without starting X/ffmpeg."""
import os

import harness  # noqa: F401  (sets config/ on sys.path)
import apprun


class FakeStdout:
    def __init__(self):
        self.rfd, self.wfd = os.pipe()
        self.closed = False

    def fileno(self):
        return self.rfd

    def close(self):
        if self.closed:
            return
        self.closed = True
        for fd in (self.rfd, self.wfd):
            try:
                os.close(fd)
            except OSError:
                pass


class FakeProc:
    def __init__(self):
        self.stdout = FakeStdout()
        self.stdin = None
        self.stderr = None
        self._rc = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self._rc

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self._rc = 0
        return self._rc

    def kill(self):
        self.killed = True
        self._rc = -9


old = FakeProc()
apprun._stop_proc(old)
assert old.terminated
assert old.stdout.closed

pane = object.__new__(apprun.AppPane)
pane.ff = FakeProc()
old_capture = pane.ff
pane.ffbuf = bytearray(b"partial")
pane.app_w = pane.app_h = 4
pane.disp = ":99"

new_capture = FakeProc()
orig_popen = apprun.subprocess.Popen
try:
    apprun.subprocess.Popen = lambda *_args, **_kw: new_capture
    pane._spawn_capture(2)
    assert old_capture.terminated
    assert old_capture.stdout.closed
    assert pane.ff is new_capture
    assert pane.ffbuf == bytearray()
finally:
    apprun.subprocess.Popen = orig_popen
    new_capture.stdout.close()

print("test_apprun OK")
