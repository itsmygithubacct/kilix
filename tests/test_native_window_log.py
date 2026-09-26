import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/native-window-log.py'
spec = importlib.util.spec_from_file_location('native_window_log', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ProcessTests(unittest.TestCase):
    def test_process_chain_preserves_argument_boundaries_and_handles_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            p = proc / '42'
            p.mkdir()
            (p / 'status').write_text('Name:\tviewer\nPPid:\t21\n')
            (p / 'cmdline').write_bytes(b'viewer\0file with spaces.pdf\0')
            (p / 'exe').symlink_to('/usr/bin/viewer')
            (p / 'cwd').symlink_to('/tmp')
            result = module.process_chain(42, proc)
            self.assertEqual(result[0]['argv'], ['viewer', 'file with spaces.pdf'])
            self.assertEqual(result[0]['exe'], '/usr/bin/viewer')
            self.assertEqual(result[1], {'pid': 21, 'unavailable': True})

    def test_private_rotated_json_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.umask(0o077)
            logger = module.make_logger(Path(tmp))
            handler = logger.handlers[-1]
            try:
                handler.maxBytes = 80
                for i in range(20):
                    logger.info(json.dumps({'item': i, 'argv': ['a\nb']}))
                for path in Path(tmp).glob('native-windows.jsonl*'):
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                    for line in path.read_text().splitlines():
                        json.loads(line)
                self.assertLessEqual(len(list(Path(tmp).glob('*.jsonl*'))), 5)
            finally:
                logger.removeHandler(handler)
                handler.close()
                os.umask(old)


@unittest.skipUnless(shutil.which('Xvfb') and shutil.which('openbox'), 'needs Xvfb and Openbox')
class NativeWindowIntegration(unittest.TestCase):
    def test_real_managed_windows_pid_focus_and_singleton(self):
        from Xlib import X, Xatom, display, protocol
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'state'
            children = []
            d = None
            stderr = None
            try:
                r, w = os.pipe()
                xvfb = subprocess.Popen(['Xvfb', '-displayfd', str(w), '-screen', '0', '800x600x24', '-nolisten', 'tcp'],
                                        pass_fds=(w,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                children.append(xvfb)
                os.close(w)
                with os.fdopen(r) as stream:
                    number = stream.readline().strip()
                env = dict(os.environ, DISPLAY=':' + number)
                d = display.Display(env['DISPLAY'])
                root = d.screen().root
                atom = d.intern_atom
                children.append(subprocess.Popen(['openbox'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                def wait_for(predicate):
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline:
                        if predicate():
                            return
                        time.sleep(.03)
                    self.fail('timed out waiting for X11/logger state')
                wait_for(lambda: root.get_full_property(atom('_NET_SUPPORTING_WM_CHECK'), X.AnyPropertyType))
                cmd = ['/usr/bin/python3', str(SCRIPT), '--state-dir', str(state)]
                stderr = tempfile.TemporaryFile()
                logger = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=stderr)
                children.append(logger)
                wait_for(lambda: (state / 'native-windows.jsonl').exists())
                duplicate = subprocess.run(cmd, env=env, capture_output=True, timeout=5)
                self.assertEqual(duplicate.returncode, 0, duplicate.stderr)
                def window(klass):
                    win = root.create_window(5, 5, 100, 80, 0, d.screen().root_depth, X.InputOutput, X.CopyFromParent)
                    win.set_wm_class(klass, klass)
                    win.change_property(atom('WM_COMMAND'), Xatom.STRING, 8, b'fixture\0file with spaces\0')
                    win.map()
                    d.sync()
                    return win
                own = window('kilix')
                native = window('routing-fixture')
                def records():
                    return [json.loads(l) for l in (state / 'native-windows.jsonl').read_text().splitlines()]
                wait_for(lambda: any(x['window'] == hex(native.id) and x['event'] == 'opened' for x in records()))
                opened = next(x for x in records() if x['window'] == hex(native.id) and x['event'] == 'opened')
                self.assertEqual(opened['wm_command'], ['fixture', 'file with spaces'])
                self.assertEqual(opened['processes'][0]['pid'], os.getpid())
                self.assertEqual(opened['pid_source'], 'XRes')  # no _NET_WM_PID was set
                def activate(win):
                    root.send_event(protocol.event.ClientMessage(window=win, client_type=atom('_NET_ACTIVE_WINDOW'),
                        data=(32, [2, X.CurrentTime, 0, 0, 0])), event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
                    d.flush()
                    wait_for(lambda: int(root.get_full_property(atom('_NET_ACTIVE_WINDOW'), X.AnyPropertyType).value[0]) == win.id)
                activate(own)
                time.sleep(.1)
                before = sum(x['window'] == hex(native.id) and x['event'] == 'focused' for x in records())
                activate(native)
                wait_for(lambda: sum(x['window'] == hex(native.id) and x['event'] == 'focused' for x in records()) > before)
                native.change_property(atom('WM_COMMAND'), Xatom.STRING, 8, b'fixture\0second document\0')
                d.flush()
                wait_for(lambda: any(x['event'] == 'metadata' and x['wm_command'] == ['fixture', 'second document'] for x in records()))
                self.assertFalse(any(x['window'] == hex(own.id) for x in records()))
                self.assertIsNone(logger.poll())
            finally:
                if d:
                    d.close()
                for p in reversed(children):
                    p.terminate()
                    try:
                        p.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        p.kill()
                        p.wait()
                if stderr:
                    stderr.close()


if __name__ == '__main__':
    unittest.main()
