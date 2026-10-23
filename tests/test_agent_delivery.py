"""Delivery verifies UI effects and never repeats uncertain input attempts."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
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


def claude_screen(value=None, history="", *, footer=None, prompt_gap=None):
    rule = "\x1b[0;38:2:136:136:136m" + "─" * 80
    if value is None:
        composer = "\u00a0"  # Claude's visible empty-composer placeholder.
    else:
        composer = (" " if prompt_gap is None else prompt_gap) + value
    return (history + "\n" + rule + "\n\x1b[0m❯" +
            composer + "\n" + rule +
            "\n\x1b[0m" + (footer or
            "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents") + "\n")


def wrapped_wire(wire):
    split = len(wire) // 2
    return wire[:split] + "\n  " + wire[split:]


AUTO = "  ⏵⏵ auto mode on · "
FIXTURES = Path(__file__).resolve().parent / "fixtures"
LONG_WRAP_REQUEST = json.loads((FIXTURES / "claude_long_wrap_request.json").read_text())["params"]["text"]
LONG_WRAP_ID = "wrapped-message-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
LONG_WRAP_WIRE = f"[kilix-message:{LONG_WRAP_ID}] {LONG_WRAP_REQUEST}"
LONG_WRAP_SEAMS = ("xxxxxx xxxx", "xxxxxxxx xxxx", "xxxxxxxxxxx xxxx.")
AUTO_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "claude_auto_monitor_footer.ansi"


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


class ClaudeTerminal(Terminal):
    def __init__(self, *, footer=None, prompt_gap=" "):
        super().__init__()
        self.footer = footer
        self.prompt_gap = prompt_gap
        self.panes[1]['foreground_processes'][0]['cmdline'] = ['/bin/claude']
        self.current = claude_screen(footer=self.footer)

    def run(self, args, payload=None):
        if args[0] != 'send-text':
            return super().run(args, payload)
        self.calls.append((args, payload))
        if payload == b'\r':
            self.history = '❯ ' + self.wire
            self.current = claude_screen(history=self.history, footer=self.footer)
            if self.on_submit:
                self.on_submit(self)
        else:
            self.wire = payload.decode()
            self.current = claude_screen(self.wire, history=self.history, footer=self.footer,
                                         prompt_gap=self.prompt_gap)
            if self.on_paste:
                self.on_paste(self)
        return ''


class ComposerTests(unittest.TestCase):
    def test_codex_busy_queue_hint_footer_preserves_exact_draft(self):
        raw = ("• Working (12s • esc to interrupt)\n\n"
               "\x1b[1m›\x1b[22m owned draft probe\n\n"
               "  \x1b[1mtab\x1b[22m to queue message     99% context left\n")
        parsed = delivery.composer(raw)
        self.assertFalse(parsed["empty"])
        self.assertEqual(parsed["wire_text"], "owned draft probe")
        for bad in (
            raw.replace("\x1b[1mtab\x1b[22m", "tab"),
            raw.replace("99%", "101%"),
            raw.replace("context left", "context left extra"),
            raw.replace("• Working (12s • esc to interrupt)", "Idle"),
            raw + "  GPT-example medium · project\n",
        ):
            with self.subTest(screen=bad), self.assertRaises(delivery.ScreenError):
                delivery.composer(bad)

    def test_claude_monitor_footer_preserves_empty_and_draft_detection(self):
        for footer in (
            "  ⏵⏵ bypass permissions on · 1 monitor · esc to interrupt · ← for agents · ↓ to manage",
            "  ⏵⏵ bypass permissions on · 2 monitors · ← for agents · ↓ to manage",
            "  ⏵⏵ bypass permissions on · 1 monitor",
        ):
            with self.subTest(footer=footer):
                self.assertTrue(delivery.composer(claude_screen(footer=footer), 'claude')['empty'])
                result = delivery.composer(claude_screen('existing draft', footer=footer), 'claude')
                self.assertFalse(result['empty'])
                self.assertEqual(result['text'], 'existing draft')

    def test_claude_shell_count_footer_preserves_empty_and_draft_detection(self):
        for footer in (
            "  ⏵⏵ bypass permissions on · 1 shell · ← for agents · ↓ to manage",
            "  ⏵⏵ bypass permissions on · 2 shells",
            "  ⏵⏵ bypass permissions on · 12 shells · esc to interrupt · ← for agents · ↓ to manage",
        ):
            with self.subTest(footer=footer):
                self.assertTrue(delivery.composer(claude_screen(footer=footer), 'claude')['empty'])
                result = delivery.composer(
                    claude_screen('existing draft', footer=footer), 'claude')
                self.assertFalse(result['empty'])
                self.assertEqual(result['text'], 'existing draft')

    def test_claude_unknown_count_footer_refuses(self):
        footer = "  ⏵⏵ bypass permissions on · 1 monitor · esc to interrupt · ← for agents · ↓ to manage"
        shell = "  ⏵⏵ bypass permissions on · 1 shell · ← for agents · ↓ to manage"
        plural_shell = "  ⏵⏵ bypass permissions on · 2 shells · ← for agents · ↓ to manage"
        for invalid in (
            footer.replace('1 monitor', 'monitor'),
            footer.replace('1 monitor', '0 monitors'),
            footer.replace('1 monitor', '1 monitors'),
            footer.replace('1 monitor', '2 monitor'),
            footer.replace('↓ to manage', 'Enter to confirm'),
            footer.replace('bypass permissions on', 'unknown mode'),
            shell.replace('1 shell', 'shell'),
            shell.replace('1 shell', '0 shells'),
            shell.replace('1 shell', '01 shells'),
            shell.replace('1 shell', '1 shells'),
            plural_shell.replace('2 shells', '2 shell'),
            plural_shell.replace('2 shells', '2 panes'),
            plural_shell.replace('↓ to manage', 'Enter to confirm'),
        ):
            with self.subTest(footer=invalid), self.assertRaises(control.ControlError):
                delivery.composer(claude_screen(footer=invalid), 'claude')

    def test_claude_frame_distinguishes_history_empty_and_wrapped_draft(self):
        raw = claude_screen(history='❯ earlier user message\n● Earlier answer')
        self.assertTrue(delivery.composer(raw, 'claude')['empty'])
        for value in ('hello café 世界\n  wrapped tail', 'Try a suggestion', '\x1b[2ma draft'):
            result = delivery.composer(claude_screen(value), 'claude')
            self.assertFalse(result['empty'])
            self.assertEqual(delivery.normalized(result['text']), delivery.normalized(value.replace('\x1b[2m', '')))

    def test_claude_prompt_spacers_preserve_exact_wire_and_payload_spaces(self):
        wire = '[kilix-message:framing-proof] Keep  these   spaces exactly.'
        for gap in (' ', '\u00a0'):
            with self.subTest(prompt_gap=gap):
                result = delivery.composer(
                    claude_screen(wire, prompt_gap=gap), 'claude')
                self.assertFalse(result['empty'])
                self.assertEqual(result['wire_text'], wire)
                self.assertTrue(delivery.composer_matches(result, wire))

    def test_claude_unknown_or_repeated_prompt_spacer_cannot_prove_wire(self):
        wire = '[kilix-message:framing-proof] body'
        unknown = delivery.composer(
            claude_screen(wire, prompt_gap='\u2003'), 'claude')
        self.assertIsNone(unknown['wire_text'])
        self.assertFalse(delivery.composer_matches(unknown, wire))
        repeated = delivery.composer(
            claude_screen(wire, prompt_gap='\u00a0\u00a0'), 'claude')
        self.assertEqual(repeated['wire_text'], '\u00a0' + wire)
        self.assertFalse(delivery.composer_matches(repeated, wire))

    def test_claude_faint_suggestion_stays_occupied_without_wire_proof(self):
        wire = '[kilix-message:faint-proof] visible candidate text'
        typed = delivery.composer(
            claude_screen(wire, prompt_gap='\u00a0'), 'claude')
        faint = delivery.composer(
            claude_screen('\x1b[2m' + wire, prompt_gap='\u00a0'), 'claude')
        self.assertFalse(typed['empty'])
        self.assertEqual(typed['wire_text'], wire)
        self.assertIsNone(typed['faint_wire_text'])
        self.assertFalse(faint['empty'])
        self.assertEqual(faint['text'], wire)
        self.assertIsNone(faint['wire_text'])
        self.assertEqual(faint['faint_wire_text'], wire)
        self.assertFalse(delivery.composer_matches(faint, wire))

    def test_claude_suggestion_ownership_requires_all_faint_single_row(self):
        wire = '[kilix-message:owned-old] complete suggestion text'
        wrapped = delivery.composer(
            claude_screen('\x1b[2m' + wrapped_wire(wire), prompt_gap='\u00a0'), 'claude')
        mixed = delivery.composer(
            claude_screen('\x1b[2m' + wire[:8] + '\x1b[0m' + wire[8:],
                          prompt_gap='\u00a0'), 'claude')
        self.assertIsNone(wrapped['faint_wire_text'])
        self.assertIsNone(mixed['faint_wire_text'])
        self.assertFalse(wrapped['empty'])
        self.assertFalse(mixed['empty'])

    def test_claude_unknown_frames_and_footer_refuse(self):
        for raw in ('❯\u00a0', claude_screen().replace('shift+tab to cycle', 'Enter to confirm'),
                    claude_screen().replace('❯\u00a0', 'Password:'),
                    claude_screen().replace('❯\u00a0', '❯\u00a0\nmenu option')):
            with self.subTest(raw=raw), self.assertRaises(control.ControlError):
                delivery.composer(raw, 'claude')

    def test_placeholder_style_distinguishes_real_input(self):
        self.assertTrue(delivery.composer(screen())["empty"])
        for value in ("Ask Codex to do anything", "someone else's draft", "[Pasted Content 850 chars]"):
            self.assertFalse(delivery.composer(screen(value))["empty"])

    def test_history_prompt_does_not_count_as_composer(self):
        raw = "\x1b[1;2m›\x1b[22m Ask Codex to do anything\n  GPT-6.1-Sol high · ~/project\n"
        with self.assertRaises(control.ControlError):
            delivery.composer(raw)

    def test_wrapped_unicode_and_color_codes(self):
        raw = screen("hello café 世界 \n  next line")
        result = delivery.composer(raw)
        self.assertEqual(delivery.normalized(result["text"]), "hellocafé世界nextline")
        self.assertEqual(result["wire_text"], "hello café 世界 next line")
        claude = delivery.composer(
            claude_screen("hello  café 世界 \n  next  line"), "claude")
        self.assertEqual(claude["wire_text"], "hello  café 世界 next  line")
        ambiguous = delivery.composer(screen("hello\n\n  next line"))
        self.assertIsNone(ambiguous["wire_text"])
        # A truecolor component of 2 must not mark input as faint.
        self.assertFalse(delivery.composer(screen("\x1b[38;2;2;2;2mhello"))["empty"])

    def test_unknown_ui_or_plain_text_cannot_prove_readiness(self):
        for raw in ("$ ", "Password: ", "Trust this folder?", "› Ask Codex to do anything\n",
                    screen().replace("GPT-6.1-Sol", "unknown-model"),
                    screen().replace("\n\n  \x1b[38", "\nmenu selection\n  \x1b[38")):
            with self.subTest(raw=raw):
                with self.assertRaises(control.ControlError):
                    delivery.composer(raw)

    def test_claude_normal_auto_count_footers_preserve_empty_and_draft_detection(self):
        for footer in (
            AUTO + "install gh for PR status · 1 monitor · ← for agents · ↓ to manage",
            AUTO + "install gh for PR status · 2 monitors · ← for agents · ↓ to manage",
            AUTO + "install gh for PR status · 1 shell",
            AUTO + "1 monitor · esc to interrupt · ← for agents · ↓ to manage",
            AUTO + "12 shells",
        ):
            with self.subTest(footer=footer):
                self.assertTrue(delivery.composer(claude_screen(footer=footer), 'claude')['empty'])
                result = delivery.composer(claude_screen('existing draft', footer=footer), 'claude')
                self.assertFalse(result['empty'])
                self.assertEqual(result['text'], 'existing draft')


    def test_claude_actual_normal_auto_shell_and_monitor_footer(self):
        raw = (Path(__file__).resolve().parent / "fixtures" / "claude_auto_shell_monitor_footer.ansi").read_text()
        result = delivery.composer(raw, 'claude')
        self.assertTrue(result['empty'])
        self.assertIn("1 shell, 1 monitor", result['screen'])


    def test_claude_mixed_counts_preserve_typed_drafts(self):
        for mode in [AUTO + "install gh for PR status · ", "  ⏵⏵ bypass permissions on · "]:
            for count in ["1 shell, 1 monitor", "2 shells, 3 monitors", "12 shells, 10 monitors"]:
                footer = mode + count + " · esc to interrupt · ← for agents · ↓ to manage"
                result = delivery.composer(claude_screen('keep this draft', footer=footer), 'claude')
                self.assertFalse(result['empty'])
                self.assertEqual(result['wire_text'], 'keep this draft')


    def test_claude_malformed_mixed_counts_fail_closed(self):
        for count in ["1 shells, 1 monitor", "1 shell, 0 monitors", "1 shell, 2 monitor", "1 shell, 1 monitor, 1 monitor", "1 monitor, 1 shell", "1 shell,1 monitor", "1 shell, 1 monitor extra"]:
            footer = AUTO + "install gh for PR status · " + count + " · ← for agents · ↓ to manage"
            with self.subTest(count=count), self.assertRaises(control.ControlError):
                delivery.composer(claude_screen(footer=footer), 'claude')


    def test_claude_normal_auto_unknown_footers_refuse(self):
        ok = AUTO + "install gh for PR status · 1 monitor · ← for agents · ↓ to manage"
        for invalid in (
            ok.replace('auto mode on', 'auto mode off'),
            ok.replace('auto mode on', 'plan mode on'),
            ok.replace('install gh for PR status', 'install gh for PR status!'),
            ok.replace('install gh for PR status', 'install gh'),
            ok.replace('install gh for PR status', 'update gh for PR status'),
            ok.replace('install gh for PR status · ', 'install gh for PR status · install gh for PR status · '),
            ok.replace('1 monitor', 'monitor'),
            ok.replace('1 monitor', '0 monitors'),
            ok.replace('1 monitor', '1 monitors'),
            ok.replace('1 monitor', '2 monitor'),
            ok.replace('1 monitor', '1 panes'),
            ok.replace('↓ to manage', 'Enter to confirm'),
            ok.replace('auto mode on · ', 'auto mode on · 1 monitor · '),
            ok.replace('← for agents', '← for agents · extra'),
            ok.replace('  ⏵⏵', ' ⏵⏵'),
            ok.replace('·', '.'),
            ok.replace(' · 1 monitor', '\u00a0· 1 monitor'),
            AUTO + "install gh for PR status",
            "  ⏵⏵ auto mode on 1 monitor",
        ):
            with self.subTest(footer=invalid), self.assertRaises(control.ControlError):
                delivery.composer(claude_screen(footer=invalid), 'claude')


    def test_claude_normal_auto_footer_is_not_taken_from_history_or_draft(self):
        footer = AUTO + "install gh for PR status · 1 monitor · ← for agents · ↓ to manage"
        bad = footer.replace('1 monitor', 'monitor')
        # Footer-looking text in history or the typed draft proves nothing.
        for raw in (claude_screen(footer=bad, history=footer),
                    claude_screen(footer, footer=bad),
                    claude_screen(footer + "\n  " + footer, footer=bad)):
            with self.assertRaises(control.ControlError):
                delivery.composer(raw, 'claude')
        result = delivery.composer(claude_screen(footer, footer=footer), 'claude')
        self.assertFalse(result['empty'])
        self.assertEqual(result['wire_text'], footer)


    def test_claude_captured_normal_auto_monitor_screen_keeps_faint_suggestion_occupied(self):
        raw = AUTO_FIXTURE.read_text(encoding='utf-8')
        footer = delivery.composer(raw, 'claude')['screen'].rstrip().splitlines()[-1]
        self.assertEqual(footer, AUTO + "install gh for PR status · 1 monitor · ← for agents · ↓ to manage")
        self.assertNotIn('shift+tab', raw)
        result = delivery.composer(raw, 'claude')
        self.assertFalse(result['empty'])
        self.assertEqual(result['text'], 'check build progress')
        # Faint glyphs are never evidence of a typed wire, only of a suggestion.
        self.assertIsNone(result['wire_text'])
        self.assertFalse(delivery.composer_matches(result, 'check build progress'))
        self.assertEqual(result['faint_wire_text'], 'check build progress')


    def test_claude_captured_normal_auto_screen_empty_only_with_exact_nbsp_placeholder(self):
        raw = AUTO_FIXTURE.read_text(encoding='utf-8')
        suggestion = "\u00a0\x1b[22;2mcheck build progress"
        self.assertEqual(raw.count(suggestion), 1)
        self.assertTrue(delivery.composer(raw.replace(suggestion, "\u00a0"), 'claude')['empty'])
        # Unknown placeholders are neither empty nor a typed wire.
        for replacement in ("", " ", "\u00a0\x1b[22;2mcheck  build progress",
                            "\u00a0\x1b[22;2mcheck build progress "):
            with self.subTest(replacement=replacement):
                try:
                    result = delivery.composer(raw.replace(suggestion, replacement), 'claude')
                except control.ControlError:
                    continue
                self.assertFalse(result['empty'])
                self.assertIsNone(result['wire_text'])
        typed = delivery.composer(
            raw.replace(suggestion, "\u00a0check build progress"), 'claude')
        self.assertFalse(typed['empty'])
        self.assertEqual(typed['wire_text'], 'check build progress')




class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.fd)
        self.store = delivery.Ledger(self.fd, "run.json")
        self.store.write({"status": "running"})

    def replace_during_open(self, *, repeatedly=False):
        original_open = os.open
        replaced = False

        def open_file(name, flags, *args, **kwargs):
            nonlocal replaced
            fd = original_open(name, flags, *args, **kwargs)
            if name == "run.json" and (repeatedly or not replaced):
                replaced = True
                self.store.write({"status": "completed"})
            return fd

        return mock.patch.object(delivery.os, "open", side_effect=open_file)

    def test_read_during_atomic_update_returns_a_valid_snapshot(self):
        with self.replace_during_open():
            self.assertEqual(self.store.read(), {"status": "completed"})

    def test_continuous_replacement_fails_without_an_unbounded_retry(self):
        with self.replace_during_open(repeatedly=True) as opened:
            with self.assertRaisesRegex(control.ControlError, "changed repeatedly"):
                self.store.read()
            self.assertLess(opened.call_count, 100)

    def test_read_still_rejects_shared_or_public_receipts(self):
        path = self.root / "run.json"
        alias = self.root / "alias.json"
        os.link(path, alias)
        with self.assertRaisesRegex(control.ControlError, "private owned"):
            self.store.read()
        alias.unlink()
        path.chmod(0o644)
        with self.assertRaisesRegex(control.ControlError, "private owned"):
            self.store.read()


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

    def args(self, text="Please read the prepared result.", message_id="task-1", mode="steer",
             *extra, finish_staged=False, previous_completed_suggestion_id=None, envelope=False):
        argv = ["deliver", "2", "--expect-broker", TARGET_BROKER,
                "--message-id", message_id, "--text", text,
                "--timeout", "1", "--mode", mode, *extra]
        if finish_staged:
            argv.append("--finish-staged")
        if envelope:
            argv.append("--envelope")
        if previous_completed_suggestion_id is not None:
            argv.extend(["--previous-completed-suggestion-id",
                         previous_completed_suggestion_id])
        return control.parser().parse_args(argv)

    def deliver(self, **kwargs):
        return delivery.deliver(self.client, self.args(**kwargs))

    def test_same_target_prior_receipt_lookup_is_read_only(self):
        target = "a" * 64
        current_id, prior_id = "current-id", "prior-id"
        current_hash = delivery.hashlib.sha256(current_id.encode()).hexdigest()
        prior_hash = delivery.hashlib.sha256(prior_id.encode()).hexdigest()
        record = {"digest": "b" * 64, "phase": "submitted",
                  "message_id": prior_id, "verification": "claude-screen-v1",
                  "process_identity": "c" * 64, "verified_at": 1.0}
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as root:
            prior_path = Path(root) / f"{target}-{prior_hash}.json"
            prior_path.write_text(json.dumps(record))
            prior_path.chmod(0o600)
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                store = delivery.Ledger(
                    fd, f"{target}-{current_hash}.json", target)
                self.assertEqual(store.read(prior_id), record)
                self.assertIsNone(store.read("absent-id"))
                self.assertFalse((Path(root) / (
                    target + "-" + delivery.hashlib.sha256(
                        b"absent-id").hexdigest() + ".json")).exists())
            finally:
                os.close(fd)

    def leave_exact_draft(self, *, message_id="staged-1", text="Review this exact body.",
                          mode="steer"):
        def interrupt_after_paste(_client):
            raise KeyboardInterrupt()
        self.client.on_paste = interrupt_after_paste
        with self.assertRaises(KeyboardInterrupt):
            self.deliver(message_id=message_id, text=text, mode=mode)
        self.client.on_paste = None
        self.assertEqual(self.client.inputs, [
            f"[kilix-message:{message_id}] {text}".encode("utf-8")
        ])

    def test_claude_submission_and_duplicate_keep_at_most_once_input(self):
        self.client = ClaudeTerminal()
        result = self.deliver(text='Read the full audit brief.')
        self.assertEqual(result['status'], 'submitted')
        self.assertEqual(result['verification'], 'claude-screen-v1')
        self.assertEqual(self.client.inputs, [b'[kilix-message:task-1] Read the full audit brief.', b'\r'])
        calls = len(self.client.calls)
        duplicate = self.deliver(text='Read the full audit brief.')
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(duplicate['verification'], 'claude-screen-v1')
        self.assertEqual(len(self.client.calls), calls)

    def test_claude_nbsp_framing_submits_exact_spaced_wire_once(self):
        self.client = ClaudeTerminal(prompt_gap='\u00a0')
        body = 'Read  the full audit brief with these   spaces.'
        wire = f'[kilix-message:task-1] {body}'
        result = self.deliver(text=body)
        self.assertEqual(result['status'], 'submitted')
        self.assertEqual(self.client.inputs, [wire.encode(), b'\r'])
        calls = len(self.client.calls)
        duplicate = self.deliver(text=body)
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(len(self.client.calls), calls)

    def test_codex_wrapped_normal_delivery_proves_exact_wire_once(self):
        body = "Review this wrapped body café " + "界" * 30
        self.client.on_paste = lambda c: setattr(
            c, "current", screen(wrapped_wire(c.wire), history=c.history))
        result = self.deliver(text=body)
        wire = f"[kilix-message:task-1] {body}"
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(self.client.inputs, [wire.encode(), b"\r"])

    def test_claude_wrapped_normal_delivery_proves_exact_wire_once(self):
        self.client = ClaudeTerminal()
        body = "Review this wrapped body café " + "界" * 30
        self.client.on_paste = lambda c: setattr(
            c, "current", claude_screen(wrapped_wire(c.wire), history=c.history,
                                        footer=c.footer))
        result = self.deliver(text=body)
        wire = f"[kilix-message:task-1] {body}"
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(self.client.inputs, [wire.encode(), b"\r"])

    def test_claude_monitor_submission_is_verified_and_never_repeated(self):
        footer = "  ⏵⏵ bypass permissions on · 1 monitor · esc to interrupt · ← for agents · ↓ to manage"
        self.client = ClaudeTerminal(footer=footer)
        result = self.deliver(text='Review the finished patch.')
        self.assertEqual(result['status'], 'submitted')
        self.assertEqual(result['verification'], 'claude-screen-v1')
        self.assertEqual(self.client.inputs,
                         [b'[kilix-message:task-1] Review the finished patch.', b'\r'])
        calls = len(self.client.calls)
        duplicate = self.deliver(text='Review the finished patch.')
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(len(self.client.calls), calls)

    def test_claude_shell_count_footer_delivery_is_verified_once(self):
        footer = "  ⏵⏵ bypass permissions on · 1 shell · ← for agents · ↓ to manage"
        self.client = ClaudeTerminal(footer=footer)
        result = self.deliver(text='Read the reviewed delivery patch.')
        self.assertEqual(result['status'], 'submitted')
        self.assertEqual(self.client.inputs, [
            b'[kilix-message:task-1] Read the reviewed delivery patch.', b'\r'
        ])
        calls = len(self.client.calls)
        duplicate = self.deliver(text='Read the reviewed delivery patch.')
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(len(self.client.calls), calls)

    def test_claude_monitored_draft_and_unknown_footer_receive_no_input(self):
        footer = "  ⏵⏵ bypass permissions on · 1 monitor · esc to interrupt · ← for agents · ↓ to manage"
        self.client = ClaudeTerminal(footer=footer)
        self.client.current = claude_screen('do not overwrite', footer=footer)
        self.assertEqual(self.deliver()['status'], 'blocked')
        self.client.current = claude_screen(footer=footer.replace('↓ to manage', 'Enter to confirm'))
        self.assertEqual(self.deliver()['status'], 'blocked')
        self.assertEqual(self.client.inputs, [])

    def test_claude_monitor_hints_can_disappear_when_paste_wraps(self):
        footer = "  ⏵⏵ bypass permissions on · 1 monitor · esc to interrupt · ← for agents · ↓ to manage"
        self.client = ClaudeTerminal(footer=footer)

        def compact(c):
            c.footer = '  ⏵⏵ bypass permissions on · 1 monitor'
            c.current = claude_screen(c.wire, history=c.history, footer=c.footer)

        self.client.on_paste = compact
        self.assertEqual(self.deliver()['status'], 'submitted')
        self.assertEqual(self.client.inputs,
                         [b'[kilix-message:task-1] Please read the prepared result.', b'\r'])

    def test_claude_occupied_input_and_defer_receive_no_input(self):
        self.client = ClaudeTerminal()
        self.client.current = claude_screen('existing draft')
        self.assertEqual(self.deliver()['status'], 'blocked')
        self.client.current = claude_screen()
        result = self.deliver(mode='defer')
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('Tab changes permission mode', result['error'])
        self.assertEqual(self.client.inputs, [])

    def test_claude_partial_paste_and_unaccepted_enter_are_not_resent(self):
        for fail_after_paste in (True, False):
            self.client = ClaudeTerminal()
            if fail_after_paste:
                self.client.on_paste = lambda c: setattr(c, 'current', claude_screen('partial'))
            else:
                self.client.on_submit = lambda c: setattr(c, 'current', claude_screen(c.wire))
            name = 'claude-paste' if fail_after_paste else 'claude-submit'
            self.assertEqual(self.deliver(message_id=name)['status'], 'uncertain')
            count = len(self.client.inputs)
            self.assertEqual(self.deliver(message_id=name)['status'], 'uncertain')
            self.assertEqual(len(self.client.inputs), count)

    def test_claude_crash_after_submit_recovers_without_input(self):
        self.client = ClaudeTerminal()
        self.client.on_submit = lambda c: (_ for _ in ()).throw(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.deliver()
        self.client.on_submit = None
        self.assertEqual(self.deliver()['status'], 'submitted')
        self.assertEqual(len(self.client.inputs), 2)

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

    def test_explicit_finish_staged_submits_exact_codex_draft_once(self):
        self.leave_exact_draft()
        result = self.deliver(message_id="staged-1", text="Review this exact body.",
                              finish_staged=True)
        self.assertEqual(result["status"], "submitted")
        self.assertTrue(result["duplicate"])
        self.assertEqual(self.client.inputs, [
            b"[kilix-message:staged-1] Review this exact body.", b"\r"
        ])
        calls = len(self.client.calls)
        repeated = self.deliver(message_id="staged-1", text="Review this exact body.",
                                finish_staged=True)
        self.assertEqual(repeated["status"], "submitted")
        self.assertTrue(repeated["duplicate"])
        self.assertEqual(len(self.client.calls), calls)

    def test_explicit_finish_staged_submits_exact_claude_draft_once(self):
        self.client = ClaudeTerminal()
        self.leave_exact_draft(message_id="claude-staged", text="Read the reviewed patch.")
        result = self.deliver(message_id="claude-staged", text="Read the reviewed patch.",
                              finish_staged=True)
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["verification"], "claude-screen-v1")
        self.assertEqual(self.client.inputs, [
            b"[kilix-message:claude-staged] Read the reviewed patch.", b"\r"
        ])

    def test_explicit_finish_staged_accepts_claude_nbsp_frame_once(self):
        self.client = ClaudeTerminal(prompt_gap='\u00a0')
        message_id, body = 'claude-nbsp-staged', 'Read  the exact staged patch.'
        self.leave_exact_draft(message_id=message_id, text=body)
        result = self.deliver(message_id=message_id, text=body, finish_staged=True)
        wire = f'[kilix-message:{message_id}] {body}'
        self.assertEqual(result['status'], 'submitted')
        self.assertEqual(result['verification'], 'claude-screen-v1')
        self.assertEqual(self.client.inputs, [wire.encode(), b'\r'])
        calls = len(self.client.calls)
        duplicate = self.deliver(message_id=message_id, text=body, finish_staged=True)
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(len(self.client.calls), calls)

    def test_finish_staged_unknown_claude_prompt_gap_sends_no_key(self):
        self.client = ClaudeTerminal()
        message_id, body = 'claude-unknown-gap', 'Read the staged patch.'
        wire = f'[kilix-message:{message_id}] {body}'
        self.leave_exact_draft(message_id=message_id, text=body)
        self.client.current = claude_screen(
            wire, history=self.client.history, footer=self.client.footer,
            prompt_gap='\u2003')
        result = self.deliver(message_id=message_id, text=body, finish_staged=True)
        self.assertEqual(result['status'], 'uncertain')
        self.assertIn('does not match', result['error'])
        self.assertEqual(self.client.inputs, [wire.encode()])

    def test_finish_staged_faint_claude_suggestion_cannot_send_key(self):
        self.client = ClaudeTerminal(prompt_gap='\u00a0')
        message_id, body = 'claude-faint-suggestion', 'Read the reviewed patch.'
        wire = f'[kilix-message:{message_id}] {body}'
        self.leave_exact_draft(message_id=message_id, text=body)
        self.client.current = claude_screen(
            '\x1b[2m' + wire, history=self.client.history,
            footer=self.client.footer, prompt_gap='\u00a0')
        result = self.deliver(message_id=message_id, text=body, finish_staged=True)
        self.assertEqual(result['status'], 'uncertain')
        self.assertIn('does not match', result['error'])
        self.assertEqual(self.client.inputs, [wire.encode()])
        receipt = next(Path(self.temp.name).rglob('*.json'))
        self.assertEqual(json.loads(receipt.read_text())['phase'], 'pasting')
        repeated = self.deliver(message_id=message_id, text=body, finish_staged=True)
        self.assertEqual(repeated['status'], 'uncertain')
        self.assertEqual(self.client.inputs, [wire.encode()])
        self.assertEqual(json.loads(receipt.read_text())['phase'], 'pasting')

    def test_codex_wrapped_finish_staged_proves_exact_wire_once(self):
        body = "Review this wrapped body café " + "界" * 30
        wire = f"[kilix-message:wrapped-codex] {body}"
        self.leave_exact_draft(message_id="wrapped-codex", text=body)
        self.client.current = screen(wrapped_wire(wire), history=self.client.history)
        result = self.deliver(message_id="wrapped-codex", text=body, finish_staged=True)
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(self.client.inputs, [wire.encode(), b"\r"])

    def test_claude_wrapped_finish_staged_proves_exact_wire_once(self):
        self.client = ClaudeTerminal()
        body = "Review this wrapped body café " + "界" * 30
        wire = f"[kilix-message:wrapped-claude] {body}"
        self.leave_exact_draft(message_id="wrapped-claude", text=body)
        self.client.current = claude_screen(wrapped_wire(wire), history=self.client.history,
                                            footer=self.client.footer)
        result = self.deliver(message_id="wrapped-claude", text=body, finish_staged=True)
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(self.client.inputs, [wire.encode(), b"\r"])

    def test_codex_finish_staged_rejects_unobserved_wrap_separator(self):
        message_id, body = "missing-seam-codex", "body after marker"
        marker = f"[kilix-message:{message_id}]"
        wire = marker + " " + body
        self.leave_exact_draft(message_id=message_id, text=body)
        self.client.current = screen(marker + "\n  " + body, history=self.client.history)
        result = self.deliver(message_id=message_id, text=body, finish_staged=True)
        self.assertEqual(result["status"], "uncertain")
        self.assertIn("does not match", result["error"])
        self.assertEqual(self.client.inputs, [wire.encode()])

    def test_claude_finish_staged_rejects_unobserved_wrap_separator(self):
        self.client = ClaudeTerminal()
        message_id, body = "missing-seam-claude", "body after marker"
        marker = f"[kilix-message:{message_id}]"
        wire = marker + " " + body
        self.leave_exact_draft(message_id=message_id, text=body)
        self.client.current = claude_screen(marker + "\n  " + body,
                                            history=self.client.history,
                                            footer=self.client.footer)
        result = self.deliver(message_id=message_id, text=body, finish_staged=True)
        self.assertEqual(result["status"], "uncertain")
        self.assertIn("does not match", result["error"])
        self.assertEqual(self.client.inputs, [wire.encode()])

    def test_claude_actual_long_wrap_screens_refuse_as_ambiguous_with_diagnostic(self):
        for name in ("claude_long_wrap_composer_01.ansi", "claude_long_wrap_composer_02.ansi"):
            with self.subTest(name=name):
                self.setUp()
                self.client = ClaudeTerminal()
                self.client.panes[1]["columns"] = 141   # geometry metadata must not turn rows into proof
                self.leave_exact_draft(message_id=LONG_WRAP_ID, text=LONG_WRAP_REQUEST)
                self.client.current = (FIXTURES / name).read_text(encoding="utf-8")
                parsed = delivery.composer(self.client.current, "claude")
                self.assertFalse(delivery.composer_matches(parsed, LONG_WRAP_WIRE))
                result = self.deliver(message_id=LONG_WRAP_ID, text=LONG_WRAP_REQUEST,
                                      finish_staged=True)
                self.assertEqual(result["status"], "uncertain")
                self.assertIn("same words", result["error"])
                self.assertIn("--envelope", result["error"])
                self.assertEqual(self.client.inputs, [LONG_WRAP_WIRE.encode()])
                self.addCleanup(self.temp.cleanup)

    def test_claude_wrapped_seam_collisions_never_get_enter(self):
        """Rows identical to the saved screen can hide a newline or extra space."""
        raw = (FIXTURES / "claude_long_wrap_composer_01.ansi").read_text(encoding="utf-8")
        parsed = delivery.composer(raw, "claude")
        drafts = {"newline": [LONG_WRAP_WIRE.replace(seam, seam.replace(" ", "\n"))
                              for seam in LONG_WRAP_SEAMS],
                  "all-newlines": [LONG_WRAP_WIRE.replace("xxxxxx xxxx", "xxxxxx\nxxxx")
                                   .replace("xxxxxxxx xxxx", "xxxxxxxx\nxxxx")
                                   .replace("xxxxxxxxxxx xxxx.", "xxxxxxxxxxx\nxxxx.")],
                  "double-space": [LONG_WRAP_WIRE.replace(seam, seam.replace(" ", "  "))
                                   for seam in LONG_WRAP_SEAMS]}
        for kind, variants in drafts.items():
            for draft in variants:
                with self.subTest(kind=kind, draft=draft[-30:]):
                    self.setUp()
                    self.client = ClaudeTerminal()
                    self.client.panes[1]["columns"] = 141
                    self.leave_exact_draft(message_id=LONG_WRAP_ID, text=LONG_WRAP_REQUEST)
                    # What the terminal shows for that draft: the identical saved rows.
                    self.client.current = raw
                    self.assertEqual(delivery.composer(self.client.current, "claude")["screen"],
                                     parsed["screen"])
                    result = self.deliver(message_id=LONG_WRAP_ID, text=LONG_WRAP_REQUEST,
                                          finish_staged=True)
                    self.assertEqual(result["status"], "uncertain")
                    self.assertEqual(self.client.inputs, [LONG_WRAP_WIRE.encode()])
                    self.addCleanup(self.temp.cleanup)

    def test_claude_long_unwrapped_exact_wire_still_delivers(self):
        # Short exact wires, including a hard-wrapped one, keep working unchanged.
        self.client = ClaudeTerminal()
        result = self.deliver(message_id="short-wire", text="Read the full audit brief.")
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(self.client.inputs, [b"[kilix-message:short-wire] Read the full audit brief.", b"\r"])
        self.assertNotIn("envelope", result)
        self.assertFalse((Path(self.temp.name) / "kilix/envelopes").exists())

    def envelope_paths(self):
        root = Path(self.temp.name) / "kilix/envelopes"
        return sorted(root.glob("*.txt")) if root.exists() else []

    def test_envelope_delivers_exact_text_by_short_one_row_pointer_once(self):
        self.client = ClaudeTerminal()
        self.client.panes[1]["columns"] = 220   # test state dir is long; real ~/.local/state paths are ~40
        text = "Keep  double   spaces, café 世界 and the tail.  " + "word " * 60 + "end"
        result = self.deliver(message_id="env-1", text=text, envelope=True)
        self.assertEqual(result["status"], "submitted")
        (path,) = self.envelope_paths()
        data = path.read_bytes()
        self.assertEqual(data, text.encode())
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        sha = delivery.hashlib.sha256(data).hexdigest()
        self.assertEqual(path.name, sha[:32] + ".txt")
        self.assertEqual(result["envelope"], {"path": str(path), "sha256": sha, "bytes": len(data)})
        wire = f"[kilix-message:env-1] envelope {path} bytes={len(data)}"
        self.assertEqual(self.client.inputs, [wire.encode(), b"\r"])
        self.assertLess(delivery.cell_width(wire), 220 - 3)
        self.assertFalse(result["acknowledgment_verified"])
        self.assertFalse(result["completion_verified"])
        again = self.deliver(message_id="env-1", text=text, envelope=True)
        self.assertEqual(self.client.inputs, [wire.encode(), b"\r"])
        self.assertTrue(again["duplicate"])

    def test_envelope_refuses_before_input_when_pointer_would_wrap_or_file_differs(self):
        self.client = ClaudeTerminal()
        self.client.panes[1]["columns"] = 60
        result = self.deliver(message_id="env-narrow", text="some body", envelope=True)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("would wrap", result["error"])
        self.assertEqual(self.client.inputs, [])
        # A pre-existing file with other content under the same name is never trusted.
        self.setUp()
        self.client = ClaudeTerminal()
        text = "body to bind"
        root = Path(self.temp.name) / "kilix/envelopes"
        root.mkdir(parents=True, mode=0o700)
        name = delivery.hashlib.sha256(text.encode()).hexdigest()[:32] + ".txt"
        (root / name).write_text("different")
        (root / name).chmod(0o600)
        result = self.deliver(message_id="env-clash", text=text, envelope=True)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("exact message text", result["error"])
        self.assertEqual(self.client.inputs, [])

    def test_envelope_finish_staged_requires_the_exact_existing_file(self):
        self.client = ClaudeTerminal()
        text = "finish via pointer " * 20
        def interrupt(_client):
            raise KeyboardInterrupt()
        self.client.on_paste = interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.deliver(message_id="env-finish", text=text, envelope=True)
        self.client.on_paste = None
        staged = self.client.inputs[0]
        self.client.current = claude_screen(staged.decode(), history=self.client.history,
                                            footer=self.client.footer)
        (path,) = self.envelope_paths()
        path.write_text("tampered")
        result = self.deliver(message_id="env-finish", text=text, envelope=True, finish_staged=True)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("exact message text", result["error"])
        self.assertEqual(self.client.inputs, [staged])
        path.write_bytes(text.encode())
        result = self.deliver(message_id="env-finish", text=text, envelope=True, finish_staged=True)
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(self.client.inputs, [staged, b"\r"])

    def test_explicit_defer_finish_rechecks_busy_layout_and_uses_one_tab(self):
        self.client.busy = True
        self.client.current = screen(busy=True)
        self.leave_exact_draft(message_id="defer-staged", text="Queue this exact follow-up.",
                               mode="defer")
        self.client.busy = False
        self.client.current = screen(
            "[kilix-message:defer-staged] Queue this exact follow-up.", busy=False
        )
        result = self.deliver(message_id="defer-staged", text="Queue this exact follow-up.",
                              mode="defer", finish_staged=True)
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(self.client.inputs, [
            b"[kilix-message:defer-staged] Queue this exact follow-up."
        ])

        self.client.busy = True
        self.client.current = screen(
            "[kilix-message:defer-staged] Queue this exact follow-up.", busy=True
        )
        result = self.deliver(message_id="defer-staged", text="Queue this exact follow-up.",
                              mode="defer", finish_staged=True)
        self.assertEqual(result["status"], "deferred")
        self.assertEqual(self.client.inputs[-1], b"\t")

    def test_explicit_finish_refuses_partial_empty_or_collapsed_composer(self):
        for label, draft in (("partial", "part of the draft"), ("empty", None),
                             ("different", "Thefullstagedcontent."),
                             ("collapsed", "[Pasted Content 44 chars]")):
            with self.subTest(state=label):
                self.client = Terminal()
                self.leave_exact_draft(message_id=label, text="The full staged content.")
                self.client.current = screen(draft)
                result = self.deliver(message_id=label, text="The full staged content.",
                                      finish_staged=True)
                self.assertEqual(result["status"], "uncertain")
                self.assertEqual(self.client.inputs,
                                 [f"[kilix-message:{label}] The full staged content.".encode()])

    def test_explicit_finish_refuses_a_previously_visible_marker(self):
        self.leave_exact_draft(message_id="already-seen")
        marker = "[kilix-message:already-seen]"
        self.client.current = screen("Review this exact body.", history=marker)
        result = self.deliver(message_id="already-seen", text="Review this exact body.",
                              finish_staged=True)
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(len(self.client.inputs), 1)

    def test_explicit_finish_requires_exact_id_body_mode_target_and_process(self):
        self.leave_exact_draft()
        attempts = [
            {"message_id": "different-id"},
            {"message_id": "staged-1", "text": "different body"},
            {"message_id": "staged-1", "mode": "defer"},
            {"message_id": "staged-1", "pane": 3},
        ]
        for values in attempts:
            with self.subTest(values=values):
                before = len(self.client.inputs)
                args = self.args(message_id="staged-1", text="Review this exact body.",
                                 finish_staged=True)
                for key, value in values.items():
                    setattr(args, key, value)
                result = delivery.deliver(self.client, args)
                self.assertIn(result["status"], ("blocked", "uncertain"))
                self.assertEqual(len(self.client.inputs), before)

        self.client.panes[1]["foreground_processes"][0]["pid"] = 999
        result = self.deliver(message_id="staged-1", text="Review this exact body.",
                              finish_staged=True)
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(len(self.client.inputs), 1)

    def test_explicit_finish_with_changed_broker_finds_no_old_receipt(self):
        self.leave_exact_draft()
        args = self.args(message_id="staged-1", text="Review this exact body.",
                         finish_staged=True)
        args.expect_broker = "c" * 16
        result = delivery.deliver(self.client, args)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(len(self.client.inputs), 1)

    def test_explicit_finish_requires_existing_private_ledger_and_valid_receipt(self):
        self.leave_exact_draft(message_id="unavailable")
        shutil.rmtree(Path(self.temp.name) / "kilix" / "agent-delivery")
        result = self.deliver(message_id="unavailable", text="Review this exact body.",
                              finish_staged=True)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(len(self.client.inputs), 1)
        self.assertFalse((Path(self.temp.name) / "kilix" / "agent-delivery").exists())

        self.client = Terminal()
        self.leave_exact_draft(message_id="corrupt")
        path = next((Path(self.temp.name) / "kilix" / "agent-delivery").glob("*.json"))
        path.write_text("broken receipt")
        result = self.deliver(message_id="corrupt", text="Review this exact body.",
                              finish_staged=True)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(len(self.client.inputs), 1)
        self.assertEqual(path.read_text(), "broken receipt")

    def test_explicit_finish_never_keys_submitting_or_unknown_phase(self):
        for phase in ("submitting", "future-phase"):
            with self.subTest(phase=phase):
                self.client = Terminal()
                self.leave_exact_draft(message_id=phase, text="Review this exact body.")
                matches = [path for path in (Path(self.temp.name) / "kilix" / "agent-delivery").glob("*.json")
                           if json.loads(path.read_text()).get("message_id") == phase]
                self.assertEqual(len(matches), 1)
                path = matches[0]
                record = json.loads(path.read_text())
                record["phase"] = phase
                path.write_text(json.dumps(record))
                result = self.deliver(message_id=phase, text="Review this exact body.",
                                      finish_staged=True)
                self.assertEqual(result["status"], "uncertain")
                self.assertEqual(len(self.client.inputs), 1)

    def test_durable_submitting_precedes_key_and_crash_before_key_never_retries(self):
        self.leave_exact_draft(message_id="crash-before-key")
        original_write = delivery.Ledger.write

        def crash_after_submitting(store, record):
            original_write(store, record)
            if record.get("phase") == "submitting":
                raise KeyboardInterrupt()

        with mock.patch.object(delivery.Ledger, "write", crash_after_submitting):
            with self.assertRaises(KeyboardInterrupt):
                self.deliver(message_id="crash-before-key", text="Review this exact body.",
                             finish_staged=True)
        path = next((Path(self.temp.name) / "kilix" / "agent-delivery").glob("*.json"))
        self.assertEqual(json.loads(path.read_text())["phase"], "submitting")
        self.assertEqual(len(self.client.inputs), 1)
        retry = self.deliver(message_id="crash-before-key", text="Review this exact body.",
                             finish_staged=True)
        self.assertEqual(retry["status"], "uncertain")
        self.assertEqual(len(self.client.inputs), 1)

    def test_crash_after_finish_key_recovers_only_by_observation(self):
        self.leave_exact_draft(message_id="crash-after-key")
        self.client.on_submit = lambda _client: (_ for _ in ()).throw(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.deliver(message_id="crash-after-key", text="Review this exact body.",
                         finish_staged=True)
        self.assertEqual(self.client.inputs[-1], b"\r")
        self.client.on_submit = None
        calls = len(self.client.calls)
        recovered = self.deliver(message_id="crash-after-key", text="Review this exact body.",
                                 finish_staged=True)
        self.assertEqual(recovered["status"], "submitted")
        self.assertEqual(len(self.client.inputs), 2)
        self.assertGreater(len(self.client.calls), calls)

    def test_default_recovery_remains_observation_only_for_exact_staged_draft(self):
        self.leave_exact_draft()
        result = self.deliver(message_id="staged-1", text="Review this exact body.")
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(len(self.client.inputs), 1)

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

    def test_finish_staged_rejects_a_changed_provider_before_key(self):
        self.leave_exact_draft()
        self.client.panes[1]['foreground_processes'][0]['cmdline'][0] = '/bin/claude'
        result = self.deliver(message_id='staged-1', text='Review this exact body.',
                              finish_staged=True)
        self.assertEqual(result['status'], 'uncertain')
        self.assertIn('provider changed', result['error'])
        self.assertEqual(len(self.client.inputs), 1)

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

    def test_busy_paste_switches_to_styled_queue_hint_before_one_tab(self):
        self.client.current = screen(busy=True)
        self.client.busy = True
        def queue_hint(client):
            client.current = ("• Working (12s • esc to interrupt)\n\n"
                "\x1b[1m›\x1b[22m " + client.wire + "\n\n"
                "  \x1b[1mtab\x1b[22m to queue message     99% context left\n")
        self.client.on_paste = queue_hint
        result = self.deliver(mode="defer")
        self.assertEqual(result["status"], "deferred")
        self.assertEqual(self.client.inputs,
                         [b"[kilix-message:task-1] Please read the prepared result.", b"\t"])
        again = self.deliver(mode="defer")
        self.assertTrue(again["duplicate"])
        self.assertEqual(len(self.client.inputs), 2)

    def test_message_already_visible_without_receipt_refuses(self):
        self.client.current = screen(history='[kilix-message:task-1] earlier message')
        self.assertEqual(self.deliver()['status'], 'blocked')
        self.assertEqual(self.client.inputs, [])

    def test_claude_captured_normal_auto_screen_delivery_receives_no_input(self):
        self.client = ClaudeTerminal()
        self.client.current = AUTO_FIXTURE.read_text(encoding='utf-8')
        result = self.deliver()
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(self.client.inputs, [])
        self.assertFalse(any(args[0] == 'send-text' for args, _ in self.client.calls))


    def test_claude_normal_auto_monitor_delivery_is_verified_once(self):
        footer = AUTO + "install gh for PR status · 1 monitor · ← for agents · ↓ to manage"
        self.client = ClaudeTerminal(footer=footer)
        result = self.deliver(text='Review the finished patch.')
        self.assertEqual(result['status'], 'submitted')
        self.assertEqual(self.client.inputs,
                         [b'[kilix-message:task-1] Review the finished patch.', b'\r'])
        calls = len(self.client.calls)
        duplicate = self.deliver(text='Review the finished patch.')
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(len(self.client.calls), calls)


    def test_claude_normal_auto_draft_and_unknown_footer_receive_no_input(self):
        footer = AUTO + "install gh for PR status · 1 monitor · ← for agents · ↓ to manage"
        self.client = ClaudeTerminal(footer=footer)
        self.client.current = claude_screen('do not overwrite', footer=footer)
        self.assertEqual(self.deliver()['status'], 'blocked')
        self.client.current = claude_screen(footer=footer.replace('1 monitor', 'monitor'))
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

    def test_cli_finish_staged_flag_completes_without_reposting_body(self):
        body = "Read the exact file body."
        self.leave_exact_draft(message_id="cli-staged", text=body)
        original = Path(self.temp.name) / "original-message"
        original.write_text(body)
        output = io.StringIO()
        args = ["deliver", "2", "--expect-broker", TARGET_BROKER, "--message-id", "cli-staged",
                "--file", str(original), "--finish-staged"]
        with mock.patch.object(control, "Client", return_value=self.client), \
                contextlib.redirect_stdout(output):
            self.assertEqual(control.main(args), 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "submitted")
        self.assertEqual(self.client.inputs[-1], b"\r")

    def test_cli_requires_original_body_even_for_finish_staged(self):
        with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr, \
                mock.patch.object(control, "Client") as client, \
                self.assertRaises(SystemExit) as caught:
            control.main(["deliver", "2", "--expect-broker", TARGET_BROKER,
                          "--message-id", "old", "--finish-staged"])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("requires --text or --file", stderr.getvalue())
        client.assert_not_called()

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
