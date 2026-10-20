"""Behavioral coverage for the Kilix voice command boundary."""

from __future__ import annotations

import json
import os
from pathlib import Path
import pathlib
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

# The suite runs both as `discover -s tests` (bare module names) and as
# `-m unittest tests.<module>` (package), so name this directory explicitly
# rather than relying on either style's import roots.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _env_support import sandbox_env  # noqa: E402



ROOT = Path(__file__).resolve().parents[1]
# The licence authority the voice runtime's gate imports is staged from the
# kilix-license copy the pinned Content component vendors. Content pins that
# predate the vendored copy have no authority to stage, and the installer then
# refuses before it changes anything.
VENDORED_LICENSE_PIN = (
    ROOT / "third_party" / "kilix-content" / "third_party" / "kilix-license.pin")
KILIX = ROOT / "kilix"
VOICE_INSTALLER = ROOT / "scripts" / "install-kilix-voice.sh"


class VoiceCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        storage = self.root / "gpu-terminal" / "kilix"
        self.environment = sandbox_env(**{
            "HOME": str(self.home),
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "GPU_TERMINAL_HOME": str(self.root / "gpu-terminal"),
            "GPU_TERMINAL_SETTINGS_FILE": str(
                self.root / "gpu-terminal" / "settings.conf"),
            "KILIX_STORAGE_HOME": str(storage),
            "KILIX_CONFIG_HOME": str(storage / "config"),
            "KILIX_STATE_DIRECTORY": str(storage / "state"),
            "KILIX_CACHE_HOME": str(storage / "cache"),
            "KILIX_SESSION_HOME": str(storage / "session"),
            "KILIX_DATA_HOME": str(storage / "data"),
            "KILIX_BUILD_DIRECTORY": str(storage / "build"),
            "KILIX_PREBUILT_HOME": str(storage / "prebuilt" / "kitty.app"),
            "KILIX_VOICE_PREFIX": str(self.root / "prefix"),
        })

    def run_kilix(self, *arguments: str, check: bool = True,
                  input_text: str | None = None):
        return subprocess.run(
            [KILIX, *arguments],
            env=self.environment,
            text=True,
            input=input_text,
            capture_output=True,
            check=check,
        )

    def pinned_voice_ref(self):
        refs = subprocess.check_output(
            [VOICE_INSTALLER, "--print-refs"],
            env=self.environment,
            text=True,
        )
        line = refs.splitlines()[0]
        self.assertTrue(line.startswith("kilix-voice="), line)
        return line.removeprefix("kilix-voice=")

    def pinned_license_line(self):
        refs = subprocess.check_output(
            [VOICE_INSTALLER, "--print-refs"],
            env=self.environment,
            text=True,
        )
        lines = [line for line in refs.splitlines()
                 if line.startswith("kilix-license=")]
        self.assertEqual(len(lines), 1, refs)
        return lines[0]

    def install_voice_stamp(self, voice_ref: str | None = None):
        state = Path(self.environment["KILIX_STATE_DIRECTORY"])
        state.mkdir(parents=True, exist_ok=True)
        stamp = state / "kilix-voice-install.refs"
        stamp.write_text(
            f"kilix-voice={voice_ref or self.pinned_voice_ref()}\n"
            "libvosk=skipped\n"
            "model-small-en-us=skipped\n"
        )
        stamp.chmod(0o600)
        return stamp

    def install_runtime_tool(self, name: str, script: str):
        data = Path(self.environment["KILIX_DATA_HOME"]) / "voice"
        generation = data / "runtime" / "generations" / "fixture"
        generation_bin = generation / "bin"
        generation_bin.mkdir(parents=True, exist_ok=True)
        tool = generation_bin / name
        tool.write_text(script)
        tool.chmod(0o755)
        library = generation / "lib" / "kilix-voice"
        library.mkdir(parents=True, exist_ok=True)
        (library / "kilix-license.pin").write_text(
            self.pinned_license_line().removeprefix("kilix-license=") + "\n")

        current = data / "runtime" / "current"
        if not current.exists() and not current.is_symlink():
            current.symlink_to("generations/fixture")
        prefix_bin = Path(self.environment["KILIX_VOICE_PREFIX"]) / "bin"
        prefix_bin.mkdir(parents=True, exist_ok=True)
        entrypoint = prefix_bin / name
        entrypoint.symlink_to(current / "bin" / name)
        self.install_voice_stamp()
        return entrypoint

    def install_fake_daemon(self):
        return self.install_runtime_tool(
            "kilix-voiced",
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = --version ]; then\n"
            "  printf '%s\\n' 'kilix-voiced fixture'\n"
            "  exit 0\n"
            "fi\n"
            "printf '%s\\n' 'voice daemon foreground'\n"
            "printf 'argc=%s\\n' \"$#\"\n"
        )

    def install_fake_stt(self):
        return self.install_runtime_tool(
            "kilix-stt",
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = --version ]; then\n"
            "  printf '%s\\n' 'kilix-stt fixture'\n"
            "  exit 0\n"
            "fi\n"
            "printf 'arg=%s\\n' \"$@\"\n"
        )

    def test_stt_routes_model_catalog_install_and_default_arguments(self):
        self.install_fake_stt()

        result = self.run_kilix(
            "stt", "--install", "lgraph-en-us",
            "--default", "lgraph-en-us",
        )

        self.assertEqual(result.stdout.splitlines(), [
            "arg=--install",
            "arg=lgraph-en-us",
            "arg=--default",
            "arg=lgraph-en-us",
        ])

        help_result = self.run_kilix("stt", "--help")
        self.assertIn("stt --models", help_result.stdout)
        self.assertIn("stt --models --json", help_result.stdout)
        self.assertIn("stt --install MODEL", help_result.stdout)
        self.assertIn("stt --default MODEL", help_result.stdout)

    def fake_pinned_sizer(self, present: bool):
        """A fake git that answers only for the pinned sizer checkout and
        logs every fetch; the sizer executable exists when `present`."""
        installer = ROOT / "scripts" / "install-kilix-tts-sizer.sh"
        self.environment["GPU_TERMINAL_SOURCE_HOME"] = str(self.root / "sources")
        ref = subprocess.check_output([installer, "--print-ref"], text=True).strip()
        ref = ref.removeprefix("kilix-system-monitor=")
        sizer = Path(subprocess.check_output(
            [installer, "--print-path"], env=self.environment, text=True).strip())
        if present:
            sizer.parent.mkdir(parents=True)
            sizer.write_text("#!/bin/sh\nexit 0\n")
            sizer.chmod(0o755)
        log = self.root / "git.log"
        git = self.bin / "git"
        git.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> '{log}'\n"
            'case "$*" in\n'
            f"  *kilix-tts-sizer-*rev-parse\\ HEAD*) echo {ref}; exit 0 ;;\n"
            "  *kilix-tts-sizer-*status*) exit 0 ;;\n"
            "  *fetch*) exit 1 ;;\n"
            "esac\n"
            'exec /usr/bin/git "$@"\n')
        git.chmod(0o755)
        return sizer, log

    def install_sizer_echo_stt(self):
        return self.install_runtime_tool(
            "kilix-stt",
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = --version ]; then\n"
            "  printf '%s\\n' 'kilix-stt fixture'\n"
            "  exit 0\n"
            "fi\n"
            "printf 'sizer=%s\\n' \"${PLEBIAN_MODEL_SIZER:-none}\"\n")

    def test_setup_default_and_recommend_get_the_pinned_sizer(self):
        # First-run setup asks the sizer which dictation model this hardware
        # should get; without it every machine was offered the compact model.
        self.install_sizer_echo_stt()
        sizer, _log = self.fake_pinned_sizer(present=True)
        for arguments in (("--setup-default", "--result", "r.json"), ("--recommend",),
                          ("--models",)):
            with self.subTest(arguments=arguments):
                result = self.run_kilix("stt", *arguments)
                self.assertEqual(result.stdout.strip(), f"sizer={sizer}")

    def test_opening_stt_never_fetches_the_sizer(self):
        self.install_sizer_echo_stt()
        _sizer, log = self.fake_pinned_sizer(present=False)
        result = self.run_kilix("stt", "--models")
        self.assertEqual(result.stdout.strip(), "sizer=none")
        self.assertNotIn("fetch", log.read_text() if log.exists() else "")
        # Setup does fetch it, and fails closed when it cannot.
        setup = self.run_kilix("stt", "--setup-default", check=False)
        self.assertNotEqual(setup.returncode, 0)
        self.assertIn("fetch", log.read_text())
        self.assertNotIn("sizer=", setup.stdout)

    def test_stt_refreshes_a_runtime_from_the_previous_voice_pin(self):
        repo = self.root / "voice-origin"
        repo.mkdir()
        (repo / "Makefile").write_text(
            "install:\n"
            "\tmkdir -p $(PREFIX)/bin\n"
            "\tinstall -m 0755 kilix-tts kilix-stt kilix-voiced "
            "$(PREFIX)/bin/\n"
        )
        fresh_script = (
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = --version ]; then\n"
            "  printf '%s\\n' \"${0##*/} fresh fixture\"\n"
            "  exit 0\n"
            "fi\n"
            "printf '%s\\n' 'runtime=fresh'\n"
            "printf 'arg=%s\\n' \"$@\"\n"
        )
        for name in ("kilix-tts", "kilix-stt", "kilix-voiced"):
            tool = repo / name
            tool.write_text(fresh_script)
            tool.chmod(0o755)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo,
                       check=True)
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            [
                "git", "-c", "user.name=Kilix test",
                "-c", "user.email=kilix-test@example.invalid",
                "commit", "-q", "-m", "fixture",
            ],
            cwd=repo,
            check=True,
        )
        voice_ref = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()
        self.environment.update({
            "KILIX_VOICE_REPO": str(repo),
            "KILIX_VOICE_REF": voice_ref,
        })

        stale_script = (
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = --version ]; then exit 0; fi\n"
            "printf '%s\\n' 'runtime=stale'\n"
            "exit 2\n"
        )
        for name in ("kilix-tts", "kilix-stt", "kilix-voiced"):
            self.install_runtime_tool(name, stale_script)
        stamp = self.install_voice_stamp("0" * 40)
        stale_stamp = stamp.read_text()

        result = self.run_kilix(
            "stt", "--install", "lgraph-en-us",
            "--default", "lgraph-en-us",
            check=VENDORED_LICENSE_PIN.is_file(),
        )

        if not VENDORED_LICENSE_PIN.is_file():
            # A Content pin with no vendored authority: the refresh is refused
            # before anything changes, and the stale runtime is not run either.
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("vendors no kilix-license authority", result.stderr)
            self.assertNotIn("runtime=stale", result.stdout)
            self.assertEqual(stamp.read_text(), stale_stamp)
            return
        self.assertEqual(result.stdout.splitlines(), [
            "runtime=fresh",
            "arg=--install",
            "arg=lgraph-en-us",
            "arg=--default",
            "arg=lgraph-en-us",
        ])
        self.assertIn("installing the pinned voice engine", result.stderr)
        self.assertEqual(
            stamp.read_text().splitlines()[0], f"kilix-voice={voice_ref}")

    def test_a_runtime_staged_from_another_licence_authority_is_not_reused(self):
        # The Voice ref matches, so only the staged authority's pin can tell
        # this runtime is stale. The repository does not exist, so whatever
        # the installer then does, it cannot clone or run anything.
        self.environment["KILIX_VOICE_REPO"] = str(self.root / "no-such-repo")
        self.install_fake_stt()
        pin = (Path(self.environment["KILIX_DATA_HOME"]) / "voice" / "runtime"
               / "generations" / "fixture" / "lib" / "kilix-voice"
               / "kilix-license.pin")
        pin.write_text("f" * 40 + "\n")

        result = self.run_kilix("stt", "--models", check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("arg=--models", result.stdout)

        pin.write_text(
            self.pinned_license_line().removeprefix("kilix-license=") + "\n")
        reused = self.run_kilix("stt", "--models")
        self.assertEqual(reused.stdout.splitlines(), ["arg=--models"])

    def test_opening_stt_only_bootstraps_the_download_free_runtime(self):
        launcher = KILIX.read_text()
        self.assertIn(
            '_kilix_voice_tool kilix-stt "$_stt_force" --without-dictation',
            launcher,
        )

    def test_voice_daemon_executes_a_valid_installed_daemon(self):
        self.install_fake_daemon()

        result = self.run_kilix("voice", "daemon")

        self.assertEqual(
            result.stdout.splitlines(), ["voice daemon foreground", "argc=0"])

    def install_prefix_daemon(self):
        return self.install_runtime_tool(
            "kilix-voiced",
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = --version ]; then\n"
            "  printf '%s\\n' 'kilix-voiced prefix fixture'\n"
            "  exit 0\n"
            "fi\n"
            "printf '%s\\n' 'prefix daemon foreground'\n"
        )

    def hermetic_path(self):
        """A PATH that cannot resolve voice tools installed on this machine."""
        self.environment["PATH"] = f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin"

    def test_voice_daemon_runs_the_installed_prefix_daemon_off_path(self):
        # Desktop launch contexts often lack ~/.local/bin on PATH. An
        # installed prefix daemon must be run, not reinstalled: on 0.1.7 this
        # path re-ran the pinned installer on every daemon start.
        self.hermetic_path()
        daemon = self.install_prefix_daemon()

        result = self.run_kilix("voice", "daemon")

        self.assertEqual(result.stdout.splitlines(), ["prefix daemon foreground"])
        self.assertNotIn("installing", result.stderr)
        self.assertTrue(daemon.exists())

    def test_voice_doctor_reports_the_prefix_daemon_it_would_run(self):
        # Doctor once probed bare PATH and printed "kilix-voiced: not found"
        # directly under "daemon: running". It must report the tool the
        # launch paths resolve, and say when PATH cannot see it.
        self.hermetic_path()
        daemon = self.install_prefix_daemon()

        result = self.run_kilix("voice", "doctor")

        lines = result.stdout.splitlines()
        voiced = [line for line in lines if line.startswith("kilix-voiced:")]
        self.assertEqual(len(voiced), 1)
        self.assertIn(str(daemon), voiced[0])
        self.assertIn("not on PATH", voiced[0])
        self.assertNotIn("not found", voiced[0])
        # A tool absent from PATH and the prefix is named with both places
        # that were checked, so "not installed" is actionable.
        stt = [line for line in lines if line.startswith("kilix-stt:")]
        self.assertEqual(len(stt), 1)
        self.assertIn("not installed", stt[0])
        self.assertIn(str(Path(self.environment["KILIX_VOICE_PREFIX"]) / "bin"),
                      stt[0])

    def _stub_pactl(self, default_sink, default_source=None):
        if default_source is None:
            default_source = default_sink + ".monitor"
        stub = self.bin / "pactl"
        stub.write_text(
            "#!/bin/sh\n"
            "[ \"$1\" = info ] || exit 0\n"
            "printf 'Server Name: pulseaudio\\nServer Version: 17.0\\n'\n"
            f"printf 'Default Source: {default_source}\\nDefault Sink: {default_sink}\\n'\n")
        stub.chmod(0o755)

    def test_voice_doctor_says_so_when_the_only_sink_is_a_null_device(self):
        # On a machine with no audio device PulseAudio serves auto_null and
        # every speak succeeds into nothing. Doctor printed that sink name and
        # still called the setup healthy; it must say what that name means.
        self.hermetic_path()
        self._stub_pactl("auto_null")
        result = self.run_kilix("voice", "doctor")
        lines = result.stdout.splitlines()
        warned = [line for line in lines if line.startswith("audio output: NONE")]
        self.assertEqual(len(warned), 1, result.stdout)
        self.assertIn("auto_null", warned[0])
        self.assertTrue(any("inaudible" in line for line in lines))

    def _configure_devices(self, **keys):
        path = Path(self.environment["GPU_TERMINAL_SETTINGS_FILE"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(f"{k}={v}\n" for k, v in keys.items()))

    def test_explicitly_configured_devices_are_not_blamed_for_a_null_default(self):
        # The operator pointed both channels at real devices; the server's
        # default being null is then a note, not a fault in either channel.
        self.hermetic_path()
        self._stub_pactl("auto_null")
        self._configure_devices(KILIX_VOICE_DEVICE_OUT="alsa_output.pci.analog-stereo",
                                KILIX_VOICE_DEVICE_IN="alsa_input.pci.analog-stereo")
        result = self.run_kilix("voice", "doctor")
        self.assertNotIn("audio output: NONE", result.stdout)
        self.assertNotIn("audio input: MONITOR", result.stdout)
        self.assertIn("explicitly configured devices", result.stdout)

    def test_only_the_channel_on_the_default_is_named(self):
        self.hermetic_path()
        self._stub_pactl("auto_null")
        self._configure_devices(KILIX_VOICE_DEVICE_OUT="alsa_output.pci.analog-stereo")
        result = self.run_kilix("voice", "doctor")
        self.assertIn("audio input: MONITOR", result.stdout)
        self.assertIn("dictate captures monitor audio", result.stdout)
        self.assertNotIn("audio output: NONE", result.stdout)

    def test_a_server_with_no_default_sink_line_is_named_as_such(self):
        # pactl info reachable, but no "Default Sink" line at all -- an empty
        # sink list. That is not "a null device"; say what was observed.
        self.hermetic_path()
        stub = self.bin / "pactl"
        stub.write_text("#!/bin/sh\n[ \"$1\" = info ] || exit 0\n"
                        "printf 'Server Name: pulseaudio\\nServer Version: 17.0\\n'\n")
        stub.chmod(0o755)
        result = self.run_kilix("voice", "doctor")
        self.assertIn("audio output: NONE", result.stdout)
        self.assertIn("reports no default sink", result.stdout)
        self.assertNotIn("unset, a null device", result.stdout)

    def test_the_control_a_real_sink_draws_no_warning(self):
        self.hermetic_path()
        self._stub_pactl("alsa_output.pci-0000_00_1b.0.analog-stereo",
                         "alsa_input.real_microphone")
        result = self.run_kilix("voice", "doctor")
        self.assertNotIn("audio output: NONE", result.stdout)
        self.assertNotIn("audio input: MONITOR", result.stdout)
        self.assertIn("audio server: pulseaudio 17.0", result.stdout)

    def test_a_real_microphone_is_not_blamed_for_a_null_output(self):
        self.hermetic_path()
        self._stub_pactl("auto_null", "alsa_input.real_microphone")
        result = self.run_kilix("voice", "doctor")
        self.assertIn("audio output: NONE", result.stdout)
        self.assertNotIn("audio input: NONE", result.stdout)
        self.assertNotIn("audio input: MONITOR", result.stdout)
        self.assertNotIn("dictate will capture silence", result.stdout)

    def test_monitor_input_is_diagnosed_independently_of_output(self):
        self.hermetic_path()
        for source in ("auto_null.monitor", "alsa_output.real_speakers.monitor"):
            with self.subTest(source=source):
                self._stub_pactl("alsa_output.real_speakers", source)
                result = self.run_kilix("voice", "doctor")
                self.assertNotIn("audio output: NONE", result.stdout)
                self.assertIn("audio input: MONITOR", result.stdout)
                self.assertIn("dictate captures monitor audio", result.stdout)

    def test_missing_input_is_diagnosed_independently_of_output(self):
        self.hermetic_path()
        self._stub_pactl("alsa_output.real_speakers", "")
        result = self.run_kilix("voice", "doctor")
        self.assertNotIn("audio output: NONE", result.stdout)
        self.assertIn("audio input: NONE", result.stdout)
        self.assertIn("reports no default source", result.stdout)

    def test_explicit_null_routes_are_diagnosed_even_with_real_defaults(self):
        self.hermetic_path()
        self._stub_pactl("alsa_output.real_speakers", "alsa_input.real_microphone")
        self._configure_devices(KILIX_VOICE_DEVICE_OUT="auto_null",
                                KILIX_VOICE_DEVICE_IN="auto_null.monitor")
        result = self.run_kilix("voice", "doctor")
        self.assertIn("audio output: NONE", result.stdout)
        self.assertIn("audio input: MONITOR", result.stdout)

    def test_voice_daemon_rejects_arguments_and_is_documented_in_help(self):
        self.install_fake_daemon()
        refused = self.run_kilix("voice", "daemon", "unexpected", check=False)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("usage:", refused.stderr)
        self.assertIn("voice daemon", refused.stderr)

        help_result = self.run_kilix("voice", "help")
        self.assertIn("daemon", help_result.stdout)
        self.assertIn("run the pinned voice daemon", help_result.stdout)

    def test_voice_stop_retries_ambiguity_and_stops_both_channels(self):
        self.install_fake_daemon()
        voice_session = Path(self.environment["KILIX_SESSION_HOME"]) / "voice"
        voice_session.mkdir(parents=True, mode=0o700)
        control_path = voice_session / "control.sock"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        server.bind(str(control_path))
        server.listen(3)
        requests = []
        replies = (
            None,  # lose the first stop-speech reply; the CLI must retry it
            {"ok": True, "id": "", "stopped": True},
            {"ok": True, "id": "", "stopped": True},
        )

        def serve():
            for reply in replies:
                client, _address = server.accept()
                with client:
                    requests.append(json.loads(client.recv(65536)))
                    if reply is not None:
                        client.send(json.dumps(reply).encode())

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        try:
            result = self.run_kilix("voice", "stop")
        finally:
            server.close()
        worker.join(timeout=2)

        self.assertEqual(result.returncode, 0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(requests, [
            {"op": "stop-speech"},
            {"op": "stop-speech"},
            {"op": "stop-dictation"},
        ])

    def test_speak_uses_compact_unicode_packet_and_rejects_oversize_text(self):
        self.install_fake_daemon()
        voice_session = Path(self.environment["KILIX_SESSION_HOME"]) / "voice"
        voice_session.mkdir(parents=True, mode=0o700)
        control_path = voice_session / "control.sock"

        def exchange(text: str, reply: bool):
            server = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            server.bind(str(control_path))
            server.listen(1)
            packets = []

            def serve():
                client, _address = server.accept()
                with client:
                    packets.append(client.recv(256 * 1024))
                    if reply:
                        client.send(json.dumps({
                            "ok": True, "id": "", "chunks": 1,
                        }).encode())

            worker = threading.Thread(target=serve, daemon=True)
            worker.start()
            try:
                result = self.run_kilix(
                    "speak", "-", input_text=text, check=reply)
            finally:
                server.close()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            control_path.unlink(missing_ok=True)
            return result, packets

        text = "😀" * 32768
        spoken, packets = exchange(text, reply=True)
        self.assertEqual(spoken.returncode, 0)
        self.assertLessEqual(len(packets[0]), 192 * 1024)
        self.assertEqual(json.loads(packets[0])["text"], text)

        refused, packets = exchange("😀" * 50000, reply=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(packets, [b""])
        self.assertIn("too large for one local control packet", refused.stderr)

    def test_ambiguous_initial_turns_are_compensated_before_cli_exit(self):
        self.install_fake_daemon()
        voice_session = Path(self.environment["KILIX_SESSION_HOME"]) / "voice"
        voice_session.mkdir(parents=True, mode=0o700)
        control_path = voice_session / "control.sock"

        def run_with_lost_reply(arguments, input_text=None):
            server = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            server.bind(str(control_path))
            server.listen(2)
            requests = []

            def serve():
                first, _address = server.accept()
                with first:
                    requests.append(json.loads(first.recv(65536)))
                    # Close without replying: the turn may already be active.
                compensation, _address = server.accept()
                with compensation:
                    requests.append(json.loads(compensation.recv(65536)))
                    compensation.send(json.dumps({
                        "ok": True, "id": "", "stopped": True,
                    }).encode())

            worker = threading.Thread(target=serve, daemon=True)
            worker.start()
            try:
                result = self.run_kilix(
                    *arguments, input_text=input_text, check=False)
            finally:
                server.close()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            control_path.unlink(missing_ok=True)
            return result, requests

        refused, requests = run_with_lost_reply(("speak", "-"), "hello")
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(requests, [
            {"op": "speak", "text": "hello"},
            {"op": "stop-speech"},
        ])

        refused, requests = run_with_lost_reply(("dictate", "--seconds", "1"))
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(requests[0]["op"], "dictate")
        self.assertEqual(set(requests[0]), {"op", "sock"})
        # The client leaves without reading the words, so the turn it may have
        # started is aborted: a finish would only have them decoded for nobody.
        self.assertEqual(requests[1], {"op": "stop-dictation", "mode": "abort"})
        self.assertFalse(any(voice_session.glob("cli-*.sock")))


    def fake_voice_daemon(self, final_after=None, reply_delay=0.0, stop_reply_delay=0.0,
                          echo_mode=True, on_stop=None, on_dictate=None, drop_dictate_reply=False,
                          error_after=None, refuse_dictate=False, drop_first_abort_reply=False,
                          final_on_accept=None):
        """Answer the control socket as kilix-voiced does, recording each request.

        `dictate` is accepted and every stop acknowledged. With `final_after`,
        the final "slow words" reaches the caller that many seconds after a
        finish-mode stop; `reply_delay` holds the reply to `dictate` and
        `stop_reply_delay` the reply to each stop. `echo_mode=False` answers as
        a kilix-voiced from before stop modes: it finishes every stop and
        echoes no mode. `on_stop` is called with each stop before it is
        answered, and `on_dictate` with the dictate request; with
        `drop_dictate_reply` that request is never answered. With
        `error_after`, the turn ends with an error datagram that many seconds
        after it is accepted, and with `final_on_accept` its text is the final
        straight away. `refuse_dictate` refuses the turn, and
        `drop_first_abort_reply` closes the first abort unanswered. Returns the requests, an event set once a turn is accepted,
        and a close function; `self.received` is set once a `dictate` has
        been read.
        """
        previous = getattr(self, "_close_fake_voice_daemon", None)
        if previous is not None:
            previous()  # a failed subtest must not leave its socket bound
        self.received = threading.Event()
        self.voice_session = Path(self.environment["KILIX_SESSION_HOME"]) / "voice"
        self.voice_session.mkdir(parents=True, mode=0o700, exist_ok=True)
        control_path = self.voice_session / "control.sock"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        server.bind(str(control_path))
        server.listen(4)
        server.settimeout(0.1)
        requests = []
        accepted = threading.Event()
        stopping = threading.Event()

        def send_final(path, event=None):
            out = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            try:
                out.sendto(json.dumps(event or {"final": "slow words"}).encode(), path)
            except OSError:
                pass
            finally:
                out.close()

        def serve():
            receiver = None
            while not stopping.is_set():
                try:
                    client, _address = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    return
                with client:
                    packet = client.recv(65536)
                    if not packet:
                        continue  # connected, then cancelled before asking
                    request = json.loads(packet)
                    requests.append(request)
                    if request["op"] == "dictate":
                        receiver = request["sock"]
                        self.received.set()
                        if on_dictate is not None:
                            on_dictate(request)
                        if drop_dictate_reply:
                            continue  # closed unanswered: the turn may have started
                        if refuse_dictate:
                            client.send(json.dumps({
                                "ok": False, "id": "",
                                "error": "another caller is dictating"}).encode())
                            continue
                        time.sleep(reply_delay)
                        try:
                            client.send(json.dumps({
                                "ok": True, "id": "", "turn": "listen-1"}).encode())
                        except OSError:
                            continue
                        # The client acts on a signal at the same points
                        # whether it lands before or after this reply is read.
                        accepted.set()
                        if final_on_accept is not None:
                            send_final(receiver, {"final": final_on_accept})
                        if error_after is not None:
                            timer = threading.Timer(error_after, send_final, (
                                receiver, {"error": "the recogniser failed"}))
                            timer.daemon = True
                            timer.start()
                        continue
                    mode = request.get("mode", "finish") if echo_mode else "finish"
                    if on_stop is not None:
                        on_stop(request)
                    if (drop_first_abort_reply and mode == "abort"
                            and sum(r.get("mode") == "abort" for r in requests) == 1):
                        continue  # closed unanswered; the client retries
                    time.sleep(stop_reply_delay)
                    reply = {"ok": True, "id": "", "stopped": True}
                    if echo_mode:
                        reply["mode"] = mode
                    try:
                        client.send(json.dumps(reply).encode())
                    except OSError:
                        pass  # the caller gave up on this reply; keep serving
                    if final_after is not None and mode == "finish" and receiver:
                        timer = threading.Timer(final_after, send_final, (receiver,))
                        timer.daemon = True
                        timer.start()

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()

        def close():
            if stopping.is_set():
                return
            stopping.set()
            worker.join(timeout=2)
            server.close()
            control_path.unlink(missing_ok=True)

        self.addCleanup(close)
        self._close_fake_voice_daemon = close
        return requests, accepted, close

    def start_dictate(self, *arguments):
        """`kilix dictate` in its own process group, as a terminal would run it."""
        return subprocess.Popen(
            [KILIX, "dictate", *arguments], env=self.environment, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)

    def test_discard_on_interrupt_aborts_the_turn_at_once(self):
        # Through `kilix ask --voice` Ctrl-C cancels the request, but a
        # finish-mode stop had Whisper decode the words anyway: the recogniser
        # and the microphone lease were held 4.1 s for a transcript ask threw
        # away (live N-STOP check, 2026-10-20).
        self.install_fake_daemon()
        requests, accepted, _close = self.fake_voice_daemon(final_after=0.2)
        proc = self.start_dictate("--seconds", "30", "--discard-on-interrupt")
        self.assertTrue(accepted.wait(10))
        started = time.monotonic()
        os.killpg(proc.pid, signal.SIGINT)
        out, err = proc.communicate(timeout=10)
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertEqual(proc.returncode, 1, err)
        self.assertEqual(out, "")
        self.assertIn("dictation cancelled", err)
        self.assertEqual(requests[1:], [{"op": "stop-dictation", "mode": "abort"}])
        self.assertFalse(any(self.voice_session.glob("cli-*.sock")))

    def test_an_interrupt_without_discard_still_finishes_and_prints(self):
        # Whether Ctrl-C lands while the turn is being confirmed or after,
        # plain dictate finishes and prints what it heard.
        self.install_fake_daemon()
        for phase, reply_delay in (("confirming", 1.0), ("listening", 0.0)):
            with self.subTest(phase):
                requests, accepted, close = self.fake_voice_daemon(
                    final_after=0.2, reply_delay=reply_delay)
                proc = self.start_dictate("--seconds", "30")
                self.assertTrue((self.received if reply_delay else accepted).wait(10))
                os.killpg(proc.pid, signal.SIGINT)
                out, err = proc.communicate(timeout=15)
                self.assertEqual(proc.returncode, 0, err)
                self.assertEqual(out, "slow words\n")
                self.assertEqual(requests[1:], [{"op": "stop-dictation"}])
                close()

    def test_discard_on_interrupt_does_not_apply_to_a_pane(self):
        result = self.run_kilix(
            "dictate", "--pane", "3", "--discard-on-interrupt", check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not apply to --pane", result.stderr)

    def test_a_hangup_or_terminate_aborts_the_turn(self):
        # Closing an "Ask Kilix by voice" tab mid-utterance killed the client
        # without a stop, and the daemon recorded on until the 30 s cap.
        self.install_fake_daemon()
        for sig in (signal.SIGHUP, signal.SIGTERM):
            with self.subTest(sig.name):
                requests, accepted, close = self.fake_voice_daemon()
                proc = self.start_dictate("--seconds", "30")
                self.assertTrue(accepted.wait(10))
                os.killpg(proc.pid, sig)
                out, err = proc.communicate(timeout=10)
                # Silent: the terminal may be gone. The launcher's shell dies
                # of the signal itself; the client's own status is checked in
                # test_a_signal_at_any_boundary_leaves_no_turn_and_no_socket.
                self.assertEqual((out, err), ("", ""))
                self.assertEqual(requests[1:], [{"op": "stop-dictation", "mode": "abort"}])
                self.assertFalse(any(self.voice_session.glob("cli-*.sock")))
                close()

    def test_further_signals_cannot_turn_the_abort_into_a_finish(self):
        # kitty-pty-broker ends a session with SIGTERM to each process in it
        # and again to the group. The second landed while the client waited
        # for its abort to be confirmed, and the stop it then left behind was
        # a finish: the words were decoded for 4 s after the pane closed. A
        # Ctrl-C after a cancel did the same. The fake sends the later signals
        # while the abort is being exchanged.
        self.install_fake_daemon()
        cases = (
            (signal.SIGTERM, (), (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)),
            (signal.SIGINT, ("--discard-on-interrupt",), (signal.SIGINT, signal.SIGTERM)),
        )
        for first, extra, later in cases:
            with self.subTest(first.name):
                group = []

                def again(_request):
                    for sig in later:
                        os.killpg(group[0], sig)

                requests, accepted, close = self.fake_voice_daemon(on_stop=again)
                proc = self.start_dictate("--seconds", "30", *extra)
                group.append(proc.pid)
                self.assertTrue(accepted.wait(10))
                os.killpg(proc.pid, first)
                out, err = proc.communicate(timeout=10)
                self.assertEqual(out, "")
                self.assertNotIn("Traceback", err)
                self.assertEqual(requests[1:], [{"op": "stop-dictation", "mode": "abort"}])
                self.assertFalse(any(self.voice_session.glob("cli-*.sock")))
                close()

    # Runs the launcher's voice client and sends itself each signal at a named
    # point: `binding`, before any turn is asked for; `connecting`, entering
    # the wait for a daemon to start; `connected`, as that wait returns;
    # `acknowledged`, the moment the dictate request is answered; `removing`,
    # as the receiver goes; `cancelling`, entering the abort; `aborting`,
    # inside the abort exchange; `cleanup`, closing the receiver once the final
    # is in; `printing`, as the words are printed. `A+B` sends both at once.
    # `STALL1.5` in place of a signal holds the client 1.5 s there, as a
    # loaded host would.
    BOUNDARY_CHILD = r"""
import os, signal, socket, sys, time
launcher, session, steps, discard, seconds = sys.argv[1:6]
source = open(launcher).read().split("_kilix_voice_client() {\n  cat <<'PY'\n", 1)[1]
source = source.split("\noperation = sys.argv[1]", 1)[0]
os.environ["KILIX_VOICE_SESSION"] = session
os.environ["KILIX_VOICE_SPAWN"] = os.environ.get("BOUNDARY_SPAWN", "0")
os.environ["KILIX_VOICE_DAEMON"] = "/bin/true"  # spawned only in spawn mode, and never a daemon
namespace = {"__name__": "kilix_voice_client"}
exec(compile(source, "kilix-voice-client", "exec"), namespace)
pending = [(point, [float(name[5:]) if name.startswith("STALL") else getattr(signal, name)
                     for name in names.split("+")])
           for point, names in (step.split(":") for step in steps.split(","))]

def due(point, frame, event, arg):
    code = frame.f_code
    if point == "binding":
        return event == "c_call" and getattr(arg, "__name__", "") == "bind"
    if point == "connecting":
        return event == "call" and code.co_name == "connect"
    if point == "connected":
        return event == "return" and code.co_name == "connect"
    if point == "removing":
        return event == "call" and code.co_name == "remove_receiver"
    if point == "cancelling":
        return event == "call" and code.co_name == "abort_turn"
    if point == "acknowledged":
        return (event == "return" and code.co_name == "request"
                and frame.f_locals["message"]["op"] == "dictate")
    if point == "aborting":
        return (event == "call" and code.co_name == "exchange_existing"
                and frame.f_locals["message"].get("mode") == "abort")
    if point == "printing":
        return event == "c_call" and getattr(arg, "__name__", "") == "print"
    if point == "cleanup":
        return (event == "call" and code.co_name == "close"
                and code.co_filename.endswith("socket.py")
                and frame.f_locals["self"].type == socket.SOCK_DGRAM)
    raise ValueError(point)

def profile(frame, event, arg):
    if pending and due(pending[0][0], frame, event, arg):
        for action in pending.pop(0)[1]:
            if isinstance(action, float):
                time.sleep(action)
            else:
                os.kill(os.getpid(), action)

sys.setprofile(profile)
namespace["cmd_dictate"](int(seconds), discard == "1")
"""

    def test_a_signal_at_any_boundary_leaves_no_turn_and_no_socket(self):
        # Review round 5: a handler that raised could land after the daemon
        # confirmed the turn but before the client owned it (no stop at all),
        # inside the abort (a finish instead), or in cleanup (a traceback and
        # the socket left behind).
        abort = [{"op": "stop-dictation", "mode": "abort"}]
        finish = [{"op": "stop-dictation"}]
        cases = (
            # None: no turn was asked for at all.
            ("binding:SIGHUP", "0", 30, 1, None, ""),
            ("binding:SIGINT", "1", 30, 1, None, ""),
            # Connected, but no turn asked for yet: none is.
            ("connected:SIGHUP", "0", 30, 1, None, ""),
            ("connected:SIGINT", "1", 30, 1, None, ""),
            ("connected:SIGINT", "0", 30, 1, None, ""),
            ("acknowledged:SIGHUP", "0", 30, 1, abort, ""),
            ("acknowledged:SIGTERM", "0", 30, 1, abort, ""),
            ("acknowledged:SIGINT", "1", 30, 1, abort, ""),
            ("acknowledged:SIGINT,aborting:SIGINT", "1", 30, 1, abort, ""),
            ("acknowledged:SIGINT,aborting:SIGTERM", "1", 30, 1, abort, ""),
            ("acknowledged:SIGTERM,aborting:SIGINT", "0", 30, 1, abort, ""),
            ("acknowledged:SIGTERM+SIGINT", "0", 30, 1, abort, ""),
            # Held past its whole budget before the abort: one is still sent.
            ("acknowledged:SIGTERM,cancelling:STALL2.5", "0", 30, 1, abort, ""),
            ("acknowledged:SIGINT", "0", 30, 0, finish, "slow words\n"),
            # Once the words are being written, a cancel exits at once and the
            # dictation fails, so `kilix ask` throws away anything written.
            ("printing:SIGTERM", "0", 1, 1, finish, ""),
            ("printing:SIGINT", "1", 1, 1, finish, ""),
            ("printing:SIGINT", "0", 1, 0, finish, "slow words\n"),
            # The receiver goes as soon as the final is in, before any word is
            # written; a cancel then still discards the words.
            ("cleanup:SIGTERM", "0", 1, 1, finish, ""),
            ("cleanup:SIGHUP", "1", 1, 1, finish, ""),
            ("cleanup:SIGINT", "1", 1, 1, finish, ""),
            ("cleanup:SIGINT", "0", 1, 0, finish, "slow words\n"),
        )
        for steps, discard, seconds, status, stops, words in cases:
            with self.subTest(steps=steps, discard=discard):
                requests, _accepted, close = self.fake_voice_daemon(final_after=0.1)
                result = subprocess.run(
                    [sys.executable, "-c", self.BOUNDARY_CHILD, str(KILIX),
                     str(self.voice_session), steps, discard, str(seconds)],
                    env=self.environment, text=True, capture_output=True, timeout=30)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual(result.stdout, words)
                if stops is None:
                    self.assertEqual(requests, [])
                else:
                    self.assertEqual(requests[0]["op"], "dictate")
                    self.assertEqual(requests[1:], stops)
                self.assertFalse(any(self.voice_session.glob("cli-*.sock")))
                close()

    def test_a_hangup_while_the_turn_is_unconfirmed_aborts_it(self):
        # The daemon closed the dictate request unanswered, so the turn may
        # be recording, and the terminal hung up meanwhile: the compensating
        # stop must discard the words, not finish them.
        self.install_fake_daemon()
        group = []
        requests, _accepted, _close = self.fake_voice_daemon(
            drop_dictate_reply=True, on_dictate=lambda _request: os.killpg(group[0], signal.SIGHUP))
        proc = self.start_dictate("--seconds", "30")
        group.append(proc.pid)
        out, _err = proc.communicate(timeout=15)
        self.assertEqual(out, "")
        self.assertEqual(requests[1:], [{"op": "stop-dictation", "mode": "abort"}])
        self.assertFalse(any(self.voice_session.glob("cli-*.sock")))

    def test_an_error_ends_the_turn_without_a_stop(self):
        # An error is the turn's end. stop-dictation names no turn, so a stop
        # sent after it could end the next one, which may be someone else's.
        self.install_fake_daemon()
        requests, _accepted, _close = self.fake_voice_daemon(error_after=0.1)
        proc = self.start_dictate("--seconds", "30")
        out, err = proc.communicate(timeout=15)
        self.assertEqual(proc.returncode, 1, err)
        self.assertEqual(out, "")
        self.assertIn("the recogniser failed", err)
        self.assertEqual([request["op"] for request in requests], ["dictate"])
        self.assertFalse(any(self.voice_session.glob("cli-*.sock")))

    def receiver_present(self):
        return any(self.voice_session.glob("cli-*.sock"))

    def test_a_wedged_daemon_cannot_hold_a_cancel(self):
        # Two stop attempts at the 5 s reply timeout held a cancelled client
        # 10 s. The broker SIGKILLs a closed pane's session 1.5 s after its
        # SIGTERM, so the receiver goes first and the abort has 2 s in all.
        # The order is checked where the abort arrives, not by a clock; the
        # exit bound leaves room for a loaded host.
        self.install_fake_daemon()
        at_abort = []
        _requests, accepted, _close = self.fake_voice_daemon(
            stop_reply_delay=8.0, on_stop=lambda _request: at_abort.append(self.receiver_present()))
        proc = self.start_dictate("--seconds", "30", "--discard-on-interrupt")
        self.assertTrue(accepted.wait(10))
        started = time.monotonic()
        os.killpg(proc.pid, signal.SIGINT)
        out, err = proc.communicate(timeout=15)
        elapsed = time.monotonic() - started
        self.assertEqual(at_abort[:1], [False])
        self.assertLess(elapsed, 4.5)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(out, "")
        self.assertIn("could not cancel dictation", err)

    def wait_for(self, predicate, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not predicate():
            time.sleep(0.02)
        return predicate()

    def test_a_cancel_does_not_wait_out_an_unanswered_dictate_request(self):
        # kilix-voiced answers one control connection at a time, so the abort
        # queues behind the dictate it cancels; the client must not sit out
        # the 5 s reply timeout first (round 6, R1).
        self.install_fake_daemon()
        requests, _accepted, _close = self.fake_voice_daemon(reply_delay=6.0)
        proc = self.start_dictate("--seconds", "30", "--discard-on-interrupt")
        self.assertTrue(self.received.wait(10))
        started = time.monotonic()
        os.killpg(proc.pid, signal.SIGINT)
        self.assertTrue(self.wait_for(lambda: not self.receiver_present(), 3.0))
        out, err = proc.communicate(timeout=15)
        self.assertLess(time.monotonic() - started, 4.5)
        self.assertEqual((proc.returncode, out), (1, ""))
        self.assertIn("could not cancel dictation", err)
        # Once the daemon is free it reads the queued abort, after the dictate.
        self.assertTrue(self.wait_for(lambda: len(requests) >= 2, 10))
        self.assertEqual(requests[1:], [{"op": "stop-dictation", "mode": "abort"}])

    def test_a_cancel_does_not_wait_out_an_unanswered_finish(self):
        # At the cap the client sends a finish; a cancel while its reply is
        # awaited must abort at once, not retry the finish (round 6, R1).
        self.install_fake_daemon()
        finishing = threading.Event()
        requests, _accepted, _close = self.fake_voice_daemon(
            stop_reply_delay=6.0, on_stop=lambda request: finishing.set())
        proc = self.start_dictate("--seconds", "1", "--discard-on-interrupt")
        self.assertTrue(finishing.wait(10))
        started = time.monotonic()
        os.killpg(proc.pid, signal.SIGINT)
        self.assertTrue(self.wait_for(lambda: not self.receiver_present(), 3.0))
        out, err = proc.communicate(timeout=15)
        self.assertLess(time.monotonic() - started, 4.5)
        self.assertEqual((proc.returncode, out), (1, ""))
        self.assertTrue(self.wait_for(lambda: len(requests) >= 3, 15))
        self.assertEqual(requests[1:], [{"op": "stop-dictation"},
                                        {"op": "stop-dictation", "mode": "abort"}])

    def test_a_cancel_does_not_wait_out_a_finish_asked_for_by_ctrl_c(self):
        # Plain Ctrl-C finishes the turn; a hangup while that finish's reply
        # is awaited must not wait it out either.
        self.install_fake_daemon()
        finishing = threading.Event()
        requests, accepted, _close = self.fake_voice_daemon(
            stop_reply_delay=6.0, on_stop=lambda request: finishing.set())
        proc = self.start_dictate("--seconds", "30")
        self.assertTrue(accepted.wait(10))
        os.killpg(proc.pid, signal.SIGINT)
        self.assertTrue(finishing.wait(10))
        started = time.monotonic()
        os.killpg(proc.pid, signal.SIGTERM)
        self.assertTrue(self.wait_for(lambda: not self.receiver_present(), 3.0))
        out, err = proc.communicate(timeout=15)
        self.assertLess(time.monotonic() - started, 4.5)
        self.assertEqual((out, err), ("", ""))
        self.assertTrue(self.wait_for(lambda: len(requests) >= 3, 15))
        self.assertEqual(requests[1:], [{"op": "stop-dictation"},
                                        {"op": "stop-dictation", "mode": "abort"}])

    def test_the_cancel_budget_runs_from_the_signal(self):
        # The 2 s are counted from the cancel, not from the abort: time the
        # client lost before it got there, here 1.2 s, comes out of them.
        # The abort's reply takes 1.2 s, so it is too late.
        self.install_fake_daemon()
        requests, _accepted, _close = self.fake_voice_daemon(final_after=0.1, stop_reply_delay=1.2)
        result = subprocess.run(
            [sys.executable, "-c", self.BOUNDARY_CHILD, str(KILIX), str(self.voice_session),
             "acknowledged:SIGINT,removing:STALL1.2", "1", "30"],
            env=self.environment, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("could not cancel dictation", result.stderr)
        self.assertEqual(requests[1], {"op": "stop-dictation", "mode": "abort"})
        self.assertFalse(self.receiver_present())

    def test_a_cancel_while_the_daemon_starts_asks_for_no_turn(self):
        # A cancel while the client waited for a daemon to start was acted on
        # only once it connected, after it had asked for a turn (round 6, R2).
        Path(self.environment["KILIX_SESSION_HOME"]).mkdir(parents=True, exist_ok=True)
        session = Path(tempfile.mkdtemp(dir=self.environment["KILIX_SESSION_HOME"]))
        for name, discard, message in (("SIGINT", "1", "dictation cancelled"), ("SIGTERM", "0", "")):
            with self.subTest(name):
                started = time.monotonic()
                result = subprocess.run(
                    [sys.executable, "-c", self.BOUNDARY_CHILD, str(KILIX), str(session),
                     "connecting:" + name, discard, "30"],
                    env={**self.environment, "BOUNDARY_SPAWN": "1"},
                    text=True, capture_output=True, timeout=30)
                self.assertLess(time.monotonic() - started, 6.0)  # spawn waits 8 s
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertNotIn("not running", result.stderr)
                self.assertIn(message, result.stderr)
                self.assertFalse(any(session.glob("cli-*.sock")))

    def test_a_cancel_is_not_held_by_a_blocked_write(self):
        # The words go to a pipe nobody drains; a cancel must still end the
        # client, with a failure status, and leave no receiver (round 6, R4).
        self.install_fake_daemon()
        words = "word " * 6000
        for sig, extra in ((signal.SIGTERM, ()), (signal.SIGINT, ("--discard-on-interrupt",))):
            with self.subTest(sig.name):
                pids = []
                requests, accepted, close = self.fake_voice_daemon(
                    final_on_accept=words,
                    on_dictate=lambda request: pids.append(
                        int(Path(request["sock"]).stem.split("-")[1])))
                proc = subprocess.Popen(
                    [KILIX, "dictate", "--seconds", "30", *extra], env=self.environment,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                    pipesize=4096)
                self.assertTrue(accepted.wait(10))
                client = Path("/proc/%d" % pids[0])
                self.assertTrue(self.wait_for(
                    lambda: "pipe" in (client / "wchan").read_text(), 10))
                os.killpg(proc.pid, sig)
                self.assertTrue(self.wait_for(lambda: not client.exists(), 3.0))
                self.assertFalse(self.receiver_present())
                proc.communicate(timeout=10)
                self.assertEqual([request["op"] for request in requests], ["dictate"])
                close()

    def test_a_refused_turn_is_not_stopped(self):
        # A refusal means no turn of ours exists; a stop names no turn, so it
        # would end the one that does, another caller's.
        self.install_fake_daemon()
        requests, _accepted, _close = self.fake_voice_daemon(refuse_dictate=True)
        result = self.run_kilix("dictate", "--seconds", "30", check=False)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("another caller is dictating", result.stderr)
        self.assertEqual([request["op"] for request in requests], ["dictate"])

    def test_a_lost_abort_reply_is_retried_within_the_budget(self):
        self.install_fake_daemon()
        requests, accepted, _close = self.fake_voice_daemon(drop_first_abort_reply=True)
        proc = self.start_dictate("--seconds", "30", "--discard-on-interrupt")
        self.assertTrue(accepted.wait(10))
        os.killpg(proc.pid, signal.SIGINT)
        out, err = proc.communicate(timeout=10)
        self.assertEqual((proc.returncode, out), (1, ""))
        self.assertIn("dictation cancelled", err)
        self.assertNotIn("could not cancel", err)
        self.assertEqual(requests[1:], [{"op": "stop-dictation", "mode": "abort"}] * 2)

    def test_a_daemon_that_predates_cancelling_is_named(self):
        # A kilix-voiced from before stop modes drops `mode`, finishes the turn
        # and echoes nothing; its acknowledgement is no proof of an abort.
        self.install_fake_daemon()
        requests, accepted, _close = self.fake_voice_daemon(final_after=0.1, echo_mode=False)
        proc = self.start_dictate("--seconds", "30", "--discard-on-interrupt")
        self.assertTrue(accepted.wait(10))
        os.killpg(proc.pid, signal.SIGINT)
        out, err = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 1, err)
        self.assertEqual(out, "")
        self.assertIn("predates cancelling", err)
        self.assertEqual(requests[1:], [{"op": "stop-dictation", "mode": "abort"}])

    def test_a_cancel_before_the_turn_is_confirmed_aborts_it_too(self):
        # The signal can land while the client still waits for the daemon to
        # confirm the turn; the compensating stop must then abort as well.
        self.install_fake_daemon()
        cases = ((signal.SIGHUP, ()), (signal.SIGINT, ("--discard-on-interrupt",)))
        for sig, extra in cases:
            with self.subTest(sig.name):
                requests, _accepted, close = self.fake_voice_daemon(reply_delay=1.0)
                proc = self.start_dictate("--seconds", "30", *extra)
                self.assertTrue(self.received.wait(10))
                os.killpg(proc.pid, sig)
                proc.communicate(timeout=15)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and len(requests) < 2:
                    time.sleep(0.05)
                self.assertEqual(requests[1:], [{"op": "stop-dictation", "mode": "abort"}])
                close()

    def test_a_final_is_awaited_longer_than_five_seconds_after_the_cap(self):
        # Whisper decodes only once recording stops; a full 30 s turn took 11 s
        # under load, and the client gave up 5 s after its stop with "heard
        # nothing before the dictation timed out" (2026-10-20).
        self.install_fake_daemon()
        requests, accepted, _close = self.fake_voice_daemon(final_after=6.0)
        proc = self.start_dictate("--seconds", "2")
        self.assertTrue(accepted.wait(10))
        out, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(out, "slow words\n")
        self.assertEqual(requests[1:], [{"op": "stop-dictation"}])

    def test_voice_doctor_finds_a_model_kilix_content_keeps(self):
        # kilix-voice reads whisper-small-en where `kilix models install` put
        # it. Doctor looked only under voice/models and called a working,
        # licensed model "not installed" (2026-10-20).
        self.hermetic_path()
        data = Path(self.environment["KILIX_DATA_HOME"])
        content = data / "desktop-apps" / "assets" / "faster-whisper-small-en"
        local = data / "voice" / "models" / "whisper-small-en"

        def model_line():
            lines = [line for line in self.run_kilix("voice", "doctor").stdout.splitlines()
                     if line.startswith("speech model:")]
            self.assertEqual(len(lines), 1)
            return lines[0]

        self._configure_devices(KILIX_VOICE_STT_ENGINE="whisper",
                                KILIX_VOICE_STT_MODEL="whisper-small-en")
        self.assertEqual(model_line(), f"speech model: not installed ({content})")
        content.mkdir(parents=True)
        self.assertEqual(model_line(), f"speech model: {content}")
        local.mkdir(parents=True)
        self.assertEqual(model_line(), f"speech model: {local}")
        # A model kilix-content does not keep is still looked for under voice/models.
        self._configure_devices(KILIX_VOICE_STT_ENGINE="vosk",
                                KILIX_VOICE_STT_MODEL="small-en-us")
        self.assertEqual(model_line(), "speech model: not installed (%s)"
                         % (data / "voice" / "models" / "small-en-us"))
        # Whistle is the other model kilix-content keeps, in its own directory.
        self._configure_devices(KILIX_VOICE_STT_ENGINE="whistle",
                                KILIX_VOICE_STT_MODEL="whistle")
        whistle = data / "desktop-apps" / "assets" / "whistle"
        self.assertEqual(model_line(), f"speech model: not installed ({whistle})")
        whistle.mkdir(parents=True)
        self.assertEqual(model_line(), f"speech model: {whistle}")


if __name__ == "__main__":
    unittest.main()
