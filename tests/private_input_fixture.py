"""Shared authenticated private X fixture with exact owned-process cleanup."""
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'config'))
import xinject
from Xlib import X, XK, display
import x11_sandbox


class NativeInputFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        before = x11_sandbox.require_private_x11()
        cls.temp = tempfile.TemporaryDirectory(prefix='input-dispatch-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.auth = cls.root/'auth'
        cls.cookie = os.urandom(16)
        cls.write_auth(0)
        cls.log = (cls.root/'Xvfb.log').open('w')
        cls.addClassCleanup(cls.log.close)
        ready = cls.root/'display'
        with ready.open('w') as fd:
            cls.server = subprocess.Popen(
                ['Xvfb', '-displayfd', str(fd.fileno()), '-screen', '0', '640x480x24',
                 '-nolisten', 'tcp', '-noreset', '-auth', str(cls.auth)],
                pass_fds=(fd.fileno(),), stdout=subprocess.DEVNULL, stderr=cls.log)
        cls.pidfd = os.pidfd_open(cls.server.pid)
        cls.addClassCleanup(cls.stop_server)
        until = time.monotonic()+5
        while not ready.stat().st_size:
            if cls.server.poll() is not None or time.monotonic() >= until:
                raise RuntimeError('owned X server failed: '+(cls.root/'Xvfb.log').read_text())
            time.sleep(.01)
        number = int(ready.read_text())
        x11_sandbox.confirm_our_socket(number, before)
        cls.write_auth(number)
        os.environ['XAUTHORITY'] = str(cls.auth)
        cls.addClassCleanup(os.environ.pop,'XAUTHORITY',None)
        cls.name = ':'+str(number)


    @classmethod
    def write_auth(cls, number):
        fields = (socket.gethostname().encode(), str(number).encode(), b'MIT-MAGIC-COOKIE-1', cls.cookie)
        cls.auth.write_bytes(struct.pack('!H',256)+b''.join(struct.pack('!H',len(f))+f for f in fields))
        cls.auth.chmod(0o600)


    @classmethod
    def stop_server(cls):
        try:
            signal.pidfd_send_signal(cls.pidfd, signal.SIGTERM)
            cls.server.wait(timeout=5)
        finally:
            if cls.server.poll() is None:
                signal.pidfd_send_signal(cls.pidfd, signal.SIGKILL)
                cls.server.wait(timeout=5)
            os.close(cls.pidfd)


    def setUp(self):
        self.xd = display.Display(self.name)
        self.addCleanup(self.xd.close)
        x11_sandbox.claim_display(self.xd)
        self.app = display.Display(self.name)
        self.addCleanup(self.app.close)
        first = self.xd.display.info.min_keycode
        count = self.xd.display.info.max_keycode-first+1
        self.original = self.xd.get_keyboard_mapping(first,count)
        self.addCleanup(self.restore_map,first)
        self.inj = xinject.Injector(self.xd,640,480)
        self.addCleanup(self.inj.release_all)
        screen = self.app.screen()
        self.window = screen.root.create_window(0,0,320,200,0,screen.root_depth,
                                                event_mask=X.KeyPressMask|X.KeyReleaseMask)
        self.addCleanup(self.window.destroy)
        self.window.map()
        self.window.set_input_focus(X.RevertToParent,X.CurrentTime)
        self.app.sync()
        self.events([])


    def restore_map(self,first):
        self.xd.change_keyboard_mapping(first,self.original)
        self.xd.sync()


    def events(self, out):
        while self.app.pending_events():
            ev = self.app.next_event()
            if ev.type == X.MappingNotify:
                self.app.refresh_keyboard_mapping(ev)
            elif ev.type == X.KeyPress:
                sym = self.app.keycode_to_keysym(ev.detail,1 if ev.state & X.ShiftMask else 0)
                if sym not in {XK.XK_Shift_L,XK.XK_Control_L,XK.XK_Alt_L,XK.XK_Super_L}:
                    out.append(sym)


    def held(self):
        bitmap = bytes(self.xd.query_keymap())
        return [i for i in range(256) if bitmap[i//8] & (1 << (i%8))]
