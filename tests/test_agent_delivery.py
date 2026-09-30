"""Delivery verifies UI effects and never repeats uncertain input attempts."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
import agent_control as control
import agent_delivery as delivery
from test_agent_control import FakeClient, TARGET_BROKER, SOURCE_BROKER


def screen(value=None, history="", *, busy=False, dim_placeholder=True):
    prompt = "\x1b[0;1m›\x1b[22m "
    if value is None:
        value = ("\x1b[2m" if dim_placeholder else "") + "Ask Codex to do anything"
    return (history + "\n\x1b[0m\n" + prompt + value + "\x1b[0m\n\n"
            "  \x1b[38:2:246:226:183mGPT-6.1-Sol high\x1b[39m · ~/project · Task\n"
            "  " + ("esc to interrupt" if busy else "? for shortcuts") + "\n")


class Clock:
    def __init__(self): self.now = 0
    def monotonic(self): return self.now
    def sleep(self, seconds): self.now += seconds


class Terminal(FakeClient):
    def __init__(self):
        super().__init__()
        self.command = ["kitten", "@", "--to", "unix:@private-delivery-test"]
        self.current = screen()
        self.history = ""
        self.wire = None
        self.on_paste = None
        self.on_submit = None
        self.on_read = None
        self.reads = 0
        self.busy = False

    def run(self, args, payload=None):
        self.calls.append((args, payload))
        if args[0] == "get-text":
            self.reads += 1
            if self.on_read:
                self.on_read(self)
            return self.current
        if args[0] == "send-text":
            if payload in (b"\r", b"\t"):
                self.history = (("Queued follow-up inputs\n" if payload == b"\t" else
                                 "Messages to be submitted after next tool call\n" if self.busy else "")
                                + "\x1b[1;2m›\x1b[22m " + self.wire)
                self.current = screen(history=self.history, busy=self.busy)
                if self.on_submit:
                    self.on_submit(self)
            else:
                self.wire = payload.decode()
                self.current = screen(self.wire, history=self.history, busy=self.busy)
                if self.on_paste:
                    self.on_paste(self)
        return ""

    @property
    def inputs(self):
        return [payload for _, payload in self.calls if payload is not None]


class ComposerTests(unittest.TestCase):
    def test_placeholder_style_distinguishes_real_input(self):
        self.assertTrue(delivery.composer(screen())["empty"])
        for value in ("Ask Codex to do anything", "someone else's draft", "[Pasted Content 850 chars]"):
            self.assertFalse(delivery.composer(screen(value))["empty"])

    def test_history_prompt_does_not_count_as_composer(self):
        raw = "\x1b[1;2m›\x1b[22m Ask Codex to do anything\n  GPT-6.1-Sol high · ~/project\n"
        with self.assertRaises(control.ControlError):
            delivery.composer(raw)

    def test_wrapped_unicode_and_color_codes(self):
        raw = screen("hello café 世界\n  next line")
        result = delivery.composer(raw)
        self.assertEqual(delivery.normalized(result["text"]), "hellocafé世界nextline")
        # A truecolor component of 2 must not mark input as faint.
        self.assertFalse(delivery.composer(screen("\x1b[38;2;2;2;2mhello"))["empty"])

    def test_unknown_ui_or_plain_text_cannot_prove_readiness(self):
        for raw in ("$ ", "Password: ", "Trust this folder?", "› Ask Codex to do anything\n",
                    screen().replace("GPT-6.1-Sol", "unknown-model"),
                    screen().replace("\n\n  \x1b[38", "\nmenu selection\n  \x1b[38")):
            with self.subTest(raw=raw):
                with self.assertRaises(control.ControlError):
                    delivery.composer(raw)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = Terminal()
        self.clock = Clock()
        for name in ("monotonic", "sleep"):
            patch = mock.patch.object(delivery.time, name, getattr(self.clock, name))
            patch.start()
            self.addCleanup(patch.stop)

    def args(self, text="Please read the prepared result.", message_id="task-1", mode="steer", *extra):
        return control.parser().parse_args(["deliver", "2", "--expect-broker", TARGET_BROKER,
                                           "--message-id", message_id, "--text", text,
                                           "--timeout", "1", "--mode", mode, *extra])

    def deliver(self, **kwargs):
        return delivery.deliver(self.client, self.args(**kwargs))

    def test_one_paste_separate_enter_verified_receipt(self):
        result = self.deliver(text="Hello café $value `literal` 'quotes'")
        self.assertEqual(result["status"], "submitted")
        self.assertTrue(result["delivery_verified"])
        self.assertFalse(result["acknowledgment_verified"])
        self.assertFalse(result["completion_verified"])
        self.assertEqual(self.client.inputs, [b"[kilix-message:task-1] Hello caf\xc3\xa9 $value `literal` 'quotes'", b"\r"])
        paste = next(i for i, (_, p) in enumerate(self.client.calls) if p == self.client.inputs[0])
        key = next(i for i, (_, p) in enumerate(self.client.calls) if p == b"\r")
        self.assertTrue(any(a[0] == "get-text" for a, _ in self.client.calls[paste+1:key]))
        self.assertLess(len(json.dumps(result)), 450)

    def test_duplicate_returns_receipt_without_any_more_terminal_io(self):
        first = self.deliver()
        calls = len(self.client.calls)
        second = self.deliver()
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["verified_at"], first["verified_at"])
        self.assertEqual(len(self.client.calls), calls)

    def test_message_id_collision_preserves_original_receipt(self):
        self.deliver()
        before = [p.read_bytes() for p in Path(self.temp.name).rglob('*.json')]
        for kwargs in ({"text": "different body"}, {"mode": "defer"}):
            result = self.deliver(**kwargs)
            self.assertEqual(result["status"], "blocked")
            self.assertIn("different payload", result["error"])
        self.assertEqual(before, [p.read_bytes() for p in Path(self.temp.name).rglob('*.json')])
        self.assertEqual(len(self.client.inputs), 2)

    def test_occupied_and_unknown_screens_receive_no_input(self):
        for raw in (screen("other sender's draft"), screen("Ask Codex to do anything"),
                    screen("[Pasted Content 600 chars]"), screen("   "), "$ ", "Trust this directory?", "Password:"):
            self.client.current = raw
            result = self.deliver()
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(self.client.inputs, [])

    def test_stale_self_ambiguous_missing_and_non_codex_target(self):
        base = copy.deepcopy(self.client.panes)
        scenarios = [lambda: self.client.panes[1]['env'].update(KITTY_PTY_BROKER_SESSION='c'*16),
                     lambda: setattr(self.client, 'caller', 2),
                     lambda: self.client.panes.append({**copy.deepcopy(self.client.panes[1]), 'id': 3}),
                     lambda: self.client.panes.pop(),
                     lambda: self.client.panes[1].update(foreground_processes=[{'pid': 22, 'cmdline': ['/bin/bash']}])]
        for scenario in scenarios:
            self.client.panes = copy.deepcopy(base)
            self.client.caller = 1
            scenario()
            self.assertEqual(self.deliver()['status'], 'blocked')
            self.assertEqual(self.client.inputs, [])

    def test_partial_or_collapsed_paste_never_sends_enter_or_retries(self):
        for value in ('partial', '[Pasted Content 800 chars]'):
            with self.subTest(value=value):
                self.client.current = screen()
                self.client.on_paste = lambda c: setattr(c, 'current', screen(value))
                result = self.deliver(message_id=value.split()[0].strip('['))
                self.assertEqual(result['status'], 'uncertain')
                count = len(self.client.inputs)
                again = self.deliver(message_id=value.split()[0].strip('['))
                self.assertEqual(again['status'], 'uncertain')
                self.assertEqual(len(self.client.inputs), count)
                self.assertNotIn(b'\r', self.client.inputs)

    def test_delayed_full_paste_waits_before_enter(self):
        def after_paste(c): c.current = screen('partial')
        def on_read(c):
            if c.reads == 4: c.current = screen(c.wire)
        self.client.on_paste, self.client.on_read = after_paste, on_read
        self.assertEqual(self.deliver()['status'], 'submitted')
        self.assertEqual(len(self.client.inputs), 2)
        self.assertGreater(self.clock.now, 0)

    def test_partial_redraw_after_enter_is_polled_without_resending(self):
        self.client.on_submit = lambda c: setattr(c, 'current', 'partial redraw')
        def read(c):
            if c.reads == 5: c.current = screen(history=c.history)
        self.client.on_read = read
        self.assertEqual(self.deliver()['status'], 'submitted')
        self.assertEqual(len(self.client.inputs), 2)

    def test_restart_between_paste_and_enter_stops(self):
        self.client.on_paste = lambda c: c.panes[1]['env'].update(KITTY_PTY_BROKER_SESSION='c'*16)
        self.assertEqual(self.deliver()['status'], 'uncertain')
        self.assertEqual(len(self.client.inputs), 1)

    def test_foreground_process_change_stops(self):
        self.client.on_paste = lambda c: c.panes[1]['foreground_processes'][0].update(pid=999)
        self.assertEqual(self.deliver()['status'], 'uncertain')
        self.assertEqual(len(self.client.inputs), 1)

    def test_other_input_before_submit_is_preserved(self):
        def read(c):
            if c.reads == 3: c.current = screen(c.wire + ' other input')
        self.client.on_read = read
        self.assertEqual(self.deliver()['status'], 'uncertain')
        self.assertEqual(len(self.client.inputs), 1)

    def test_unaccepted_enter_is_not_repeated(self):
        self.client.on_submit = lambda c: setattr(c, 'current', screen(c.wire))
        self.assertEqual(self.deliver()['status'], 'uncertain')
        self.assertEqual(self.deliver()['status'], 'uncertain')
        self.assertEqual(len(self.client.inputs), 2)

    def test_empty_input_alone_is_not_delivery_proof(self):
        self.client.on_submit = lambda c: setattr(c, 'current', screen())
        self.assertEqual(self.deliver()['status'], 'uncertain')
        self.assertEqual(len(self.client.inputs), 2)

    def test_crash_after_submit_recovers_without_input(self):
        self.client.on_submit = lambda c: (_ for _ in ()).throw(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt): self.deliver()
        self.client.on_submit = None
        result = self.deliver()
        self.assertEqual(result['status'], 'submitted')
        self.assertTrue(result['duplicate'])
        self.assertEqual(len(self.client.inputs), 2)

    def test_crash_before_paste_does_not_retry_even_with_empty_input(self):
        with mock.patch.object(self.client, 'input', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt): self.deliver()
        result = self.deliver()
        self.assertEqual(result['status'], 'uncertain')
        self.assertEqual(self.client.inputs, [])

    def test_recovery_rejects_a_restarted_process_in_the_same_pane(self):
        self.client.on_submit = lambda c: (_ for _ in ()).throw(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt): self.deliver()
        self.client.panes[1]['foreground_processes'][0]['pid'] = 999
        result = self.deliver()
        self.assertEqual(result['status'], 'uncertain')
        self.assertIn('process changed', result['error'])
        self.assertEqual(len(self.client.inputs), 2)

    def test_busy_enter_steers_and_explicit_tab_defers(self):
        for mode, key, status in [('steer', b'\r', 'submitted'), ('defer', b'\t', 'deferred')]:
            self.client.current = screen(busy=True)
            self.client.busy = True
            result = self.deliver(message_id=mode, mode=mode)
            self.assertEqual(result['status'], status)
            self.assertEqual(self.client.inputs[-1], key)

    def test_defer_on_idle_target_is_blocked(self):
        self.assertEqual(self.deliver(mode='defer')['status'], 'blocked')
        self.assertEqual(self.client.inputs, [])

    def test_message_already_visible_without_receipt_refuses(self):
        self.client.current = screen(history='[kilix-message:task-1] earlier message')
        self.assertEqual(self.deliver()['status'], 'blocked')
        self.assertEqual(self.client.inputs, [])

    def test_invalid_inputs_are_rejected_before_terminal_io(self):
        values = ['', ' ', '/model', ' !pwd', '#note', 'two\nlines', 'é'*450, '\x1b[A']
        for text in values:
            self.assertEqual(self.deliver(text=text)['status'], 'blocked')
        for name in ['', '../escape', 'a'*65, '世界']:
            self.assertEqual(self.deliver(message_id=name)['status'], 'blocked')
        for timeout in [0, 61, float('inf'), float('nan')]:
            args = self.args(); args.timeout = timeout
            self.assertEqual(delivery.deliver(self.client, args)['status'], 'blocked')
        self.assertEqual(self.client.calls, [])

    def test_literal_file_and_nonregular_input(self):
        f = Path(self.temp.name)/'message'
        f.write_text("Please read $PATH literally.")
        args = self.args(); args.file = f; args.text = None
        self.assertEqual(delivery.deliver(self.client, args)['status'], 'submitted')
        fifo = Path(self.temp.name)/'fifo'; os.mkfifo(fifo)
        args.file = fifo; args.message_id = 'fifo'
        self.assertEqual(delivery.deliver(self.client, args)['status'], 'blocked')
        self.assertEqual(len(self.client.inputs), 2)

    def test_lock_prevents_interleaved_delivery(self):
        with delivery.ledger(self.client, self.args()):
            result = self.deliver()
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('another delivery', result['error'])
        self.assertEqual(self.client.inputs, [])

    def test_private_durable_state_stores_no_body(self):
        body = 'PRIVATE BODY DO NOT STORE'
        self.deliver(text=body)
        root = Path(self.temp.name)/'kilix/agent-delivery'
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)
        for f in root.iterdir():
            self.assertEqual(f.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(body, f.read_text())

    def test_symlink_or_corrupt_receipt_is_not_overwritten(self):
        self.deliver()
        f = next(Path(self.temp.name).rglob('*.json'))
        f.write_text('broken')
        self.assertEqual(self.deliver()['status'], 'blocked')
        self.assertEqual(f.read_text(), 'broken')
        f.unlink()
        outside = Path(self.temp.name)/'outside'; outside.write_text('untouched')
        f.symlink_to(outside)
        self.assertEqual(self.deliver()['status'], 'blocked')
        self.assertEqual(outside.read_text(), 'untouched')
        self.assertEqual(len(self.client.inputs), 2)

    def test_main_returns_compact_json_and_nonzero_on_block(self):
        for occupied, code in [(False, 0), (True, 1)]:
            self.client.current = screen('occupied') if occupied else screen()
            args = ['deliver', '2', '--expect-broker', TARGET_BROKER, '--message-id', str(code), '--text', 'Hello there']
            output = io.StringIO()
            with mock.patch.object(control, 'Client', return_value=self.client), contextlib.redirect_stdout(output):
                self.assertEqual(control.main(args), code)
            self.assertEqual(json.loads(output.getvalue())['delivery_verified'], not occupied)

    def test_expired_client_deadline_never_starts_a_request(self):
        client = object.__new__(control.Client)
        client.command = ['/bin/true']
        client.deadline = -1
        with mock.patch.object(control.subprocess, 'Popen') as start:
            with self.assertRaises(control.ControlError):
                client.run(['send-text'], b'hello')
            start.assert_not_called()

    def test_script_entrypoint_reports_client_refusal_as_json(self):
        root = Path(self.temp.name)
        kitten = root/'kitten'
        kitten.write_text('#!/bin/sh\necho "[]"\n')
        kitten.chmod(0o700)
        password = root/'credential'
        password.write_text('fixture only')
        password.chmod(0o600)
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(root),
               'XDG_STATE_HOME': str(root/'state'), 'KITTY_LISTEN_ON': 'unix:/fixture',
               'KITTY_WINDOW_ID': '1', 'KILIX_KITTEN': str(kitten),
               'KILIX_RC_PASSWORD_FILE': str(password), 'PYTHONDONTWRITEBYTECODE': '1'}
        done = subprocess.run([sys.executable, str(Path(control.__file__)), 'deliver', '2',
                               '--expect-broker', TARGET_BROKER, '--message-id', 'script-test',
                               '--text', 'Hello fixture'], capture_output=True, text=True,
                              env=env, timeout=5)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(json.loads(done.stdout)['status'], 'blocked')
        self.assertIn('absent', json.loads(done.stdout)['error'])
        self.assertEqual(done.stderr, '')


if __name__ == '__main__':
    unittest.main()
