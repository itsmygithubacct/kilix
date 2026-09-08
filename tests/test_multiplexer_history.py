"""Causal build history controls using the explicitly supplied native package."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

import test_multiplexer_boundaries as fixture


class HistoryTests(fixture.InstalledBuildTests):
    def test_earlier_include_and_nested_negative_lookup_invalidate(self):
        earlier = self.root / 'earlier'
        earlier.mkdir()
        (earlier / 'nested').mkdir()
        (self.headers / 'nested').mkdir()
        (self.headers / 'nested/guard.h').write_text('#define MARKER 3\n')
        (self.source / 'main.c').write_text(self.main.replace('<guard.h>', '<nested/guard.h>'))
        self.commit()
        self.env['CFLAGS'] = '-I' + str(earlier) + ' -I' + str(self.headers)
        self.run_build(); self.run_build()
        self.assertEqual(self.count(), 2)
        (earlier / 'nested/guard.h').write_text('#define MARKER 19\n')
        self.run_build()
        self.assertEqual((self.value(), self.count()), (19, 4))
        self.run_build(); self.assertEqual(self.count(), 4)

    def test_make_selected_initially_absent_include_invalidate(self):
        earlier = self.root / 'absent'
        make = (self.source / 'Makefile').read_text()
        (self.source / 'Makefile').write_text(make.replace('$(CC)', '$(CC) -I' + str(earlier)))
        self.commit()
        self.run_build(); self.run_build(); self.assertEqual(self.count(), 2)
        self.assertIsNone(self.record()['search_directories'][str(earlier)])
        earlier.mkdir(); (earlier / 'guard.h').write_text('#define MARKER 19\n')
        self.run_build(); self.assertEqual((self.value(), self.count()), (19, 4))
        self.run_build(); self.assertEqual(self.count(), 4)

    def test_has_include_negative_is_not_a_consumed_header(self):
        source = self.main.replace('#include <guard.h>',
            '#include <guard.h>\n#if __has_include(<optional.h>)\n#undef MARKER\n#define MARKER 19\n#endif')
        (self.source / 'main.c').write_text(source); self.commit()
        self.run_build(); self.run_build(); self.assertEqual(self.count(), 2)
        optional = self.headers / 'optional.h'
        self.assertNotIn(str(optional), self.record()['dependencies'])
        optional.touch()
        self.run_build(); self.assertEqual((self.value(), self.count()), (19, 4))

    def test_snapshot_directory_restore_after_actual_compile_refuses_and_recovers(self):
        sub = self.source / 'sub'; sub.mkdir()
        (sub / 'main.c').write_text(self.main); (self.source / 'main.c').unlink()
        make = (self.source / 'Makefile').read_text().replace('main.c', 'sub/main.c')
        (self.source / 'Makefile').write_text(make); self.commit()
        self.run_build(); before = self.published()
        enter, release, done, finish = (self.control / n for n in ('entered', 'release', 'done', 'finish'))
        first = '\techo enter >> ' + str(enter) + '; while test ! -f ' + str(release) + '; do sleep .01; done\n'
        last = '\techo done >> ' + str(done) + '; while test ! -f ' + str(finish) + '; do sleep .01; done\n'
        gated = make.replace('\t$(CC)', first + '\t$(CC)').replace('\tprintf', last + '\tprintf')
        (self.source / 'Makefile').write_text(gated); self.commit()
        child = self.start()
        self.wait(lambda: enter.exists() and len(enter.read_text().splitlines()) == 2)
        active = [g for g in self.base.glob('generation-*') if (g / 'source/Makefile').read_text() == gated]
        self.assertEqual(len(active), 1)
        snapshot = active[0] / 'source/sub'; saved = self.root / 'saved-sub'
        original = (snapshot / 'main.c').stat()
        snapshot.parent.chmod(0o700); snapshot.chmod(0o700); snapshot.rename(saved)
        snapshot.mkdir(); (snapshot / 'main.c').write_text(self.main.replace('return MARKER', 'return 19'))
        release.touch()
        self.wait(lambda: done.exists() and len(done.read_text().splitlines()) == 2)
        snapshot.rename(self.root / 'alternate-sub'); saved.rename(snapshot)
        snapshot.chmod(0o500); snapshot.parent.chmod(0o500)
        restored = (snapshot / 'main.c').stat()
        self.assertEqual((original.st_ino, original.st_ctime_ns), (restored.st_ino, restored.st_ctime_ns))
        self.assertEqual((snapshot / 'main.c').read_text(), self.main)
        finish.touch(); _out, err = child.communicate(timeout=8)
        self.assertNotEqual(child.returncode, 0); self.assertIn('snapshot directory changed', err)
        self.assertEqual(self.published(), before); self.assertEqual(self.value(), 3)
        self.assertEqual((sub / 'main.c').read_text(), self.main)
        self.run_build(); self.run_build(); self.assertEqual(self.value(), 3)

    def test_replaced_lock_has_only_one_successful_owner(self):
        self.run_build(); before = self.published(); self.gate()
        first = self.start(); entered = self.control / 'entered'
        self.wait(lambda: entered.exists() and len(entered.read_text().splitlines()) == 2)
        original = (self.base / '.build.lock').stat().st_ino
        (self.base / '.build.lock').rename(self.base / 'retained-lock')
        second = self.start()
        self.wait(lambda: first.poll() is not None)
        _out, err = first.communicate(timeout=3)
        self.assertNotEqual(first.returncode, 0); self.assertIn('build lock name', err)
        self.assertEqual(self.published(), before)
        self.wait(lambda: (self.base / '.build.lock').exists())
        self.assertNotEqual((self.base / '.build.lock').stat().st_ino, original)
        self.wait(lambda: len(entered.read_text().splitlines()) == 4)
        # These are cumulative entries. The first owner was already reaped
        # before the second could acquire the continuing directory flock.
        (self.control / 'release').touch()
        _out, err = second.communicate(timeout=8); self.assertEqual(second.returncode, 0, err)
        self.run_build(); self.assertEqual(self.value(), 3)

    def injected(self, patch):
        wrapper = self.control / 'injected.py'
        wrapper.write_text('import os,sys\nfrom pathlib import Path\nsys.path.insert(0,' +
            repr(str(fixture.ROOT / 'config')) + ')\nimport multiplexer_compile as m\n' + patch +
            '\nfrom multiplexer_build import main\nsys.exit(main())\n')
        return ['/usr/bin/python3', '-B', str(wrapper), '--source', str(self.source),
                '--commit', self.head, '--build-dir', str(self.base)]

    def test_lock_replacement_at_entry_refuses_before_allocation(self):
        self.run_build(); before = self.published()
        patch = '''original_open = os.open
def changed_open(path,*a,**kw):
 fd=original_open(path,*a,**kw)
 if path == '.build.lock':
  os.rename('.build.lock','saved-entry-lock',src_dir_fd=kw['dir_fd'],dst_dir_fd=kw['dir_fd'])
 return fd
os.open=changed_open
'''
        result = subprocess.run(self.injected(patch), env=self.env, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0); self.assertIn('build lock name', result.stderr)
        self.assertEqual(self.published(), before)
        self.run_build(); self.assertEqual(self.published(), before)

    def test_snapshot_change_during_final_validation_refuses(self):
        self.run_build(); before = self.published(); self.env['CFLAGS'] += ' -O1'
        patch = '''original_artifacts=m.artifact_files
def changed_artifacts(root,io):
 result=original_artifacts(root,io)
 directory=root.parent/'source'
 directory.chmod(0o700);directory.chmod(0o500)
 return result
m.artifact_files=changed_artifacts
'''
        result = subprocess.run(self.injected(patch), env=self.env, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0); self.assertIn('snapshot directory changed', result.stderr)
        self.assertEqual(self.published(), before)
        self.run_build(); self.assertEqual(self.value(), 3)

    def test_lock_loss_after_each_publication_replace_restores_exact_previous(self):
        self.run_build()
        before = self.published()
        modes = [(self.base / n).stat().st_mode for n in ('kmx-serve', 'kmx-attach', 'build-identity')]
        for target in ('kmx-serve', 'kmx-attach', 'build-identity'):
            with self.subTest(target=target):
                self.env['CFLAGS'] = '-I' + str(self.headers) + ' -O1'
                patch = '''original_replace=os.replace
fired=False
def changed_replace(src,dst,*a,**kw):
 global fired
 result=original_replace(src,dst,*a,**kw)
 if str(dst)==TARGET and not fired:
  fired=True
  fd=kw['dst_dir_fd']
  os.rename('.build.lock','saved-publication-lock',src_dir_fd=fd,dst_dir_fd=fd)
 return result
os.replace=changed_replace
'''.replace('TARGET', repr(target))
                result = subprocess.run(self.injected(patch), env=self.env, capture_output=True, text=True, timeout=10)
                self.assertNotEqual(result.returncode, 0); self.assertIn('build lock name', result.stderr)
                self.assertEqual(self.published(), before)
                self.assertEqual([(self.base / n).stat().st_mode for n in ('kmx-serve', 'kmx-attach', 'build-identity')], modes)
                self.env['CFLAGS'] = '-I' + str(self.headers)
                self.run_build(); self.assertEqual(self.published(), before)
        self.env['CFLAGS'] += ' -O1'; self.run_build(); self.assertEqual(self.value(), 3)

    def test_directory_history_restoration_and_close_are_bounded(self):
        spec = importlib.util.spec_from_file_location('history_guards', fixture.ROOT / 'config/multiplexer_build_guards.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        root = self.root / 'snapshot'; root.mkdir(); (root / 'sub').mkdir()
        count = len(os.listdir('/proc/self/fd'))
        history = module.DirectoryHistory(root, lambda: None)
        try:
            history.check()
            (root / 'sub').rename(self.root / 'saved')
            (self.root / 'saved').rename(root / 'sub')
            with self.assertRaises(module.BuildBoundaryChanged): history.check()
            with self.assertRaises(module.BuildBoundaryChanged): history.check()
        finally: history.close()
        self.assertEqual(len(os.listdir('/proc/self/fd')), count)

    def test_benign_lock_descriptor_close_is_not_a_mutation(self):
        spec = importlib.util.spec_from_file_location('lock_guards', fixture.ROOT / 'config/multiplexer_build_guards.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        root = self.root / 'private-lock'; root.mkdir(mode=0o700)
        before = len(os.listdir('/proc/self/fd'))
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        lock = os.open(root / '.build.lock', os.O_CREAT | os.O_RDWR, 0o600)
        guard = module.NamedLock(root, directory, lock)
        try:
            other = os.open(root / '.build.lock', os.O_RDWR); os.close(other)
            guard.check()
            os.write(lock, b'x')
            with self.assertRaises(module.BuildBoundaryChanged): guard.check()
        finally:
            guard.close(); os.close(lock); os.close(directory)
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)

    def test_restored_lock_and_directory_names_still_refuse(self):
        self.run_build(); before = self.published(); self.gate()
        for target in ('lock', 'directory', 'generation'):
            with self.subTest(target=target):
                child = self.start(); entered = self.control / 'entered'
                self.wait(lambda: entered.exists() and len(entered.read_text().splitlines()) == 2)
                path = self.base / '.build.lock' if target == 'lock' else self.base
                if target == 'generation':
                    make = (self.source / 'Makefile').read_text()
                    path = next(g for g in self.base.glob('generation-*') if (g / 'source/Makefile').read_text() == make)
                moved = path.with_name(path.name + '-saved')
                path.rename(moved); moved.rename(path)
                if target == 'generation': (self.control / 'release').touch()
                _out, err = child.communicate(timeout=8)
                self.assertNotEqual(child.returncode, 0); self.assertIn('changed', err)
                self.assertEqual(self.published(), before)
                entered.unlink()
        (self.control / 'release').touch()
        self.run_build(); self.assertEqual(self.value(), 3)

    def test_io_interruption_pair_mismatch_is_detected_on_next_use(self):
        self.run_build(); before = self.published()
        self.header.write_text('#define MARKER 7\n')
        patch = '''original_replace=os.replace
def changed_replace(src,dst,*a,**kw):
 if str(dst)=='kmx-attach':raise OSError('test publication interruption')
 return original_replace(src,dst,*a,**kw)
os.replace=changed_replace
'''
        result = subprocess.run(self.injected(patch), env=self.env, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0); self.assertIn('test publication interruption', result.stderr)
        values = [subprocess.run([str(self.base / n)], env=self.env).returncode for n in ('kmx-serve', 'kmx-attach')]
        self.assertEqual(values, [7, 3]); self.assertEqual(self.published()[2], before[2])
        count = self.count(); self.run_build(); self.assertEqual(self.count(), count + 2)
        self.assertEqual(self.value(), 7)


def load_tests(_loader, _suite, _pattern):
    return unittest.TestSuite(HistoryTests(name) for name in HistoryTests.__dict__ if name.startswith('test_'))


if __name__ == '__main__':
    unittest.main()
