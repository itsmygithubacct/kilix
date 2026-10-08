"""The two voice widgets as the tab bar renders them, and the glyph guard.

The widgets live in the kitty fork, which is a submodule: the segment tests
skip when it is not checked out.  The symbol_map assertion does not skip,
because `config/kitty.conf` is Kilix's own file and an unmapped codepoint has
no symptom other than a blank box in the tab bar.
"""

import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import pty
import re
import shutil
import socket
import sys
import tempfile
import termios
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
FORK = ROOT / "src" / "kitty"
FORK_VOICE = FORK / "kilix_voice.py"
FORK_TABS = FORK / "tabs.py"
KITTY_CONF = ROOT / "config" / "kitty.conf"

# Verified against the pinned Symbols Nerd Font Mono by glyph name rather than
# by "the codepoint is mapped": md-account_voice, md-microphone,
# md-microphone_off.
SPEAK_CODEPOINT = 0xF05CB
MICROPHONE_CODEPOINT = 0xF036C
MICROPHONE_OFF_CODEPOINT = 0xF036D

_MAPPED = re.compile(r"U\+([0-9A-Fa-f]+)")
_CHR_LITERAL = re.compile(r"chr\(0x([0-9A-Fa-f]+)\)")


def _symbol_map_codepoints() -> set[int]:
    mapped: set[int] = set()
    for line in KITTY_CONF.read_text().splitlines():
        if not line.startswith("symbol_map "):
            continue
        for entry in line.split()[1].split(","):
            # kitty accepts either a single codepoint or a U+X-U+Y range.
            bounds = [int(value, 16) for value in _MAPPED.findall(entry)]
            if bounds:
                mapped.update(range(bounds[0], bounds[-1] + 1))
    return mapped


def _dictation_toggle_source() -> str:
    """The one dictation toggle behind the microphone click and Ctrl+Shift+D.

    The click handler in tabs.py only delegates to it (asserted in
    DictationHotkeyTests), so the guards are checked where they now live.
    """
    source = FORK_VOICE.read_text()
    body = source[source.index("def toggle_dictation("):]
    return body[:body.index("\ndef ", 10)]


def _load_fork_voice():
    """Import the fork's voice chrome without a built kitty.

    `kilix_voice.py` is a package module: it reads the shared settings through
    `kilix_battery` and takes three helpers from kitty's own `utils`, which a
    plain source checkout cannot provide.  Stubbing exactly those leaves what
    is under test — the settings gating and the glyphs — as it ships.
    """
    package_name = "kilix_fork_voice_test"
    package = types.ModuleType(package_name)
    package.__path__ = [str(FORK)]
    sys.modules[package_name] = package

    rgb = types.ModuleType(f"{package_name}.rgb")
    rgb.to_color = lambda spec: spec
    rgb.color_from_int = lambda value: value
    utils = types.ModuleType(f"{package_name}.utils")
    utils.color_as_int = lambda color: 0
    utils.log_error = lambda *args: None
    utils.which = shutil.which
    # Segment colour is the one thing these tests do not assert, so the option
    # lookup behind it only has to exist.
    fast_data_types = types.ModuleType(f"{package_name}.fast_data_types")
    fast_data_types.get_options = lambda: types.SimpleNamespace(foreground=0)
    for name, module in (
        ("rgb", rgb), ("utils", utils), ("fast_data_types", fast_data_types),
    ):
        sys.modules[f"{package_name}.{name}"] = module

    for name in ("kilix_battery", "kilix_voice"):
        loader = importlib.machinery.SourceFileLoader(
            f"{package_name}.{name}", str(FORK / f"{name}.py"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        if spec is None:
            raise RuntimeError(f"could not load {name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[loader.name] = module
        loader.exec_module(module)
    return sys.modules[f"{package_name}.kilix_voice"]


class VoiceGlyphTests(unittest.TestCase):
    def test_widget_codepoints_are_pinned_to_the_symbol_font(self):
        mapped = _symbol_map_codepoints()
        for codepoint in (
            SPEAK_CODEPOINT, MICROPHONE_CODEPOINT, MICROPHONE_OFF_CODEPOINT,
        ):
            self.assertIn(codepoint, mapped, f"U+{codepoint:04X} would be tofu")

    @unittest.skipUnless(
        FORK_VOICE.is_file(), "kitty fork submodule is not checked out")
    def test_every_glyph_the_widget_emits_is_mapped(self):
        # The assertion above covers the three glyphs the plan names; this one
        # covers a fourth added later, which is the case that would otherwise
        # ship a blank box with nothing else wrong.
        source = FORK_VOICE.read_text()
        emitted = {int(value, 16) for value in _CHR_LITERAL.findall(source)}
        emitted.update(
            ord(character) for character in source if ord(character) >= 0xE000)
        self.assertGreaterEqual(len(emitted), 3)
        unmapped = sorted(
            f"U+{codepoint:04X}"
            for codepoint in emitted - _symbol_map_codepoints())
        self.assertEqual(unmapped, [], "these would render as tofu")


@unittest.skipUnless(
    FORK_VOICE.is_file(), "kitty fork submodule is not checked out")
class VoiceSegmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.voice = _load_fork_voice()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.files = 0
        self.voice.voice_state = self.voice.VoiceState()
        self.voice._VOICE_TIMER_ID = None

    def settings(self, **values):
        """Point the fork's reader at a fresh settings file.

        A new path each time: the reader caches on the file's identity and
        mtime, and two writes inside one timer tick can share both.
        """
        self.files += 1
        path = Path(self.tmp.name) / f"settings-{self.files}.conf"
        path.write_text(
            "".join(f"{key}={value}\n" for key, value in values.items()))
        return mock.patch.dict(
            os.environ, {"GPU_TERMINAL_SETTINGS_FILE": str(path)}, clear=False)

    def test_segments_disappear_when_their_toggle_is_off(self):
        with self.settings(KILIX_CHROME_SPEAK="0", KILIX_CHROME_DICTATE="0"):
            self.assertIsNone(self.voice.speak_segment())
            self.assertIsNone(self.voice.dictate_segment())

    def test_segments_carry_their_glyph_and_action_when_on(self):
        with self.settings(KILIX_CHROME_SPEAK="1", KILIX_CHROME_DICTATE="1"):
            text, action = self.voice.speak_segment()[:2]
            self.assertEqual(text.strip(), chr(SPEAK_CODEPOINT))
            self.assertEqual(action, self.voice.SPEAK_ACTION)

            text, action = self.voice.dictate_segment()[:2]
            # Which microphone depends on whether a recogniser is installed,
            # and the test machine may have neither.
            self.assertIn(text.strip(), (
                chr(MICROPHONE_CODEPOINT), chr(MICROPHONE_OFF_CODEPOINT)))
            self.assertEqual(action, self.voice.DICTATE_ACTION)

    def test_disabled_microphone_offers_verified_lazy_model_install(self):
        data = Path(self.tmp.name) / "data"
        with self.settings(
                KILIX_CHROME_DICTATE="1",
                KILIX_VOICE_STT_ENGINE="vosk",
                KILIX_VOICE_STT_MODEL="lgraph-en-us"), \
                mock.patch.dict(os.environ, {
                    "KILIX_DATA_HOME": str(data),
                    "KILIX_HOME": str(ROOT),
                }, clear=False):
            offer = self.voice.dictation_install_offer()

        self.assertIsNotNone(offer)
        self.assertEqual(offer.model, "lgraph-en-us")
        self.assertEqual(offer.size, 130557655)
        self.assertEqual(offer.argv, (
            str(ROOT / "kilix"), "stt", "--install", "lgraph-en-us",
            "--default", "lgraph-en-us"))
        self.assertIn("124.5 MiB", offer.message)
        self.assertIn("Nothing is downloaded unless", offer.message)

    def test_model_install_offer_disappears_when_vosk_assets_are_present(self):
        data = Path(self.tmp.name) / "data"
        model = data / "voice" / "models" / "small-en-us"
        model.mkdir(parents=True)
        library = data / "voice" / "lib" / "current" / "libvosk.so"
        library.parent.mkdir(parents=True)
        library.write_bytes(b"fixture")
        with self.settings(
                KILIX_VOICE_STT_ENGINE="vosk",
                KILIX_VOICE_STT_MODEL="small-en-us"), \
                mock.patch.dict(os.environ, {
                    "KILIX_DATA_HOME": str(data),
                    "KILIX_HOME": str(ROOT),
                }, clear=False):
            self.assertIsNotNone(self.voice.dictation_install_offer())
            for relative in self.voice.STT_MODEL_REQUIRED_FILES["small-en-us"]:
                target = model / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"fixture")
            self.voice._AVAILABILITY_UNTIL = float("inf")
            self.assertIsNone(self.voice.dictation_install_offer())
            self.assertEqual(self.voice._AVAILABILITY_UNTIL, 0.0)
            # Dictation also needs a capture tool; supply one rather than
            # depend on the host having PulseAudio or ALSA utilities.
            real_which = self.voice.which
            with mock.patch.object(
                    self.voice, "which",
                    lambda name: f"/usr/bin/{name}" if name == "parec"
                    else real_which(name)):
                self.assertTrue(
                    self.voice._stt_available("vosk", "small-en-us"))

    def test_vibevoice_dictates_once_its_runtime_is_built(self):
        data = Path(self.tmp.name) / "data"
        with self.settings(
                KILIX_VOICE_STT_ENGINE="vibevoice",
                KILIX_VOICE_STT_MODEL="vibevoice-asr-bitnet"), \
                mock.patch.dict(os.environ, {
                    "KILIX_DATA_HOME": str(data),
                    "KILIX_HOME": str(ROOT),
                }, clear=False):
            os.environ.pop("KILIX_VOICE_VIBEASR", None)
            offer = self.voice.dictation_install_offer()
            self.assertIsNotNone(offer)
            self.assertIn("vibevoice-asr-bitnet", offer.argv)
            self.assertIn("model and VibeASR runtime", offer.message)
            model = data / "voice" / "models" / "vibevoice-asr-bitnet"
            for relative in self.voice.STT_MODEL_REQUIRED_FILES[
                    "vibevoice-asr-bitnet"]:
                target = model / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"fixture")
            # Weights alone: the offer now builds only the runtime.
            offer = self.voice.dictation_install_offer()
            self.assertIsNotNone(offer)
            self.assertIn("its VibeASR runtime is not installed", offer.message)
            self.assertIn("Build the VibeASR runtime", offer.message)
            runtime = data / "voice" / "vibeasr" / "current" / "bin" / "asr_infer"
            runtime.parent.mkdir(parents=True)
            runtime.write_text("#!/bin/sh\n")
            runtime.chmod(0o755)
            self.assertIsNone(self.voice.dictation_install_offer())
            real_which = self.voice.which
            with mock.patch.object(
                    self.voice, "which",
                    lambda name: f"/usr/bin/{name}" if name == "parec"
                    else real_which(name)):
                self.assertTrue(self.voice._stt_available(
                    "vibevoice", "vibevoice-asr-bitnet"))
                runtime.chmod(0o644)       # present but not executable
                self.assertFalse(self.voice._stt_available(
                    "vibevoice", "vibevoice-asr-bitnet"))

    def test_whistle_selection_checks_the_content_model_and_native_library(self):
        data = Path(self.tmp.name) / "whistle-data"
        with self.settings(KILIX_VOICE_STT_ENGINE="whistle"), \
                mock.patch.dict(os.environ, {
                    "KILIX_DATA_HOME": str(data), "KILIX_HOME": str(ROOT),
                    "KILIX_VOICE_WHISTLE_LIBRARY": ""}), \
                mock.patch.object(self.voice, "which", return_value="/usr/bin/parec"):
            self.assertEqual((self.voice.stt_engine(), self.voice.stt_model()), ("whistle", "whistle"))
            self.assertEqual(self.voice.dictation_install_offer().model, "whistle")
            model = data / "desktop-apps/assets/whistle/whistle.cact"
            model.parent.mkdir(parents=True); model.write_bytes(b"weights")
            self.assertFalse(self.voice._stt_available("whistle", "whistle"))
            library = data / "voice/whistle/current/libneedle3.so"
            library.parent.mkdir(parents=True); library.write_bytes(b"library")
            self.assertTrue(self.voice._stt_available("whistle", "whistle"))
            self.assertIsNone(self.voice.dictation_install_offer())
            model.write_bytes(b"")
            self.assertFalse(self.voice._stt_available("whistle", "whistle"))

    def test_whisper_dictates_from_the_content_store_once_its_runtime_is_installed(self):
        # A Whisper default read as vosk/small-en-us before the chrome knew
        # whisper: the button checked, and offered to fetch, the wrong model.
        data = Path(self.tmp.name) / "data"
        with self.settings(
                KILIX_VOICE_STT_ENGINE="whisper",
                KILIX_VOICE_STT_MODEL="whisper-small-en"), \
                mock.patch.dict(os.environ, {
                    "KILIX_DATA_HOME": str(data),
                    "KILIX_HOME": str(ROOT),
                }, clear=False):
            os.environ.pop("KILIX_VOICE_WHISPER", None)
            self.assertEqual((self.voice.stt_engine(), self.voice.stt_model()),
                             ("whisper", "whisper-small-en"))
            offer = self.voice.dictation_install_offer()
            self.assertIsNotNone(offer)
            self.assertEqual(offer.argv[1:], ("stt", "--install", "whisper-small-en",
                                              "--default", "whisper-small-en"))
            self.assertIn("model and Whisper runtime", offer.message)
            # `kilix models install` puts the weights in the content store.
            model = data / "desktop-apps" / "assets" / "faster-whisper-small-en"
            for relative in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
                target = model / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"fixture")
            offer = self.voice.dictation_install_offer()
            self.assertIsNotNone(offer)
            self.assertIn("its Whisper runtime is not installed", offer.message)
            runtime = data / "voice" / "whisper" / "current" / "bin" / "kilix-whisper-stt"
            runtime.parent.mkdir(parents=True)
            runtime.write_text("#!/bin/sh\n")
            runtime.chmod(0o755)
            self.assertIsNone(self.voice.dictation_install_offer())
            real_which = self.voice.which
            with mock.patch.object(
                    self.voice, "which",
                    lambda name: f"/usr/bin/{name}" if name == "parec"
                    else real_which(name)):
                self.assertTrue(self.voice._stt_available("whisper", "whisper-small-en"))
                runtime.chmod(0o644)       # present but not executable
                self.assertFalse(self.voice._stt_available("whisper", "whisper-small-en"))
                runtime.chmod(0o755)
                # A copy under voice/models is the one kilix-voice loads; an
                # incomplete copy there is not rescued by the content store.
                (data / "voice" / "models" / "whisper-small-en").mkdir(parents=True)
                self.assertFalse(self.voice._stt_available("whisper", "whisper-small-en"))

    def test_a_piper_default_reads_aloud_through_the_piper_provider(self):
        # Kristin is the default voice, and the chrome fell back to espeak for
        # an engine it did not know; status then reported the wrong voice.
        data = Path(self.tmp.name) / "data"
        players = {"pacat"}
        real_which = self.voice.which
        with self.settings(KILIX_VOICE_TTS_ENGINE="piper",
                           KILIX_VOICE_TTS_VOICE="en_US-kristin-medium"), \
                mock.patch.dict(os.environ, {"KILIX_DATA_HOME": str(data)}, clear=False), \
                mock.patch.object(self.voice, "which",
                                  lambda name: f"/usr/bin/{name}" if name in players
                                  else (None if name in ("kilix-piper-tts", "pacat", "paplay", "aplay")
                                        else real_which(name))):
            os.environ.pop("KILIX_PIPER_TTS", None)
            self.assertEqual(self.voice.tts_engine(), "piper")
            self.assertFalse(self.voice._tts_available("piper"))
            provider = data / "voice" / "piper" / "current" / "bin" / "kilix-piper-tts"
            provider.parent.mkdir(parents=True)
            provider.write_text("#!/bin/sh\n")
            provider.chmod(0o755)
            self.assertTrue(self.voice._tts_available("piper"))
            players.clear()           # nothing to play it on
            self.assertFalse(self.voice._tts_available("piper"))

    def test_the_chrome_and_the_sdk_share_one_speech_vocabulary(self):
        # The chrome re-reads these rather than importing them; a divergence
        # is a silent fallback to vosk, not a crash.
        sys.path.insert(0, str(ROOT / "config"))
        self.addCleanup(sys.path.remove, str(ROOT / "config"))
        from kilix_sdk import settings as sdk
        self.assertEqual(self.voice.TTS_ENGINES, sdk.VOICE_TTS_ENGINE_CHOICES)
        self.assertEqual(self.voice.STT_ENGINES, sdk.VOICE_STT_ENGINE_CHOICES)
        self.assertEqual(self.voice.STT_MODELS, sdk.VOICE_STT_MODEL_CHOICES)
        self.assertEqual(self.voice.STT_MODEL_ENGINES, sdk.VOICE_STT_MODEL_ENGINES)
        self.assertEqual(set(self.voice.STT_MODEL_BYTES), set(sdk.VOICE_STT_MODEL_CHOICES))
        self.assertEqual(set(self.voice.STT_MODEL_REQUIRED_FILES), set(sdk.VOICE_STT_MODEL_CHOICES))

    def test_choosing_the_vibevoice_engine_selects_its_model(self):
        with self.settings(KILIX_VOICE_STT_ENGINE="vibevoice",
                           KILIX_VOICE_STT_MODEL="small-en-us"):
            self.assertEqual(self.voice.stt_model(), "vibevoice-asr-bitnet")
        with self.settings(KILIX_VOICE_STT_ENGINE="vosk",
                           KILIX_VOICE_STT_MODEL="small-en-us"):
            self.assertEqual(self.voice.stt_model(), "small-en-us")

    def test_confirmed_lazy_install_opens_a_held_visible_overlay(self):
        launch = mock.Mock()
        tab = types.SimpleNamespace(new_window=launch)
        window = types.SimpleNamespace(
            id=41, destroyed=False, tabref=lambda: tab)
        boss = types.SimpleNamespace(
            window_id_map={41: window}, show_error=mock.Mock())
        fast_data_types = sys.modules[
            f"{self.voice.__package__}.fast_data_types"]
        argv = (str(ROOT / "kilix"), "stt", "--install", "small-en-us")

        with mock.patch.object(
                fast_data_types, "get_boss", return_value=boss, create=True):
            self.voice.launch_model_install(
                True, 41, argv, "small-en-us")

        launch.assert_called_once()
        options = launch.call_args.kwargs
        self.assertEqual(options["cmd"], list(argv))
        self.assertEqual(options["overlay_for"], 41)
        self.assertTrue(options["hold"])

    def test_dictation_dispatch_confirms_lazy_install_before_listening(self):
        block = _dictation_toggle_source()
        offer = block.index("dictation_install_offer()")
        begin = block.index("begin_dictation(target.id)")
        self.assertLess(offer, begin)
        self.assertIn("boss.confirm(", block)
        self.assertIn("launch_model_install", block)
        self.assertIn("title='Install speech model?'", block)

    def test_one_toggle_does_not_hide_the_other_widget(self):
        with self.settings(KILIX_CHROME_SPEAK="0", KILIX_CHROME_DICTATE="1"):
            self.assertIsNone(self.voice.speak_segment())
            self.assertIsNotNone(self.voice.dictate_segment())
        with self.settings(KILIX_CHROME_SPEAK="1", KILIX_CHROME_DICTATE="0"):
            self.assertIsNotNone(self.voice.speak_segment())
            self.assertIsNone(self.voice.dictate_segment())

    def test_dictated_text_cannot_submit_itself(self):
        # "Dictation never submits" is a property of this function: a newline
        # is stripped like every other control character, so no policy has to
        # be remembered at the injection site.
        sanitize = self.voice.sanitize_for_injection
        self.assertEqual(sanitize("rm -rf /\n"), "rm -rf /")
        self.assertEqual(sanitize("git\r\nstatus"), "gitstatus")
        self.assertEqual(sanitize("say \x1b[31mred\x1b[0m"), "say [31mred[0m")
        self.assertEqual(sanitize("bell\x07 del\x7f"), "bell del")
        self.assertEqual(sanitize("eight \x9bbit"), "eight bit")
        self.assertEqual(sanitize("  keep inner  spaces  "), "keep inner  spaces")
        self.assertEqual(sanitize("café naïve"), "café naïve")

    def test_daemon_resolution_has_working_installed_development_and_cli_paths(self):
        with mock.patch.object(self.voice, "which", return_value="/opt/bin/kilix-voiced"):
            self.assertEqual(
                self.voice.voice_daemon_target(),
                (["/opt/bin/kilix-voiced"], None),
            )

        project = Path(self.tmp.name) / "sources" / "kilix-apps" / "kilix-voice"
        project.mkdir(parents=True)
        development_daemon = project / "kilix-voiced"
        development_daemon.write_text("#!/bin/sh\n")
        development_daemon.chmod(0o755)
        with mock.patch.object(self.voice, "which", return_value=None), \
                mock.patch.dict(os.environ, {
                    "GPU_TERMINAL_SOURCE_HOME": str(Path(self.tmp.name) / "sources"),
                    "KILIX_HOME": str(Path(self.tmp.name) / "kilix"),
                }, clear=False):
            self.assertEqual(
                self.voice.voice_daemon_target(),
                ([str(development_daemon)], str(project)),
            )

        development_daemon.unlink()
        kilix_home = Path(self.tmp.name) / "kilix"
        kilix_home.mkdir()
        kilix = kilix_home / "kilix"
        kilix.write_text("#!/bin/sh\n")
        kilix.chmod(0o755)
        with mock.patch.object(self.voice, "which", return_value=None), \
                mock.patch.dict(os.environ, {
                    "GPU_TERMINAL_SOURCE_HOME": str(Path(self.tmp.name) / "sources"),
                    "KILIX_HOME": str(kilix_home),
                }, clear=False):
            self.assertEqual(
                self.voice.voice_daemon_target(),
                ([str(kilix), "voice", "daemon"], None),
            )

    def test_pixel_panes_are_identified_without_hiding_text_panes(self):
        def pane(image_count, text):
            screen = types.SimpleNamespace(
                grman=types.SimpleNamespace(image_count=image_count))
            return types.SimpleNamespace(screen=screen, as_text=lambda: text)

        self.assertTrue(self.voice.is_pixel_pane(pane(1, "")))
        self.assertFalse(self.voice.is_pixel_pane(pane(1, "terminal text")))
        self.assertFalse(self.voice.is_pixel_pane(pane(0, "")))

    def test_dictation_dispatch_refuses_a_pixel_pane_with_visible_guidance(self):
        block = _dictation_toggle_source()
        self.assertIn("if is_pixel_pane(target):", block)
        self.assertIn("'Dictation unavailable'", block)
        self.assertIn("Voice input works '", block)
        self.assertIn("'in terminal panes.'", block)

    def test_dictation_dispatch_keeps_the_hidden_prompt_guard(self):
        # The click-time check must stay, and must be the canonical-mode test:
        # a name drift back to a bare echo check would refuse dictation at
        # every readline prompt again.
        block = _dictation_toggle_source()
        self.assertIn("elif pane_at_hidden_prompt(target):", block)
        self.assertIn("'Dictation refused'", block)

    def _pane_with_tty(self, *, echo, icanon):
        """A pane stub over a real pty whose slave has the given line flags."""
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        attrs = termios.tcgetattr(slave)
        for flag, wanted in ((termios.ECHO, echo), (termios.ICANON, icanon)):
            attrs[3] = attrs[3] | flag if wanted else attrs[3] & ~flag
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
        return types.SimpleNamespace(
            child=types.SimpleNamespace(child_fd=master))

    def test_hidden_prompt_means_canonical_mode_with_echo_off(self):
        # A password prompt (getpass, `read -s`, sudo, an ssh passphrase)
        # leaves the tty canonical and turns echo off: the kernel reads a line
        # it never shows. That is the one refused state.
        guard = self.voice.pane_at_hidden_prompt
        self.assertTrue(self._is_hidden(guard, echo=False, icanon=True))
        # A readline-style shell prompt turns echo off too, but in raw mode,
        # because the line editor echoes for itself. Refusing that refused
        # dictation at every ordinary bash prompt (the 0.1.7 review failure).
        self.assertFalse(self._is_hidden(guard, echo=False, icanon=False))
        # Cooked, echoing ttys and raw-but-echoing ttys are not hidden.
        self.assertFalse(self._is_hidden(guard, echo=True, icanon=True))
        self.assertFalse(self._is_hidden(guard, echo=True, icanon=False))

    def _is_hidden(self, guard, *, echo, icanon):
        return guard(self._pane_with_tty(echo=echo, icanon=icanon))

    def test_delivery_types_into_a_readline_prompt(self):
        # End to end through deliver_dictation with the real guard over a real
        # pty: the state every interactive shell prompt is actually in must
        # receive the transcript.
        tty_pane = self._pane_with_tty(echo=False, icanon=False)
        write_to_child = mock.Mock()
        window = types.SimpleNamespace(
            destroyed=False,
            child=tty_pane.child,
            screen=types.SimpleNamespace(
                grman=types.SimpleNamespace(image_count=0),
                in_bracketed_paste_mode=False,
            ),
            as_text=lambda: "$ ",
            write_to_child=write_to_child,
        )
        boss = types.SimpleNamespace(window_id_map={77: window})
        fast_data_types = sys.modules[
            f"{self.voice.__package__}.fast_data_types"]
        self.voice.voice_state.target_window_id = 77

        with mock.patch.object(
                fast_data_types, "get_boss", return_value=boss, create=True), \
                mock.patch.object(self.voice, "report_async_error") as error:
            self.voice.deliver_dictation("echo hello")

        error.assert_not_called()
        write_to_child.assert_called_once_with("echo hello")

    def test_delivery_still_refuses_a_real_password_prompt(self):
        # The protective half, with the real guard over a real pty: canonical
        # mode with echo off is refused and the transcript is discarded.
        tty_pane = self._pane_with_tty(echo=False, icanon=True)
        write_to_child = mock.Mock()
        window = types.SimpleNamespace(
            destroyed=False,
            child=tty_pane.child,
            screen=types.SimpleNamespace(
                grman=types.SimpleNamespace(image_count=0),
                in_bracketed_paste_mode=False,
            ),
            as_text=lambda: "Password: ",
            write_to_child=write_to_child,
        )
        boss = types.SimpleNamespace(window_id_map={78: window})
        fast_data_types = sys.modules[
            f"{self.voice.__package__}.fast_data_types"]
        self.voice.voice_state.target_window_id = 78

        with mock.patch.object(
                fast_data_types, "get_boss", return_value=boss, create=True), \
                mock.patch.object(self.voice, "report_async_error") as error:
            self.voice.deliver_dictation("hunter2")

        write_to_child.assert_not_called()
        error.assert_called_once_with(
            "Dictation refused",
            "The pane reached a hidden prompt while Kilix was listening; the "
            "transcript was discarded.",
        )

    def test_dictation_delivery_discards_text_if_target_became_a_pixel_pane(self):
        write_to_child = mock.Mock()
        window = types.SimpleNamespace(
            destroyed=False,
            screen=types.SimpleNamespace(
                grman=types.SimpleNamespace(image_count=1),
                in_bracketed_paste_mode=False,
            ),
            as_text=lambda: "",
            write_to_child=write_to_child,
        )
        boss = types.SimpleNamespace(window_id_map={73: window})
        fast_data_types = sys.modules[
            f"{self.voice.__package__}.fast_data_types"]
        self.voice.voice_state.target_window_id = 73

        with mock.patch.object(
                fast_data_types, "get_boss", return_value=boss, create=True), \
                mock.patch.object(self.voice, "report_async_error") as error:
            self.voice.deliver_dictation("this must remain invisible")

        write_to_child.assert_not_called()
        error.assert_called_once_with(
            "Dictation unavailable",
            "The pane switched to pixel output while Kilix was listening; the "
            "transcript was discarded. Voice input works in terminal panes.",
        )

    def test_dictation_delivery_visibly_discards_text_at_a_new_hidden_prompt(self):
        write_to_child = mock.Mock()
        window = types.SimpleNamespace(
            destroyed=False,
            screen=types.SimpleNamespace(
                grman=types.SimpleNamespace(image_count=0),
                in_bracketed_paste_mode=False,
            ),
            as_text=lambda: "password:",
            write_to_child=write_to_child,
        )
        boss = types.SimpleNamespace(window_id_map={74: window})
        fast_data_types = sys.modules[
            f"{self.voice.__package__}.fast_data_types"]
        self.voice.voice_state.target_window_id = 74

        with mock.patch.object(
                fast_data_types, "get_boss", return_value=boss, create=True), \
                mock.patch.object(self.voice, "pane_at_hidden_prompt", return_value=True), \
                mock.patch.object(self.voice, "report_async_error") as error:
            self.voice.deliver_dictation("do not insert this")

        write_to_child.assert_not_called()
        error.assert_called_once_with(
            "Dictation refused",
            "The pane reached a hidden prompt while Kilix was listening; the "
            "transcript was discarded.",
        )

    def test_session_voice_symlink_is_refused_before_connect_or_chmod(self):
        session = Path(self.tmp.name) / "session"
        session.mkdir()
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        outside.chmod(0o755)
        (session / "voice").symlink_to(outside, target_is_directory=True)

        with mock.patch.dict(
                os.environ, {"KILIX_SESSION_HOME": str(session)}, clear=False), \
                self.assertRaisesRegex(OSError, "unsafe voice session directory"):
            self.voice._ensure_session_voice_dir()
        with mock.patch.dict(
                os.environ, {"KILIX_SESSION_HOME": str(session)}, clear=False), \
                mock.patch.object(self.voice.socket, "socket") as socket_constructor:
            self.assertIsNone(self.voice._connect_control())
        socket_constructor.assert_not_called()
        self.assertEqual(outside.stat().st_mode & 0o777, 0o755)
        self.assertEqual(list(outside.iterdir()), [])

    def test_dictation_request_uses_the_daemon_protocol_without_ignored_fields(self):
        session = Path(self.tmp.name) / "protocol-session"
        with mock.patch.dict(
                os.environ, {"KILIX_SESSION_HOME": str(session)}, clear=False), \
                mock.patch.object(self.voice, "_availability", return_value={
                    "speak": False, "dictate": True}), \
                mock.patch.object(
                    self.voice, "send_control", return_value={"ok": True}) as control, \
                mock.patch.object(self.voice, "ensure_voice_timer"), \
                mock.patch.object(self.voice, "_invalidate"):
            self.assertIsNone(self.voice.begin_dictation(75))
            request = control.call_args.args[0]
            self.assertEqual(set(request), {"op", "sock"})
            self.assertEqual(request["op"], "dictate")
            self.voice._finish_dictation()

    def test_lost_dictation_ack_stops_a_possibly_open_microphone(self):
        session = Path(self.tmp.name) / "lost-ack-session"
        with mock.patch.dict(
                os.environ, {"KILIX_SESSION_HOME": str(session)}, clear=False), \
                mock.patch.object(self.voice, "_availability", return_value={
                    "speak": False, "dictate": True}), \
                mock.patch.object(
                    self.voice, "send_control", side_effect=(None, {"ok": True})) as control, \
                mock.patch.object(self.voice, "_invalidate"):
            error = self.voice.begin_dictation(76)

        self.assertIn("could not be reached", error)
        self.assertEqual(control.call_args_list[0].args[0]["op"], "dictate")
        self.assertEqual(
            control.call_args_list[1],
            mock.call({"op": "stop-dictation"}, spawn=False),
        )
        self.assertIsNone(self.voice.voice_state.dictation_socket)

    def test_lost_dictation_and_stop_acks_retain_socket_until_bounded_cleanup(self):
        session = Path(self.tmp.name) / "lost-both-acks-session"
        with mock.patch.dict(
                os.environ, {"KILIX_SESSION_HOME": str(session)}, clear=False), \
                mock.patch.object(self.voice, "_availability", return_value={
                    "speak": False, "dictate": True}), \
                mock.patch.object(self.voice, "send_control", side_effect=(None, None)), \
                mock.patch.object(self.voice, "ensure_voice_timer"), \
                mock.patch.object(self.voice, "_invalidate"):
            error = self.voice.begin_dictation(77)

        self.assertIn("listening indicator will remain active", error)
        self.assertTrue(self.voice.voice_state.listening)
        self.assertTrue(self.voice.voice_state.stopping)
        self.assertIsNotNone(self.voice.voice_state.dictation_socket)

        self.voice.voice_state.stop_deadline = 0.0
        self.voice.voice_state.stop_limit = 1.0
        with mock.patch.object(self.voice, "send_control", return_value=None) as stop, \
                mock.patch.object(self.voice, "_invalidate"), \
                mock.patch.object(self.voice, "report_async_error") as warning:
            self.voice.poll_dictation()

        self.assertFalse(self.voice.voice_state.listening)
        self.assertIsNone(self.voice.voice_state.dictation_socket)
        stop.assert_called_once_with({"op": "stop-dictation"}, spawn=False)
        warning.assert_called_once()
        self.assertIn("configured recording limit", warning.call_args.args[1])

    def test_second_click_waits_for_final_instead_of_injecting_stale_partial(self):
        class ReturnSocket:
            def __init__(self):
                self.closed = False

            def recv(self, _maximum):
                return json.dumps({"final": "authoritative final"}).encode()

            def close(self):
                self.closed = True

        returned = ReturnSocket()
        self.voice.voice_state.listening = True
        self.voice.voice_state.partial = "stale partial"
        self.voice.voice_state.dictation_socket = returned
        with mock.patch.object(
                self.voice, "send_control", return_value={"ok": True, "stopped": True}) as control, \
                mock.patch.object(self.voice, "deliver_dictation") as deliver, \
                mock.patch.object(self.voice, "ensure_voice_timer"), \
                mock.patch.object(self.voice, "_invalidate"):
            self.voice.end_dictation(flush=True)
            self.assertTrue(self.voice.voice_state.listening)
            self.assertTrue(self.voice.voice_state.stopping)
            self.assertTrue(self.voice.voice_state.stop_confirmed)
            self.assertFalse(returned.closed)
            deliver.assert_not_called()
            control.assert_called_once_with({"op": "stop-dictation"}, spawn=False)

            self.voice.poll_dictation()

        deliver.assert_called_once_with("authoritative final")
        self.assertFalse(self.voice.voice_state.listening)
        self.assertFalse(self.voice.voice_state.stopping)
        self.assertTrue(returned.closed)

    def test_dictation_async_error_timeout_and_empty_final_are_visible(self):
        class ReturnSocket:
            def __init__(self, result):
                self.result = result
                self.closed = False

            def recv(self, _maximum):
                if isinstance(self.result, BaseException):
                    raise self.result
                if self.result is None:
                    raise BlockingIOError
                return json.dumps(self.result).encode()

            def close(self):
                self.closed = True

        cases = (
            (OSError("microphone disappeared"), "Dictation failed",
             "microphone disappeared", False),
            ({"error": "model refused audio"}, "Dictation failed",
             "model refused audio", False),
            ({"final": ""}, "Dictation finished",
             "No speech was recognized", False),
        )
        for result, title, fragment, stopping in cases:
            with self.subTest(result=result):
                returned = ReturnSocket(result)
                self.voice.voice_state = self.voice.VoiceState(
                    listening=True,
                    stopping=stopping,
                    stop_deadline=0.0,
                    dictation_socket=returned,
                )
                with mock.patch.object(
                        self.voice, "report_async_error") as error, \
                        mock.patch.object(self.voice, "send_control") as control, \
                        mock.patch.object(self.voice, "_invalidate"):
                    self.voice.poll_dictation()
                self.assertFalse(self.voice.voice_state.listening)
                self.assertTrue(returned.closed)
                error.assert_called_once()
                self.assertEqual(error.call_args.args[0], title)
                self.assertIn(fragment, error.call_args.args[1])
                if isinstance(result, OSError):
                    control.assert_called_once_with(
                        {"op": "stop-dictation"}, spawn=False)
                else:
                    control.assert_not_called()

    def test_ambiguous_explicit_speech_stop_keeps_truthful_active_state(self):
        self.voice.voice_state.speaking = True
        with mock.patch.object(
                self.voice, "send_control", side_effect=(None, None)) as control, \
                mock.patch.object(self.voice, "ensure_voice_timer"), \
                mock.patch.object(self.voice, "report_async_error") as error:
            self.voice.stop_speech()

        self.assertTrue(self.voice.voice_state.speaking)
        self.assertEqual(control.call_args_list, [
            mock.call({"op": "stop-speech"}, spawn=False),
            mock.call({"op": "stop-speech"}, spawn=False),
        ])
        error.assert_called_once()
        self.assertIn("leave the speaking indicator active", error.call_args.args[1])

        self.voice.voice_state.next_speech_poll = 0.0
        stopped = {"ok": True, "status": {
            "pid": 91,
            "speaking": False,
            "speech_error": "",
            "speech_error_serial": 0,
        }}
        with mock.patch.object(self.voice, "send_control", return_value=stopped):
            self.voice.poll_speech_status()
        self.assertFalse(self.voice.voice_state.speaking)

    def test_ambiguous_dictation_stop_stays_active_until_status_confirms(self):
        class EmptyReturnSocket:
            def __init__(self):
                self.closed = False

            def recv(self, _maximum):
                raise BlockingIOError

            def close(self):
                self.closed = True

        returned = EmptyReturnSocket()
        self.voice.voice_state.listening = True
        self.voice.voice_state.dictation_socket = returned
        with mock.patch.object(
                self.voice, "send_control", side_effect=(None, None)) as control, \
                mock.patch.object(self.voice, "ensure_voice_timer"), \
                mock.patch.object(self.voice, "_invalidate"), \
                mock.patch.object(self.voice, "report_async_error") as uncertain:
            self.voice.end_dictation(flush=True)

        self.assertTrue(self.voice.voice_state.listening)
        self.assertTrue(self.voice.voice_state.stopping)
        self.assertFalse(returned.closed)
        self.assertEqual(control.call_count, 2)
        uncertain.assert_called_once()
        self.assertIn("indicator active", uncertain.call_args.args[1])

        self.voice.voice_state.stop_deadline = 0.0
        confirmed = {"ok": True, "status": {"listening": False}}
        with mock.patch.object(self.voice, "send_control", return_value=confirmed), \
                mock.patch.object(self.voice, "_invalidate"), \
                mock.patch.object(self.voice, "report_async_error"):
            self.voice.poll_dictation()
        self.assertTrue(self.voice.voice_state.listening)
        self.assertTrue(self.voice.voice_state.stop_confirmed)

        self.voice.voice_state.stop_deadline = 0.0
        with mock.patch.object(self.voice, "_invalidate"), \
                mock.patch.object(self.voice, "report_async_error") as missing_final:
            self.voice.poll_dictation()
        self.assertFalse(self.voice.voice_state.listening)
        self.assertTrue(returned.closed)
        missing_final.assert_called_once()
        self.assertIn("microphone is closed", missing_final.call_args.args[1])

    def test_acknowledged_dictation_stop_has_bounded_final_grace(self):
        class EmptyReturnSocket:
            def __init__(self):
                self.closed = False

            def recv(self, _maximum):
                raise BlockingIOError

            def close(self):
                self.closed = True

        returned = EmptyReturnSocket()
        self.voice.voice_state.listening = True
        self.voice.voice_state.dictation_socket = returned
        with mock.patch.object(
                self.voice, "send_control", return_value={"ok": True}) as control, \
                mock.patch.object(self.voice, "ensure_voice_timer"), \
                mock.patch.object(self.voice, "_invalidate"):
            self.voice.end_dictation(flush=True)

        self.assertTrue(self.voice.voice_state.stop_confirmed)
        self.assertTrue(self.voice.voice_state.listening)
        self.voice.voice_state.stop_deadline = 0.0
        with mock.patch.object(self.voice, "_invalidate"), \
                mock.patch.object(self.voice, "report_async_error") as missing_final:
            self.voice.poll_dictation()

        self.assertFalse(self.voice.voice_state.listening)
        self.assertTrue(returned.closed)
        control.assert_called_once_with({"op": "stop-dictation"}, spawn=False)
        missing_final.assert_called_once()
        self.assertIn("microphone is closed", missing_final.call_args.args[1])

    def test_read_aloud_dispatch_refuses_a_pixel_pane_with_visible_guidance(self):
        source = FORK_TABS.read_text()
        block = source.split("elif tab_action == SPEAK_ACTION:", 1)[1].split(
            "elif tab_action == DICTATE_ACTION:", 1)[0]
        self.assertIn("if is_pixel_pane(target):", block)
        self.assertIn("'Read aloud unavailable'", block)
        self.assertIn("'nothing to read. Read aloud works on terminal panes.'", block)

    def test_two_sequential_controls_use_two_one_request_connections(self):
        class OneRequestSocket:
            def __init__(self, reply):
                self.reply = json.dumps(reply).encode()
                self.sent = []
                self.closed = False

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                self.closed = True

            def settimeout(self, _timeout):
                pass

            def send(self, payload):
                self.sent.append(json.loads(payload))
                return len(payload)

            def recv(self, _maximum):
                return self.reply

        first = OneRequestSocket({"ok": True, "id": 1})
        second = OneRequestSocket({"ok": True, "id": 2})
        with mock.patch.object(
                self.voice, "control_connection", side_effect=[first, second]):
            self.assertTrue(self.voice.send_control({"op": "status", "id": 1})["ok"])
            self.assertTrue(self.voice.send_control({"op": "status", "id": 2})["ok"])

        self.assertEqual(first.sent, [{"op": "status", "id": 1}])
        self.assertEqual(second.sent, [{"op": "status", "id": 2}])
        self.assertTrue(first.closed)
        self.assertTrue(second.closed)

    def test_maximum_non_ascii_speech_fits_one_real_seqpacket(self):
        try:
            sender, receiver = socket.socketpair(
                socket.AF_UNIX, socket.SOCK_SEQPACKET)
        except OSError as error:
            self.skipTest(f"SOCK_SEQPACKET socketpair unavailable: {error}")
        self.addCleanup(receiver.close)
        receiver.send(json.dumps({"ok": True, "chunks": 1}).encode())
        text = "😀" * self.voice._MAX_REQUEST_CHARS

        with mock.patch.object(
                self.voice, "control_connection", return_value=sender):
            reply = self.voice.send_control({"op": "speak", "text": text})

        self.assertTrue(reply["ok"])
        payload = receiver.recv(self.voice._CONTROL_MAX_REQUEST_BYTES)
        self.assertLessEqual(len(payload), self.voice._CONTROL_MAX_REQUEST_BYTES)
        self.assertEqual(json.loads(payload)["text"], text)

    def test_read_aloud_status_poll_reports_completion_and_async_failure_once(self):
        replies = [
            {"ok": True, "chunks": 1},
            {"ok": True, "status": {
                "pid": 42,
                "speaking": False,
                "speech_error": "",
                "speech_error_serial": 0,
            }},
        ]
        with mock.patch.object(self.voice, "_availability", return_value={
                "speak": True, "dictate": False}), \
                mock.patch.object(
                    self.voice, "send_control", side_effect=replies) as control, \
                mock.patch.object(self.voice, "ensure_voice_timer"), \
                mock.patch.object(self.voice, "_invalidate"):
            self.assertIsNone(self.voice.speak("hello"))
            self.assertTrue(self.voice.voice_state.speaking)
            self.voice.poll_speech_status()
        self.assertEqual(
            control.call_args_list[0].args[0], {"op": "speak", "text": "hello"})
        self.assertEqual(control.call_args_list[1].args[0], {"op": "status"})
        self.assertFalse(self.voice.voice_state.speaking)

        self.voice.voice_state.speaking = True
        self.voice.voice_state.next_speech_poll = 0.0
        failed_status = {"ok": True, "status": {
            "pid": 42,
            "speaking": False,
            "speech_error": "speaker process exited with status 1",
            "speech_error_serial": 1,
        }}
        with mock.patch.object(self.voice, "send_control", return_value=failed_status), \
                mock.patch.object(self.voice, "report_async_error") as error:
            self.voice.poll_speech_status()
        self.assertFalse(self.voice.voice_state.speaking)
        error.assert_called_once_with(
            "Read aloud failed", "speaker process exited with status 1")

        # The daemon persists its last failure; its serial makes the dialog
        # edge-triggered rather than appearing on every timer tick.
        self.voice.voice_state.speaking = True
        self.voice.voice_state.next_speech_poll = 0.0
        with mock.patch.object(self.voice, "send_control", return_value=failed_status), \
                mock.patch.object(self.voice, "report_async_error") as repeated:
            self.voice.poll_speech_status()
        repeated.assert_not_called()

        self.voice.voice_state.speaking = True
        self.voice.voice_state.next_speech_poll = 0.0
        with mock.patch.object(
                self.voice, "send_control", side_effect=(None, {"ok": True})) as control, \
                mock.patch.object(self.voice, "report_async_error") as unreachable:
            self.voice.poll_speech_status()
        unreachable.assert_called_once()
        self.assertIn("could not be reached", unreachable.call_args.args[1])
        self.assertEqual(
            control.call_args_list,
            [
                mock.call({"op": "status"}, spawn=False),
                mock.call({"op": "stop-speech"}, spawn=False),
            ],
        )

    def test_lost_speak_ack_silences_a_possibly_accepted_turn(self):
        with mock.patch.object(self.voice, "_availability", return_value={
                "speak": True, "dictate": False}), \
                mock.patch.object(
                    self.voice, "send_control", side_effect=(None, {"ok": True})) as control, \
                mock.patch.object(self.voice, "ensure_voice_timer"), \
                mock.patch.object(self.voice, "_invalidate"):
            error = self.voice.speak("possibly accepted")

        self.assertIn("playback was stopped for safety", error)
        self.assertEqual(
            control.call_args_list,
            [
                mock.call({"op": "speak", "text": "possibly accepted"}),
                mock.call({"op": "stop-speech"}, spawn=False),
            ],
        )
        self.assertFalse(self.voice.voice_state.speaking)

    def test_lost_speak_and_stop_acks_keep_indicator_active(self):
        with mock.patch.object(self.voice, "_availability", return_value={
                "speak": True, "dictate": False}), \
                mock.patch.object(
                    self.voice, "send_control", side_effect=(None, None)) as control, \
                mock.patch.object(self.voice, "ensure_voice_timer"), \
                mock.patch.object(self.voice, "_invalidate"):
            error = self.voice.speak("possibly still audible")

        self.assertTrue(self.voice.voice_state.speaking)
        self.assertTrue(self.voice.voice_state.speech_uncertainty_reported)
        self.assertIn("indicator will remain active", error)
        self.assertEqual(control.call_args_list, [
            mock.call({"op": "speak", "text": "possibly still audible"}),
            mock.call({"op": "stop-speech"}, spawn=False),
        ])

    def test_status_loss_keeps_speaking_active_until_stop_or_status_confirms(self):
        self.voice.voice_state.speaking = True
        self.voice.voice_state.next_speech_poll = 0.0
        with mock.patch.object(
                self.voice, "send_control", side_effect=(None, None)) as control, \
                mock.patch.object(self.voice.time, "monotonic", return_value=100.0), \
                mock.patch.object(self.voice, "ensure_voice_timer"), \
                mock.patch.object(self.voice, "report_async_error") as uncertain:
            self.voice.poll_speech_status()

        self.assertTrue(self.voice.voice_state.speaking)
        self.assertEqual(control.call_args_list, [
            mock.call({"op": "status"}, spawn=False),
            mock.call({"op": "stop-speech"}, spawn=False),
        ])
        self.assertEqual(self.voice.voice_state.next_speech_poll, 105.0)
        uncertain.assert_called_once()
        self.assertIn("indicator active", uncertain.call_args.args[1])

        self.voice.voice_state.next_speech_poll = 0.0
        with mock.patch.object(
                self.voice, "send_control", side_effect=(None, None)), \
                mock.patch.object(self.voice, "ensure_voice_timer"), \
                mock.patch.object(self.voice, "report_async_error") as repeated:
            self.voice.poll_speech_status()
        self.assertTrue(self.voice.voice_state.speaking)
        repeated.assert_not_called()

        self.voice.voice_state.next_speech_poll = 0.0
        confirmed = {"ok": True, "status": {
            "pid": 92,
            "speaking": False,
            "speech_error": "",
            "speech_error_serial": 0,
        }}
        with mock.patch.object(self.voice, "send_control", return_value=confirmed):
            self.voice.poll_speech_status()
        self.assertFalse(self.voice.voice_state.speaking)
        self.assertFalse(self.voice.voice_state.speech_uncertainty_reported)

    def test_speech_status_connections_are_throttled(self):
        self.voice.voice_state.speaking = True
        self.voice.voice_state.next_speech_poll = self.voice.time.monotonic() + 1
        with mock.patch.object(self.voice, "send_control") as control:
            self.voice.poll_speech_status()
        control.assert_not_called()



def _normal_key(key: str) -> str:
    """kitty_mod is ctrl+shift here; modifier order does not matter."""
    parts = key.lower().replace("kitty_mod", "ctrl+shift").split("+")
    return "+".join(sorted(parts[:-1]) + parts[-1:])


class DictationHotkeyTests(unittest.TestCase):
    """Ctrl+Shift+D toggles dictation, and nothing else claims it."""

    def maps(self):
        found = []
        for line in KITTY_CONF.read_text().splitlines():
            fields = line.split()
            if fields[:1] != ["map"]:
                continue
            mode = None
            rest = fields[1:]
            while rest and rest[0].startswith("--"):
                flag = rest.pop(0)
                if flag in ("--mode", "--new-mode", "--on-unknown", "--on-action"):
                    value = rest.pop(0)
                    if flag == "--mode":
                        mode = value
            if rest:
                found.append((mode, _normal_key(rest[0]), " ".join(rest[1:])))
        return found

    def test_ctrl_shift_d_toggles_dictation_and_is_mapped_once(self):
        hotkey = _normal_key("ctrl+shift+d")
        claims = [(mode, action) for mode, key, action in self.maps() if key == hotkey]
        self.assertEqual(claims, [(None, "kilix_dictate")])

    def test_the_tmux_mode_prefix_is_a_different_key(self):
        prefixes = [key for mode, key, action in self.maps() if action == ""]
        self.assertNotIn(_normal_key("ctrl+shift+d"), prefixes)

    @unittest.skipUnless(FORK_VOICE.is_file(), "kitty fork submodule is not checked out")
    def test_the_engine_has_no_default_on_the_hotkey(self):
        definitions = (FORK / "options" / "definition.py").read_text()
        self.assertIsNone(re.search(r"(kitty_mod|ctrl\+shift)\+d['\s]", definitions))

    @unittest.skipUnless(FORK_VOICE.is_file(), "kitty fork submodule is not checked out")
    def test_the_engine_action_and_the_click_share_one_toggle(self):
        boss = (FORK / "boss.py").read_text()
        body = boss[boss.index("def kilix_dictate(self)"):]
        body = body[:body.index("\n    @ac(")]
        self.assertIn("toggle_dictation(", body)
        tabs = (FORK / "tabs.py").read_text()
        self.assertIn("toggle_dictation(self.active_tab.active_window", tabs)
        self.assertNotIn("begin_dictation(", tabs)


@unittest.skipUnless(FORK_VOICE.is_file(), "kitty fork submodule is not checked out")
class ToggleDictationTests(unittest.TestCase):
    """The hotkey and the microphone click run exactly this."""

    @classmethod
    def setUpClass(cls):
        cls.voice = _load_fork_voice()

    def setUp(self):
        self.boss = mock.Mock()
        data = sys.modules[self.voice.__name__.rsplit(".", 1)[0] + ".fast_data_types"]
        for patcher in (
                mock.patch.object(data, "get_boss", lambda: self.boss, create=True),
                mock.patch.object(self.voice, "voice_state", self.voice.VoiceState()),
                mock.patch.object(self.voice, "is_pixel_pane", return_value=False),
                mock.patch.object(self.voice, "pane_at_hidden_prompt", return_value=False),
                mock.patch.object(self.voice, "dictation_install_offer", return_value=None),
                mock.patch.object(self.voice, "begin_dictation", return_value=None),
                mock.patch.object(self.voice, "end_dictation")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.pane = types.SimpleNamespace(id=7)

    def test_starts_dictation_into_the_pane(self):
        self.voice.toggle_dictation(self.pane)
        self.voice.begin_dictation.assert_called_once_with(7)
        self.boss.show_error.assert_not_called()

    def test_a_second_press_stops_and_flushes(self):
        self.voice.voice_state.listening = True
        self.voice.toggle_dictation(self.pane)
        self.voice.end_dictation.assert_called_once_with(flush=True)
        self.voice.begin_dictation.assert_not_called()

    def test_no_pane_does_nothing(self):
        self.voice.toggle_dictation(None)
        self.voice.begin_dictation.assert_not_called()
        self.boss.show_error.assert_not_called()

    def test_refusals_are_shown_and_nothing_starts(self):
        for check, title in (("is_pixel_pane", "Dictation unavailable"),
                             ("pane_at_hidden_prompt", "Dictation refused")):
            with self.subTest(check=check), \
                    mock.patch.object(self.voice, check, return_value=True):
                self.boss.reset_mock()
                self.voice.toggle_dictation(self.pane)
                self.assertEqual(self.boss.show_error.call_args.args[0], title)
        self.voice.begin_dictation.assert_not_called()

    def test_a_missing_model_is_offered_not_started(self):
        offer = types.SimpleNamespace(message="install?", argv=("kilix",), model="m")
        with mock.patch.object(self.voice, "dictation_install_offer", return_value=offer):
            self.voice.toggle_dictation(self.pane)
        self.assertEqual(self.boss.confirm.call_args.args[:5],
                         ("install?", self.voice.launch_model_install, 7, ("kilix",), "m"))
        self.voice.begin_dictation.assert_not_called()

    def test_a_start_failure_is_shown(self):
        with mock.patch.object(self.voice, "begin_dictation", return_value="no mic"):
            self.voice.toggle_dictation(self.pane)
        self.boss.show_error.assert_called_once_with("Dictation unavailable", "no mic")

if __name__ == "__main__":
    unittest.main()
