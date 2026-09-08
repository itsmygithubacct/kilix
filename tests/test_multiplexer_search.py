"""Real negative-lookup lifetime and continuing publication controls."""
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import test_multiplexer_boundaries as fixture


class SearchTests(fixture.InstalledBuildTests):
    def prepare_interval(self, optional=False, absent=False):
        if optional:
            source = self.main.replace('#include <guard.h>', '#include <guard.h>\n#if __has_include(<optional.h>)\n#undef MARKER\n#define MARKER 19\n#endif')
            (self.source / 'main.c').write_text(source)
            self.commit()
        if absent:
            self.env['CFLAGS'] = '-I' + str(self.root / 'absent') + ' ' + self.env['CFLAGS']
        self.run_build()
        before = self.published()
        enter, release, done, finish = (self.control / n for n in ('entered', 'release', 'compiled', 'finish'))
        make = (self.source / 'Makefile').read_text()
        first = '\techo enter >> ' + str(enter) + '; while test ! -f ' + str(release) + '; do sleep .01; done\n'
        last = '\techo done >> ' + str(done) + '; while test ! -f ' + str(finish) + '; do sleep .01; done\n'
        make = make.replace('\t$(CC)', first + '\t$(CC)').replace('\tprintf', last + '\tprintf')
        (self.source / 'Makefile').write_text(make)
        self.commit()
        child = self.start()
        self.wait(lambda: enter.exists() and len(enter.read_text().splitlines()) == 2)
        stage = next(g for g in self.base.glob('generation-*') if (g / 'source/Makefile').read_text() == make)
        return child, stage, before, release, done, finish

    def test_transient_has_include_refuses_fresh_publication_and_recovers(self):
        child, stage, before, release, done, finish = self.prepare_interval(optional=True)
        optional = self.headers / 'optional.h'
        optional.write_bytes(b'/* existence only */\n')
        release.touch()
        self.wait(lambda: done.exists() and len(done.read_text().splitlines()) == 2)
        values = [subprocess.run([str(stage / 'out' / n)], env=self.env).returncode for n in ('kmx-serve', 'kmx-attach')]
        self.assertEqual(values, [19, 19])
        optional.unlink()
        finish.touch()
        _out, err = child.communicate(timeout=8)
        self.assertNotEqual(child.returncode, 0)
        self.assertIn('compiler include search changed', err)
        self.assertEqual(self.published(), before)
        self.assertFalse(stage.exists())
        self.run_build()
        self.assertEqual(self.value(), 3)
        count = self.count()
        self.run_build()
        self.assertEqual(self.count(), count)

    def test_initially_absent_root_transient_history_refuses(self):
        child, stage, before, release, done, finish = self.prepare_interval(absent=True)
        release.touch()
        self.wait(lambda: done.exists() and len(done.read_text().splitlines()) == 2)
        missing = self.root / 'absent'
        missing.mkdir()
        missing.rmdir()
        finish.touch()
        _out, err = child.communicate(timeout=8)
        self.assertNotEqual(child.returncode, 0)
        self.assertIn('compiler include search changed', err)
        self.assertEqual(self.published(), before)
        self.assertFalse(stage.exists())
        self.run_build()
        self.assertEqual(self.value(), 3)

    def module(self):
        sys.path.insert(0, str(fixture.ROOT / 'config'))
        self.addCleanup(lambda: sys.path.remove(str(fixture.ROOT / 'config')))
        spec = importlib.util.spec_from_file_location('search_control', fixture.ROOT / 'config/multiplexer_search.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_negative_history_unrelated_sibling_and_benign_close(self):
        module = self.module()
        before = len(os.listdir('/proc/self/fd'))
        for shape in ('existing', 'absent', 'symlink'):
            with self.subTest(shape=shape):
                parent = self.root / shape
                parent.mkdir()
                target = parent / 'headers'
                actual = target
                if shape == 'existing':
                    target.mkdir()
                elif shape == 'symlink':
                    actual = self.root / 'elsewhere'
                    actual.mkdir()
                    target.symlink_to(actual, target_is_directory=True)
                if shape != 'absent':
                    (actual / 'stable.h').write_bytes(b'stable')
                history = module.SearchHistory()
                try:
                    history.add([str(target)], lambda: None)
                    (parent / 'unrelated').touch()
                    history.check()
                    if shape == 'absent':
                        target.mkdir()
                        target.rmdir()
                    else:
                        fd = os.open(actual / 'stable.h', os.O_RDWR)
                        os.close(fd)
                        history.check()
                        (actual / 'optional.h').touch()
                        (actual / 'optional.h').unlink()
                    for _ in range(2):
                        with self.assertRaises(module.BuildBoundaryChanged):
                            history.check()
                finally:
                    history.close()
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)

    def test_actual_admission_record_framing_and_descriptor_cleanup(self):
        module = self.module()
        before = len(os.listdir('/proc/self/fd'))
        for mode in ('valid', 'empty', 'extra', 'empty-extra', 'rights', 'oversize'):
            with self.subTest(mode=mode):
                root = self.root / ('protocol-' + mode)
                root.mkdir()
                diagnostics = root / 'logs'
                diagnostics.mkdir()
                (diagnostics / 'gcc-control.log').touch(mode=0o600)
                directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                endpoint = '/proc/self/fd/' + str(directory) + '/s'
                admission = module.SearchAdmission(endpoint, self.source, diagnostics, lambda: None)
                child = None
                try:
                    packet = b'gcc-control.log\0#include <...> search starts here:\n ' + os.fsencode(self.headers) + b'\nEnd of search list.\n'
                    script = ('import array,json,os,socket,sys\n'
                        's=socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET);s.connect(sys.argv[1])\n'
                        'mode=sys.argv[2];p=bytes.fromhex(sys.argv[3])\n'
                        'if mode=="empty":s.send(b"")\n'
                        'elif mode=="rights":s.sendmsg([p],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array("i",[int(sys.argv[4])]))])\n'
                        'elif mode=="oversize":s.send(p+b"x"*65536)\n'
                        'else:s.send(p)\n'
                        'if mode=="extra":s.send(b"unexpected")\n'
                        'if mode=="empty-extra":s.send(b"");s.send(b"unexpected")\n'
                        's.shutdown(socket.SHUT_WR)\n'
                        'try: print(json.dumps(s.recv(32).decode()),flush=True)\n'
                        'except ConnectionResetError:pass\n')
                    child = subprocess.Popen(['/usr/bin/python3', '-B', '-c', script, endpoint, mode, packet.hex(), str(directory)],
                                             pass_fds=(directory,), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    deadline = time.monotonic() + 3
                    error = None
                    while child.poll() is None:
                        self.assertLess(time.monotonic(), deadline)
                        try:
                            admission.check()
                        except module.BuildBoundaryChanged as exc:
                            error = exc
                            break
                        time.sleep(.005)
                    if mode == 'valid':
                        self.assertIsNone(error)
                    else:
                        self.assertIsInstance(error, module.BuildBoundaryChanged)
                finally:
                    admission.close()
                    os.close(directory)
                    if child is not None:
                        try:
                            output, errors = child.communicate(timeout=2)
                        except subprocess.TimeoutExpired:
                            child.kill();child.communicate();raise
                        self.assertEqual(child.returncode, 0, errors)
                        if mode == 'valid':
                            self.assertEqual(json.loads(output), 'READY')
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)

    def test_named_diagnostic_that_cannot_be_examined_refuses_typed_before_ready(self):
        """The requester names the log; an absent or unreadable one must refuse.

        Every other rejection on this untrusted request path raises the module's
        typed BuildBoundaryChanged. An escaping OSError would carry the private
        generation path in its errno string and would not be classified as a
        boundary change by a caller that discriminates on the typed refusal.
        """
        module = self.module()
        before = len(os.listdir('/proc/self/fd'))
        arms = ('valid', 'absent', 'disappearing', 'nonempty', 'unreadable')
        for mode in arms:
            with self.subTest(mode=mode):
                root = self.root / ('diagnostic-' + mode)
                root.mkdir()
                diagnostics = root / 'logs'
                diagnostics.mkdir(mode=0o700)
                name = 'gcc-' + mode.replace('-', '_') + '.log'
                if mode != 'absent':
                    log = diagnostics / name
                    log.touch(mode=0o600)
                    if mode == 'nonempty':
                        log.write_bytes(b'diagnostic bytes')
                directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                endpoint = '/proc/self/fd/' + str(directory) + '/s'
                admission = module.SearchAdmission(endpoint, self.source, diagnostics, lambda: None)
                marker = root / 'sent'
                child = None
                try:
                    packet = os.fsencode(name) + b'\0#include <...> search starts here:\n ' + os.fsencode(self.headers) + b'\nEnd of search list.\n'
                    script = ('import json,socket,sys\n'
                        's=socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET);s.connect(sys.argv[1])\n'
                        's.send(bytes.fromhex(sys.argv[2]));s.shutdown(socket.SHUT_WR)\n'
                        'open(sys.argv[3],"w").close()\n'
                        'try: print(json.dumps(s.recv(32).decode()),flush=True)\n'
                        'except ConnectionResetError:pass\n')
                    child = subprocess.Popen(['/usr/bin/python3', '-B', '-c', script, endpoint, packet.hex(), str(marker)],
                                             pass_fds=(directory,), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    # The record is already in flight before the named log stops
                    # being examinable, so this is the actual request path.
                    self.wait(marker.exists)
                    if mode == 'disappearing':
                        (diagnostics / name).unlink()
                    elif mode == 'unreadable':
                        diagnostics.chmod(0o600)
                        self.addCleanup(diagnostics.chmod, 0o700)
                    deadline = time.monotonic() + 3
                    error = None
                    while child.poll() is None:
                        self.assertLess(time.monotonic(), deadline)
                        try:
                            admission.check()
                        except module.BuildBoundaryChanged as exc:
                            error = exc
                            break
                        time.sleep(.005)
                    if mode == 'valid':
                        self.assertIsNone(error)
                        self.assertEqual(sorted(admission.logs), [name])
                        self.assertTrue(admission.history.paths)
                    else:
                        self.assertIsInstance(error, module.BuildBoundaryChanged)
                        # Typed like every other admission refusal, and still a
                        # ValueError, so the operator path keeps classifying it.
                        self.assertNotIsInstance(error, OSError)
                        self.assertIsInstance(error, ValueError)
                        # Refused before READY: no invocation is recorded and no
                        # search watch is retained for an unadmitted request.
                        self.assertEqual(admission.logs, {})
                        self.assertEqual(admission.history.paths, set())
                        self.assertEqual(admission.history.roots, set())
                        if mode == 'nonempty':
                            # The original identity refusal is unchanged.
                            self.assertEqual(str(error), 'compiler diagnostic identity differs at admission')
                        else:
                            self.assertIn(name, str(error))
                            self.assertIn('unavailable at admission', str(error))
                            # The private generation path stays out of the message.
                            self.assertNotIn(str(diagnostics), str(error))
                            self.assertNotIn(str(self.root), str(error))
                finally:
                    admission.close()
                    os.close(directory)
                    if child is not None:
                        try:
                            output, errors = child.communicate(timeout=2)
                        except subprocess.TimeoutExpired:
                            child.kill();child.communicate();raise
                        self.assertEqual(child.returncode, 0, errors)
                        if mode == 'valid':
                            self.assertEqual(json.loads(output), 'READY')
                        else:
                            # A refused request is never acknowledged: the peer
                            # reads an ended channel or is reset, never READY.
                            self.assertNotIn('READY', output)
                self.assertFalse((root / 's').exists())
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)

    def test_history_missing_overflow_and_same_tick_restoration_refuse(self):
        module = self.module()
        before = len(os.listdir('/proc/self/fd'))
        for block in (b'', struct.pack('iIII', -1, 0x4000, 0, 0), b'partial'):
            history = module.SearchHistory()
            try:
                history.add([str(self.headers)], lambda: None)
                read = os.read
                with patch.object(os, 'read', side_effect=lambda fd, n, value=block: value if fd == history.fd else read(fd, n)):
                    with self.assertRaises(module.BuildBoundaryChanged):
                        history.check()
                with self.assertRaises(module.BuildBoundaryChanged):
                    history.check()
            finally:
                history.close()
        history = module.SearchHistory()
        try:
            history.add([str(self.headers)], lambda: None)
            self.headers.rename(self.root / 'held-headers')
            (self.root / 'held-headers').rename(self.headers)
            # History check uses retained kernel events, not sampled ctime.
            with patch.object(module, 'search_identity', return_value=history.state):
                with self.assertRaises(module.BuildBoundaryChanged):
                    history.check()
        finally:
            history.close()
        history = module.SearchHistory()
        try:
            history.add([str(self.headers)], lambda: None)
            wd = next(iter(history.watches))
            self.assertEqual(history.libc.inotify_rm_watch(history.fd, wd), 0)
            with self.assertRaises(module.BuildBoundaryChanged):
                history.check()
        finally:
            history.close()
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)

    def test_cancel_deadline_after_every_publication_replace_restore(self):
        self.run_build()
        for action in ('cancel', 'deadline'):
            for target in ('kmx-serve', 'kmx-attach', 'build-identity'):
                with self.subTest(action=action, target=target):
                    before = self.published()
                    modes = [(self.base / n).stat().st_mode for n in ('kmx-serve', 'kmx-attach', 'build-identity')]
                    self.env['CFLAGS'] = '-I' + str(self.headers) + ' -O1'
                    helper = self.control / 'publication.py'
                    helper.write_text('import os,sys,time\nsys.path.insert(0,' + repr(str(fixture.ROOT / 'config')) + ')\n'
                        'import multiplexer_compile as m\noriginal=m.publish\n'
                        'def publish(generation,base,directory,record,io):\n'
                        ' replace=os.replace;fired=False\n'
                        ' def changed(src,dst,*a,**kw):\n'
                        '  nonlocal fired\n'
                        '  result=replace(src,dst,*a,**kw)\n'
                        '  if str(dst)==' + repr(target) + ' and not fired:\n'
                        '   fired=True\n'
                        + ('   io.stopped=True\n' if action == 'cancel' else '   io.deadline=time.monotonic()-1\n') +
                        '  return result\n'
                        ' os.replace=changed\n'
                        ' try:return original(generation,base,directory,record,io)\n'
                        ' finally:os.replace=replace\n'
                        'm.publish=publish\nfrom multiplexer_build import main\nsys.exit(main())\n')
                    result = subprocess.run(['/usr/bin/python3', '-B', str(helper), '--source', str(self.source), '--commit', self.head,
                                             '--build-dir', str(self.base)], env=self.env, capture_output=True, text=True, timeout=12)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('interrupted' if action == 'cancel' else 'deadline', result.stderr)
                    self.assertEqual(self.published(), before)
                    self.assertEqual([(self.base / n).stat().st_mode for n in ('kmx-serve', 'kmx-attach', 'build-identity')], modes)
                    # This control proves a fresh recovery after each failed
                    # replacement. Give it a distinct supported unused define;
                    # ordinary reuse has separate unchanged-source controls.
                    self.env['CFLAGS'] = '-I' + str(self.headers) + ' -DRECOVERY_' + action.upper() + '_' + target.replace('-', '_').upper() + '=1'
                    self.run_build()
                    self.assertEqual(self.value(), 3)


def load_tests(_loader, _suite, _pattern):
    return unittest.TestSuite(SearchTests(name) for name in SearchTests.__dict__ if name.startswith('test_'))


if __name__ == '__main__':
    unittest.main()
