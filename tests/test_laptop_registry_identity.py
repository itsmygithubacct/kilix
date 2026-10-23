"""Shared registry contract with synthetic procfs and mocked process operations.

Optional KILIX_TEST_CAP_ROOT and KILIX_TEST_LAND_ROOT enable cross-provider
checks. C helpers compile into temporary storage and cannot signal a process.
"""
import builtins
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('registry_host', ROOT / 'config/laptop.py')
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
BOOT = '12345678-1234-1234-1234-123456789abc'

C_FIXTURE = r'''
#include <assert.h>
#include <stdarg.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/syscall.h>
static int fake_open(const char *, int, ...);
static int fake_kill(pid_t, int);
static long fake_syscall(long, ...);
#define open fake_open
#define kill fake_kill
#define syscall fake_syscall
#include "PROVIDER_SOURCE"
#undef open
#undef kill
#undef syscall
PROVIDER_STUB
static const char *fixture;
static int signals;
static int fake_open(const char *path, int flags, ...) {
    mode_t mode = 0;
    if (flags & O_CREAT) { va_list ap; va_start(ap, flags); mode = va_arg(ap, int); va_end(ap); }
    char mapped[4096];
    if (!strcmp(path, "/proc/4242/stat")) {
        snprintf(mapped, sizeof mapped, "%s/stat", fixture); path = mapped;
    } else if (!strcmp(path, "/proc/sys/kernel/random/boot_id")) {
        snprintf(mapped, sizeof mapped, "%s/boot", fixture); path = mapped;
    } else if (!strncmp(path, "/proc/", 6)) { errno = ENOENT; return -1; }
    return open(path, flags, mode);
}
static int fake_kill(pid_t pid, int sig) {
    assert(pid == 4242 && sig == 0); return 0;
}
static long fake_syscall(long call, ...) {
    va_list ap; va_start(ap, call);
    long result;
    if (call == SYS_pidfd_open) {
        assert(va_arg(ap, int) == 4242);
        result = open("/dev/null", O_RDONLY);
    } else {
        assert(call == SYS_pidfd_send_signal);
        assert(va_arg(ap, int) >= 0); assert(va_arg(ap, int) == SIGTERM);
        ++signals; result = 0;
    }
    va_end(ap); return result;
}
int main(int argc, char **argv) {
    assert(argc == 3); fixture = argv[1];
    assert(setenv("KILIX_LAPTOP_PROFILES", fixture, 1) == 0);
    if (!strcmp(argv[2], "write")) printf("%d\n", RECORD("test", 4242));
    else if (!strcmp(argv[2], "read")) printf("%d\n", STATUS("test", NULL));
    else { int result = CLOSE; printf("%d %d\n", result, signals); }
    return 0;
}
'''


class RegistryIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='astra-registry-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.proc(77)
        self.environment = patch.dict(os.environ, KILIX_LAPTOP_PROFILES=str(self.root))
        self.environment.start(); self.addCleanup(self.environment.stop)
        real_open = builtins.open
        def fake_open(path, *args, **kwargs):
            path = str(path)
            if path == '/proc/4242/stat': path = self.root / 'stat'
            elif path == '/proc/sys/kernel/random/boot_id': path = self.root / 'boot'
            elif path.startswith('/proc/'): raise AssertionError('unexpected process access')
            return real_open(path, *args, **kwargs)
        self.files = patch('builtins.open', side_effect=fake_open)
        self.files.start(); self.addCleanup(self.files.stop)
        self.alive = patch.object(host, '_alive', return_value=True)
        self.alive.start(); self.addCleanup(self.alive.stop)

    def proc(self, ticks, boot=BOOT):
        (self.root / 'stat').write_text('4242 (fixture) S ' + '0 ' * 18 + str(ticks) + ' 0\n')
        (self.root / 'boot').write_text(boot + '\n')

    def test_host_identity_and_stable_close(self):
        host.record_session('test', 4242)
        self.assertEqual(host.session_pid('test'), 4242)
        expected = f'v1 4242 77 {BOOT}\n'
        self.assertEqual((self.root / 'run/test.pid.identity').read_text(), expected)
        for changed in ('birth', 'boot', 'legacy'):
            host.record_session('test', 4242)
            with patch.object(host.os, 'pidfd_open', return_value=91), \
                 patch.object(host.signal, 'pidfd_send_signal') as send, \
                 patch.object(host.os, 'close'):
                if changed == 'birth': self.proc(78)
                elif changed == 'boot': self.proc(77, 'a' + BOOT[1:])
                else: (self.root / 'run/test.pid.identity').unlink()
                with self.assertRaises(host.ProfileError): host.cmd_close('test')
                send.assert_not_called()
                self.assertTrue((self.root / 'run/test.pid').exists())
            self.proc(77)
        host.record_session('test', 4242)
        # Identity changes after acquiring the handle: the second check must refuse.
        def acquire(pid):
            self.proc(78)
            return 91
        with patch.object(host.os, 'pidfd_open', side_effect=acquire), \
             patch.object(host.signal, 'pidfd_send_signal') as send, \
             patch.object(host.os, 'close') as close:
            with self.assertRaises(host.ProfileError): host.cmd_close('test')
            send.assert_not_called(); close.assert_called_once_with(91)
        self.proc(77)
        with patch.object(host.os, 'pidfd_open', return_value=91), \
             patch.object(host.signal, 'pidfd_send_signal') as send, \
             patch.object(host.os, 'close'), patch('select.poll') as poll, \
             patch.object(host.os, 'waitpid', return_value=(4242, 0)):
            poll.return_value.poll.return_value = [(91, 1)]
            self.assertEqual(host.cmd_close('test'), 0)
            send.assert_called_once_with(91, host.signal.SIGTERM)

    def test_cross_provider_records(self):
        paths = {name: os.environ.get('KILIX_TEST_' + name.upper() + '_ROOT')
                 for name in ('cap', 'land')}
        if not all(paths.values()): self.skipTest('explicit Cap and Land repair sources required')
        binaries = []
        for name, root in paths.items():
            source = C_FIXTURE.replace('PROVIDER_SOURCE', str(Path(root) / ('src/laptop_run.c' if name == 'cap' else 'src/laptop.c')))
            stub = 'bool laptop_directory(char *p, size_t n) { return snprintf(p, n, "%s", getenv("KILIX_LAPTOP_PROFILES")) > 0; }' if name == 'cap' else ''
            source = source.replace('PROVIDER_STUB', stub)
            source = source.replace('RECORD', 'laptop_run_record' if name == 'cap' else 'desk_laptop_run_record')
            source = source.replace('STATUS', 'laptop_run_status' if name == 'cap' else 'desk_laptop_run_status')
            source = source.replace('CLOSE', 'laptop_run_close("test", NULL, 0)' if name == 'cap' else 'desk_laptop_run_terminate("test")')
            path = self.root / (name + '.c'); path.write_text(source)
            binary = self.root / name
            subprocess.run(['cc', '-std=c11', '-D_DEFAULT_SOURCE', '-D_POSIX_C_SOURCE=200809L',
                            '-Wall', '-Wextra', '-Werror', '-ffunction-sections', '-fdata-sections',
                            str(path), '-Wl,--gc-sections', '-o', str(binary)], check=True)
            binaries.append(binary)
        def run(binary, action):
            return subprocess.check_output([str(binary), str(self.root), action], text=True).strip()
        for writer in [None, *binaries]:
            self.proc(77)
            if writer: self.assertEqual(run(writer, 'write'), '1')
            else: host.record_session('test', 4242)
            self.assertEqual(host.session_pid('test'), 4242)
            for reader in binaries:
                self.assertEqual(run(reader, 'read'), '1')
                self.assertEqual(run(reader, 'close'), '1 1')
            before = (self.root / 'run/test.pid.identity').read_bytes()
            for change in ('birth', 'boot', 'legacy'):
                self.proc(78 if change == 'birth' else 77,
                          'a' + BOOT[1:] if change == 'boot' else BOOT)
                if change == 'legacy': (self.root / 'run/test.pid.identity').unlink()
                with self.assertRaises(host.ProfileError): host.session_pid('test')
                for reader in binaries:
                    self.assertEqual(run(reader, 'read'), '-1')
                    self.assertEqual(run(reader, 'close'), '0 0')
                self.assertTrue((self.root / 'run/test.pid').exists())
                if change != 'legacy':
                    self.assertEqual((self.root / 'run/test.pid.identity').read_bytes(), before)
                else: (self.root / 'run/test.pid.identity').write_bytes(before)

    def test_failed_registration_cancels_only_owned_child(self):
        profile = {'desktop': '', 'id': 'test'}
        with patch.object(host, 'load_profile', return_value=profile), \
             patch.object(host, '_kilix_command', return_value='/fixture/kilix'), \
             patch.object(host, 'session_pid', return_value=None), \
             patch.object(host, 'session_text', return_value='fixture'), \
             patch.object(host, '_spawn_detached', return_value=4242), \
             patch.object(host, 'record_session', side_effect=OSError('fixture failure')), \
             patch.object(host.os, 'waitpid', return_value=(0, 0)) as wait, \
             patch.object(host.os, 'kill') as kill, patch.object(host.time, 'sleep'):
            with self.assertRaises(host.ProfileError): host.cmd_open('test')
            self.assertEqual([c.args for c in kill.call_args_list],
                             [(4242, host.signal.SIGTERM), (4242, host.signal.SIGKILL)])
            self.assertTrue(all(c.args == (4242, os.WNOHANG) for c in wait.call_args_list))
        with patch.object(host.os, 'waitpid', side_effect=ChildProcessError), \
             patch.object(host.os, 'kill') as kill:
            host._cancel_owned_child(4242)
            kill.assert_not_called()


if __name__ == '__main__': unittest.main()
