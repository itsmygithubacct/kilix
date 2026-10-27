"""Ordered real-X input and responsiveness in the pane's actual event loop."""
import json
import os
import select
import signal
import socket
import threading
import time
from types import SimpleNamespace

import private_input_fixture as fixture
import apprun
import xinject
from Xlib import XK


class NativeInput(fixture.NativeInputFixture):
    def test_rebind_wait_keeps_the_old_actual_mapping_and_fifo_position(self):
        empty = next(first+i for first in [self.xd.display.info.min_keycode]
                     for i,row in enumerate(self.original) if not any(row))
        self.inj._spare = [empty]
        self.inj.queue_input('paste','漢界')
        started = time.monotonic()
        self.inj.dispatch_pending()
        self.assertEqual(self.xd.get_keyboard_mapping(empty,1)[0][0],0x01006f22)
        self.assertEqual(self.inj._input[0][2],1)
        self.assertGreater(self.inj.input_timeout(),0)
        seen = []
        self.app.sync();self.events(seen)
        self.assertEqual(seen,[0x01006f22])
        while self.inj._input:
            time.sleep(self.inj.input_timeout())
            self.inj.dispatch_pending()
        self.assertGreaterEqual(time.monotonic()-started,xinject.REBIND_DELAY)
        self.app.sync();self.events(seen)
        self.assertEqual(seen,[0x01006f22,0x0100754c])
        self.assertEqual(self.held(),[])


    def test_held_scratch_binding_survives_ordered_paste_and_release(self):
        empty = [self.xd.display.info.min_keycode+i for i,row in enumerate(self.original) if not any(row)]
        self.assertGreaterEqual(len(empty),2)
        self.inj._spare = empty[:2]
        self.inj.chord('漢',xinject.MOD_ALT,1)
        held_code = self.inj._key_presses['漢']
        seen = []
        self.app.sync();self.events(seen)
        text = '界文新字Ab!\t'
        self.inj.queue_input('paste',text)
        self.inj.queue_input('chord','漢',0,3)
        while self.inj._input:
            self.inj.dispatch_pending()
            self.xd.sync();self.app.sync();self.events(seen)
            self.assertEqual(self.xd.get_keyboard_mapping(held_code,1)[0][0],0x01006f22)
            time.sleep(self.inj.input_timeout(0.01))
        self.assertEqual(seen,[self.inj.keysym_for(c) for c in '漢'+text[:-1]]+[XK.XK_Tab])
        self.assertEqual(self.held(),[])
        self.assertFalse(self.inj._mod_holds)


    def test_focus_return_accepts_small_text_after_cancelling_an_old_paste(self):
        empty = next(self.xd.display.info.min_keycode+i for i,row in enumerate(self.original) if not any(row))
        self.inj._spare = [empty]
        self.inj.queue_input('paste','漢界文')
        self.inj.dispatch_pending()
        seen = []
        self.app.sync();self.events(seen)
        reader,writer = socket.socketpair()
        self.addCleanup(reader.close);self.addCleanup(writer.close)
        pane = self.pane(reader)
        pane.on_focus({'in':False})
        pane.on_focus({'in':True})
        pane.on_paste('Ab!/ü\n\t')
        while self.inj._input:
            self.inj.dispatch_pending()
            self.xd.sync();self.app.sync();self.events(seen)
            time.sleep(self.inj.input_timeout(.01))
        self.assertEqual(seen,[0x01006f22]+[ord(c) for c in 'Ab!/ü']+[XK.XK_Return,XK.XK_Tab])
        self.assertEqual(self.held(),[])


    def pane(self, reader):
        p = object.__new__(apprun.AppPane)
        p.inj = self.inj
        p.term = SimpleNamespace(fd=reader.fileno(),enter=lambda:None,restore=lambda:None,
                                  write=lambda *a:None,
                                  read_input=lambda:json.loads(reader.recv(100000).decode()))
        for name in ('serve','lan','hls','mse','webrtc','resized','resizable'):
            setattr(p,name,False)
        for name in ('capture','ff','last_frame','presenter','xapp','dnd'):
            setattr(p,name,None)
        p.xapp = SimpleNamespace(make_dnd_bridge=lambda *a, **kw: None, close=lambda: None)
        p.feed = SimpleNamespace(pending_fds=lambda:[])
        p.app = SimpleNamespace(poll=lambda:None)
        for name in ('start','maintain_app_window','render_status','_stop_capture'):
            setattr(p,name,lambda *a:None)
        return p


    def run_paste(self, *, cancel=False):
        reader,writer = socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        self.addCleanup(reader.close);self.addCleanup(writer.close)
        p = self.pane(reader)
        chars = ''.join(chr(0x4e00+i) for i in range(96))
        expected = [xinject.UNICODE_KEYSYM|ord(c) for c in chars]+[ord('z')]
        seen,heartbeats,errors = [],[],[]
        stopped = threading.Event()
        resize_sent,resize_seen = [],[]
        p.tick_capture = lambda *a:heartbeats.append(time.monotonic())
        p.do_resize = lambda:resize_seen.append(time.monotonic())
        def client():
            try:
                until = time.monotonic()+10
                notified = False
                while not stopped.is_set() and time.monotonic()<until:
                    if self.app.pending_events() or select.select([self.app],[],[],.001)[0]:
                        self.events(seen)
                    if not notified and len(seen)>=3:
                        resize_sent.append(time.monotonic())
                        if cancel == 'close':
                            writer.send(json.dumps([{'kind':'key','key':'q','mods':5,'event':1}]).encode())
                            return
                        os.kill(os.getpid(),signal.SIGWINCH)
                        if cancel:
                            writer.send(json.dumps([{'kind':'focus','in':False}]).encode())
                        else:
                            writer.send(json.dumps([{'kind':'key','key':'z','mods':1,'event':1},
                                                   {'kind':'key','key':'z','mods':1,'event':3}]).encode())
                        notified = True
                    if (cancel and resize_seen and time.monotonic()-resize_seen[0]>.1) or len(seen)==len(expected):
                        break
                writer.send(json.dumps([{'kind':'key','key':'q','mods':5,'event':1}]).encode())
            except BaseException as e:
                errors.append(e)
                writer.send(json.dumps([{'kind':'key','key':'q','mods':5,'event':1}]).encode())
        writer.send(json.dumps([{'kind':'paste','text':chars}]).encode())
        start = time.monotonic()
        thread = threading.Thread(target=client)
        thread.start()
        try:
            p.run()  # actual product event loop, real X input, private socket terminal fixture
        finally:
            stopped.set();thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors,[])
        elapsed = time.monotonic()-start
        latency = (time.monotonic()-resize_sent[0] if cancel == 'close' and resize_sent else
                   resize_seen[0]-resize_sent[0] if resize_seen and resize_sent else None)
        gaps = [b-a for a,b in zip([start]+heartbeats,heartbeats)]
        print(json.dumps(dict(cancel=cancel,characters=len(seen),seconds=elapsed,
                              resize_latency=latency,max_loop_gap=max(gaps,default=0))),flush=True)
        self.assertIsNotNone(latency)
        self.assertLess(latency,.15)
        self.assertLess(max(gaps),.35)
        if not cancel:
            self.assertLess(elapsed,.5)
        self.assertEqual(self.held(),[])
        if cancel:
            self.assertLess(len(seen),len(expected))
            self.assertEqual(seen,expected[:len(seen)])
            self.assertFalse(self.inj._input)
        else:
            self.assertEqual(seen,expected)


    def test_long_paste_keeps_actual_event_loop_responsive_and_ordered(self):
        self.run_paste()


    def test_headless_event_loop_has_no_local_input_injector(self):
        reader,writer = socket.socketpair()
        self.addCleanup(reader.close);self.addCleanup(writer.close)
        pane = self.pane(reader)
        pane.term = None
        del pane.inj
        pane.app = SimpleNamespace(poll=lambda:0,returncode=0)
        pane.tick_capture = lambda *a:None
        pane.run()


    def test_focus_loss_cancels_pending_paste_and_releases_held_keys(self):
        self.inj.chord('a',xinject.MOD_CTRL,1)
        self.inj.mouse({'x':1,'y':1,'b':0,'press':True},(0,0,640,480))
        self.xd.sync();self.app.sync()
        self.events([])
        self.run_paste(cancel=True)
        self.assertFalse(self.inj._mod_holds)
        self.assertFalse(self.inj._btns_down)


    def test_close_cancels_pending_paste_and_releases_held_keys(self):
        self.inj.chord('a',xinject.MOD_CTRL,1)
        self.xd.sync();self.app.sync()
        self.events([])
        self.run_paste(cancel='close')


def load_tests(loader, tests, pattern):
    return fixture.x11_sandbox.sandbox_load_tests(loader,tests,__name__)
