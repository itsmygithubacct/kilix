"""Plebian-OS's one-time SSH server question at the first desktop login.

Unit cases drive config/ssh_offer.py directly. The terminal cases run the real
desktop_choice.py the way the launcher does (stdin and stderr on a pty, stdout
captured), with a copy of ssh_offer.py whose three system paths point into a
temporary tree: the provisioning record (owned by the test user instead of
root), the helper (a stub) and sudo (env). Nothing here needs root.
"""
import importlib.util
import io
import json
import os
from pathlib import Path
import pty
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('ssh_offer', ROOT / 'config/ssh_offer.py')
offer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(offer)
USER = offer.account()


class Interrupting(io.StringIO):
    def readline(self, *args):
        raise KeyboardInterrupt


class AskTests(unittest.TestCase):
    def answer(self, text):
        output = io.StringIO()
        return offer.ask(io.StringIO(text), output), output.getvalue()

    def test_default_is_no(self):
        for text in ('\n', '', 'n\n', 'No\n', '  \n'):
            self.assertEqual(self.answer(text)[0], 'no', repr(text))

    def test_only_an_explicit_yes_is_yes(self):
        for text in ('y\n', 'Y\n', 'yes\n', ' YES \n'):
            self.assertEqual(self.answer(text)[0], 'yes', repr(text))
        reply, shown = self.answer('maybe\nsure\n\n')
        self.assertEqual(reply, 'no')
        self.assertEqual(shown.count('Please answer y or n.'), 2)

    def test_question_says_what_yes_means_and_how_to_change_it(self):
        _, shown = self.answer('\n')
        self.assertIn('[y/N]', shown)
        self.assertIn('every account on this computer that has a\npassword can log in', shown)
        self.assertIn('log in as that account', shown)
        self.assertNotIn('your account password', shown)
        self.assertIn('sudo plebian-os-ssh-server enable', shown)
        self.assertIn('sudo plebian-os-ssh-server disable', shown)


class RecordTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.record = self.root / 'ssh-server-offer'

    def as_root(self):
        real = os.fstat

        def fstat(fd):
            info = real(fd)
            values = list(info)
            values[4] = 0  # st_uid
            return os.stat_result(values)
        return mock.patch.object(offer.os, 'fstat', fstat)

    def test_record_must_be_roots_and_name_this_account_exactly(self):
        self.record.write_text(f'ssh-server-offer/v1 {USER}\n')
        self.record.chmod(0o644)
        self.assertFalse(offer.offered_to(USER, self.record))  # owned by the test user
        with self.as_root():
            self.assertTrue(offer.offered_to(USER, self.record))
            self.assertFalse(offer.offered_to('someone-else', self.record))
            for text in (f'ssh-server-offer/v1 {USER}', f'ssh-server-offer/v2 {USER}\n',
                         f'ssh-server-offer/v1 {USER}\nextra\n', ''):
                self.record.write_text(text)
                self.assertFalse(offer.offered_to(USER, self.record), repr(text))
            self.record.write_text(f'ssh-server-offer/v1 {USER}\n')
            for mode in (0o666, 0o664, 0o646):  # group- or world-writable
                self.record.chmod(mode)
                self.assertFalse(offer.offered_to(USER, self.record), oct(mode))
            self.record.chmod(0o644)
            link = self.root / 'link'
            link.symlink_to(self.record)
            self.assertFalse(offer.offered_to(USER, link))
            self.assertFalse(offer.offered_to(USER, self.root / 'missing'))

    def test_any_answer_entry_counts_as_answered(self):
        config = self.root / 'config'
        self.assertFalse(offer.answered(config))
        offer.save_answer(config, 'no')
        self.assertTrue(offer.answered(config))
        self.assertEqual(json.loads((config / 'ssh-server-offer.json').read_text()),
                         {'schema': 'kilix.ssh-server-offer/v1', 'answer': 'no'})
        (config / 'ssh-server-offer.json').write_text('damaged')
        self.assertTrue(offer.answered(config))


class OfferOnceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = Path(temporary.name) / 'config'
        self.calls = []
        self.shown_state = []
        patches = [mock.patch.object(offer, 'offered_to', lambda user: user == USER),
                   mock.patch.object(offer, 'run_helper', self.helper),
                   mock.patch.object(offer, 'show_state', self.state),
                   mock.patch.object(offer, 'SSHD', str(Path(temporary.name) / 'no-sshd'))]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.helper_status = 0

    def state(self, output):
        self.shown_state.append(True)
        print('SSH server: ON (read back)', file=output)
        return True

    def helper(self, output):
        # The answer is saved before anything privileged runs.
        self.calls.append(offer.answered(self.config))
        return self.helper_status

    def once(self, text, interactive=True, source=None):
        output = io.StringIO()
        reply = offer.offer_once(self.config, source or io.StringIO(text), output,
                                 interactive=interactive)
        return reply, output.getvalue()

    def test_no_saves_and_never_runs_the_helper_or_asks_again(self):
        reply, shown = self.once('\n')
        self.assertEqual(reply, 'no')
        self.assertIn('SSH stays off.', shown)
        self.assertEqual(self.calls, [])
        reply, shown = self.once('y\n')
        self.assertIsNone(reply)
        self.assertEqual(shown, '')
        self.assertEqual(self.calls, [])

    def test_yes_saves_first_then_runs_the_helper_once(self):
        reply, shown = self.once('y\n\n')
        self.assertEqual(reply, 'yes')
        self.assertEqual(self.calls, [True])
        self.assertIn('Press Enter to continue', shown)
        self.assertIsNone(self.once('y\n\n')[0])
        self.assertEqual(self.calls, [True])

    def test_a_failed_helper_shows_the_real_state_and_does_not_ask_again(self):
        self.helper_status = 1
        reply, shown = self.once('yes\n\n')
        self.assertEqual(reply, 'yes')
        self.assertEqual(self.shown_state, [True])
        self.assertIn('The SSH helper did not finish (exit status 1)', shown)
        self.assertIn('SSH server: ON (read back)', shown)
        self.assertNotIn('not turned on', shown)
        self.assertIn('sudo plebian-os-ssh-server enable', shown)
        self.assertIn('sudo plebian-os-ssh-server disable', shown)
        self.assertIsNone(self.once('yes\n\n')[0])

    def test_a_successful_helper_speaks_for_itself(self):
        reply, shown = self.once('y\n\n')
        self.assertEqual(self.shown_state, [])
        self.assertNotIn('did not finish', shown)

    def test_an_ssh_server_installed_since_first_boot_means_no_question(self):
        sshd = Path(offer.SSHD)
        sshd.write_text('')
        reply, shown = self.once('y\n')
        self.assertIsNone(reply)
        self.assertEqual(shown, '')
        self.assertFalse(offer.answered(self.config))
        self.assertEqual(self.calls, [])

    def test_ctrl_c_while_saving_never_stops_the_desktop(self):
        with mock.patch.object(offer, 'save_answer', side_effect=KeyboardInterrupt):
            reply, shown = self.once('y\n')
        self.assertIsNone(reply)
        self.assertIn('skipped the SSH server question', shown)
        self.assertEqual(self.calls, [])

    def test_ctrl_c_counts_as_no(self):
        reply, shown = self.once('', source=Interrupting())
        self.assertEqual(reply, 'no')
        self.assertTrue(offer.answered(self.config))
        self.assertEqual(self.calls, [])

    def test_unattended_logins_are_never_asked_and_keep_the_question(self):
        reply, shown = self.once('y\n', interactive=False)
        self.assertIsNone(reply)
        self.assertEqual(shown, '')
        self.assertFalse(offer.answered(self.config))
        self.assertEqual(self.once('\n')[0], 'no')

    def test_no_record_means_no_question(self):
        with mock.patch.object(offer, 'offered_to', lambda user: False):
            self.assertIsNone(self.once('y\n')[0])
        self.assertFalse(offer.answered(self.config))

    def test_internal_failure_never_raises(self):
        with mock.patch.object(offer, 'save_answer', side_effect=OSError('read-only')):
            reply, shown = self.once('y\n')
        self.assertIsNone(reply)
        self.assertIn('skipped the SSH server question: read-only', shown)
        self.assertEqual(self.calls, [])


class RunHelperTests(unittest.TestCase):
    def test_the_helper_runs_through_sudo_with_the_one_word_enable(self):
        self.assertEqual(offer.SUDO, '/usr/bin/sudo')
        self.assertEqual(offer.HELPER, '/usr/local/sbin/plebian-os-ssh-server')
        seen = []

        class Child:
            def wait(self):
                return 0

        def popen(argv, **kwargs):
            seen.append(argv)
            return Child()
        with mock.patch.object(offer.os, 'access', lambda path, mode: True), \
                mock.patch.object(offer.subprocess, 'Popen', popen):
            self.assertEqual(offer.run_helper(io.StringIO()), 0)
        self.assertEqual(seen, [['/usr/bin/sudo', '/usr/local/sbin/plebian-os-ssh-server', 'enable']])


class FirstLoginTerminalTests(unittest.TestCase):
    """desktop_choice.py choose --session-start, as the launcher runs it."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        tree = self.root / 'kilix/config'
        tree.mkdir(parents=True)
        shutil.copy2(ROOT / 'config/desktop_choice.py', tree / 'desktop_choice.py')
        self.record = self.root / 'ssh-server-offer'
        self.helper_log = self.root / 'helper.log'
        helper = self.root / 'plebian-os-ssh-server'
        self.helper_rc = self.root / 'helper.rc'
        helper.write_text('#!/bin/sh\n'
                          f'echo "$*" >> {self.helper_log}\n'
                          'if [ "$1" = status ]; then echo "SSH server: ON -- stub read back"; exit 0; fi\n'
                          'echo "SSH server: ON (stub)"\n'
                          f'exit "$(cat {self.helper_rc} 2>/dev/null || echo 0)"\n')
        helper.chmod(0o755)
        source = (ROOT / 'config/ssh_offer.py').read_text()
        replacements = {
            "OFFER = Path('/var/lib/plebian-os/ssh-server-offer')": f"OFFER = Path({str(self.record)!r})",
            "HELPER = '/usr/local/sbin/plebian-os-ssh-server'": f"HELPER = {str(helper)!r}",
            "SUDO = '/usr/bin/sudo'": "SUDO = '/usr/bin/env'",
            "SSHD = '/usr/sbin/sshd'": f"SSHD = {str(self.root / 'no-sshd')!r}",
            "info.st_uid != 0": "info.st_uid != os.geteuid()",
        }
        for old, new in replacements.items():
            self.assertEqual(source.count(old), 1, old)
            source = source.replace(old, new)
        (tree / 'ssh_offer.py').write_text(source)
        self.script = tree / 'desktop_choice.py'
        self.config = self.root / 'storage/config'

    def run_choice(self, *args):
        return subprocess.run([sys.executable, '-B', str(self.script), '--config',
                               str(self.config), *args], capture_output=True, text=True,
                              timeout=10)

    def session_start(self, replies):
        """stdin/stderr on a pty, stdout captured like `$(...)` in kilix."""
        master, slave = pty.openpty()
        child = subprocess.Popen([sys.executable, '-B', str(self.script), '--config',
                                  str(self.config), 'choose', '--session-start'],
                                 stdin=slave, stdout=subprocess.PIPE, stderr=slave)
        os.close(slave)
        self.addCleanup(lambda: child.poll() is None and child.kill())
        shown, pending = b'', list(replies)
        deadline = time.monotonic() + 15
        try:
            while time.monotonic() < deadline and child.poll() is None:
                if select.select([master], [], [], .1)[0]:
                    try:
                        shown += os.read(master, 65536)
                    except OSError:
                        break
                    while pending and pending[0][0] in shown:
                        prompt_text, reply = pending.pop(0)
                        shown = shown.replace(prompt_text, b'<' + prompt_text + b'>', 1)
                        os.write(master, reply)
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                # A question nobody answers must fail this test now, not hang
                # it until the suite's timeout.
                child.kill()
                child.wait(timeout=3)
                self.fail('first-login step still waiting after 15 s:\n'
                          + shown.decode(errors='replace'))
            stdout = child.stdout.read()
        finally:
            os.close(master)
            child.stdout.close()
        self.assertEqual(child.wait(timeout=3), 0, shown.decode(errors='replace'))
        self.assertEqual(pending, [], shown.decode(errors='replace'))
        return stdout.decode(), shown.decode(errors='replace')

    def offer_to_me(self):
        self.record.write_text(f'ssh-server-offer/v1 {USER}\n')
        self.record.chmod(0o644)

    def test_desktop_then_ssh_question_default_no_then_never_again(self):
        self.offer_to_me()
        self.assertEqual(self.run_choice('request').returncode, 0)
        stdout, shown = self.session_start([(b'Choose a number:', b'2\n'),
                                            (b'[y/N]', b'\n')])
        self.assertEqual(stdout, 'icewm\n')  # only the desktop reaches the launcher
        self.assertLess(shown.index('Choose a number:'), shown.index('Remote login (SSH)'))
        self.assertIn('SSH stays off.', shown)
        self.assertFalse(self.helper_log.exists())
        # Second login: saved desktop, no question.
        stdout, shown = self.session_start([])
        self.assertEqual(stdout, 'icewm\n')
        self.assertNotIn('Remote login', shown)

    def test_yes_runs_the_helper_through_sudo_in_the_terminal(self):
        self.offer_to_me()
        stdout, shown = self.session_start([(b'[y/N]', b'y\n'),
                                            (b'Press Enter to continue', b'\n')])
        self.assertEqual(stdout, '')  # no desktop request: keep the configured launch
        self.assertEqual(self.helper_log.read_text(), 'enable\n')
        self.assertIn('SSH server: ON (stub)', shown)
        self.assertNotIn('SSH server: ON', stdout)
        stdout, shown = self.session_start([])
        self.assertNotIn('Remote login', shown)
        self.assertEqual(self.helper_log.read_text(), 'enable\n')

    def test_a_failed_helper_shows_the_state_read_back_not_a_guess(self):
        self.offer_to_me()
        self.helper_rc.write_text('1\n')
        stdout, shown = self.session_start([(b'[y/N]', b'y\n'),
                                            (b'Press Enter to continue', b'\n')])
        self.assertEqual(stdout, '')
        self.assertEqual(self.helper_log.read_text(), 'enable\nstatus\n')
        self.assertIn('did not finish (exit status 1)', shown)
        self.assertIn('SSH server: ON -- stub read back', shown)
        self.assertNotIn('not turned on', shown)

    def test_deferring_the_desktop_still_asks_the_ssh_question_once(self):
        self.offer_to_me()
        self.assertEqual(self.run_choice('request').returncode, 0)
        _, shown = self.session_start([(b'Choose a number:', b'q\n'), (b'[y/N]', b'n\n')])
        self.assertIn('SSH stays off.', shown)
        _, shown = self.session_start([(b'Choose a number:', b'q\n')])
        self.assertNotIn('Remote login', shown)

    def test_unattended_session_start_is_never_asked(self):
        self.offer_to_me()
        result = subprocess.run([sys.executable, '-B', str(self.script), '--config',
                                 str(self.config), 'choose', '--session-start'],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout + result.stderr, '')
        self.assertFalse((self.config / 'ssh-server-offer.json').exists())

    def test_no_record_no_question(self):
        stdout, shown = self.session_start([])
        self.assertEqual(stdout, '')
        self.assertNotIn('Remote login', shown)

    def test_a_missing_offer_module_does_not_stop_the_desktop(self):
        (self.script.parent / 'ssh_offer.py').unlink()
        self.assertEqual(self.run_choice('set', 'zell').returncode, 0)
        stdout, shown = self.session_start([])
        self.assertEqual(stdout, 'zell\n')
        self.assertIn('skipped the SSH server question', shown)


if __name__ == '__main__':
    unittest.main()
