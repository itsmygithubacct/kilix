"""Existing foreground identity must not consume an explicit root request."""
import json
import os
from pathlib import Path
import subprocess
import time
import unittest

import test_tui_remote_root as remote


class ExistingRootTests(unittest.TestCase):
    def setUp(self):
        self.case = remote.RemoteRootTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.trace = self.case.base / 'focus-trace.jsonl'
        self.case.env['TEST_FOCUS_TRACE'] = str(self.trace)
        relay = self.case.base / 'prebuilt/bin/kitten'
        original = relay.read_text()
        original = original.replace(
            'if "ls" in args: print("[]");sys.exit(0)\n',
            'with open(os.environ["TEST_FOCUS_TRACE"], "a") as trace: '
            'trace.write(json.dumps(args)+"\\n")\n'
            'if "ls" in args:\n'
            ' pid=int(os.environ["TEST_EXISTING_PID"])\n'
            ' cmd=Path("/proc",str(pid),"cmdline").read_bytes().split(b"\\0")[:-1]\n'
            ' print(json.dumps([{"tabs":[{"windows":[{"id":7654,'
            '"foreground_processes":[{"pid":pid,"cmdline":[os.fsdecode(a) for a in cmd]}]}]}]}]))\n'
            ' sys.exit(0)\n'
            'if "focus-window" in args: sys.exit(0)\n')
        relay.write_text(original)

    def observe(self, alias, *, explicit, matched):
        case = self.case
        self.trace.unlink(missing_ok=True)
        app = case.base / ('existing/kilix-tui/main.py' if matched else 'existing/other/main.py')
        app.parent.mkdir(parents=True, exist_ok=True)
        state = case.base / 'existing-state.json'
        state.unlink(missing_ok=True)
        app.write_text('import json,os,time\nfrom pathlib import Path\n'
            'Path(os.environ["TEST_EXISTING_STATE"]).write_text(json.dumps('
            '{"pid":os.getpid(),"root":os.environ["KILIX_CONTENT_ROOT"]}))\n'
            'time.sleep(60)\n')
        old = str(case.base / 'different-active-root')
        process = subprocess.Popen(['/usr/bin/python3', str(app)], env=dict(
            case.env, KILIX_CONTENT_ROOT=old, TEST_EXISTING_STATE=str(state)))
        try:
            end = time.monotonic() + 3
            while not state.exists():
                self.assertLess(time.monotonic(), end)
                time.sleep(.005)
            self.assertEqual(json.loads(state.read_text()), dict(pid=process.pid, root=old))
            case.env['TEST_EXISTING_PID'] = str(process.pid)
            wanted = str(case.base) + "/unused/../new 'root' $HOME\t\n\n"
            tail = ['--screenshot', 'requested-frame', '', 'two\nlines\n']
            args = [alias, '--content-root', wanted, *tail] if explicit else [alias, *tail]
            result = case.launch(args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIsNone(process.poll(), 'unrelated existing process was disturbed')
            trace = [json.loads(line) for line in self.trace.read_text().splitlines()]
            focus = [row for row in trace if 'focus-window' in row]
            launches = [row for row in trace if 'launch' in row]
            if not explicit and matched:
                self.assertEqual(len(focus), 1)
                self.assertIn('id:7654', focus[0])
                self.assertFalse(launches)
                self.assertFalse(case.output.exists())
            else:
                self.assertFalse(focus)
                self.assertEqual(len(launches), 1)
                root = os.path.normpath(wanted) if explicit else str(case.storage / 'data/desktop-apps')
                self.assertEqual(json.loads(case.output.read_text()), dict(root=root, argv=tail))
                self.assertFalse(Path(root).exists())
            self.assertEqual(json.loads(state.read_text()), dict(pid=process.pid, root=old))
        finally:
            process.terminate()
            process.wait(timeout=3)
        self.assertFalse(Path('/proc', str(process.pid)).exists())

    def test_explicit_root_launches_past_matched_and_unmatched_process(self):
        for alias in ('tui', 'kilix-tui'):
            for matched in (False, True):
                with self.subTest(alias=alias, matched=matched):
                    self.observe(alias, explicit=True, matched=matched)

    def test_ordinary_existing_focus_and_unmatched_launch_are_preserved(self):
        for alias in ('tui', 'kilix-tui'):
            for matched in (False, True):
                with self.subTest(alias=alias, matched=matched):
                    self.observe(alias, explicit=False, matched=matched)


if __name__ == '__main__':
    unittest.main()
