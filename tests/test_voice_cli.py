"""Behavioral coverage for the Kilix voice command boundary."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
KILIX = ROOT / "kilix"


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
        self.environment = {
            **os.environ,
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
        }

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

    def install_fake_daemon(self):
        daemon = self.bin / "kilix-voiced"
        daemon.write_text(
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = --version ]; then\n"
            "  printf '%s\\n' 'kilix-voiced fixture'\n"
            "  exit 0\n"
            "fi\n"
            "printf '%s\\n' 'voice daemon foreground'\n"
            "printf 'argc=%s\\n' \"$#\"\n"
        )
        daemon.chmod(0o755)

    def install_fake_stt(self):
        tool = self.bin / "kilix-stt"
        tool.write_text(
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = --version ]; then\n"
            "  printf '%s\\n' 'kilix-stt fixture'\n"
            "  exit 0\n"
            "fi\n"
            "printf 'arg=%s\\n' \"$@\"\n"
        )
        tool.chmod(0o755)
        return tool

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
        prefix_bin = Path(self.environment["KILIX_VOICE_PREFIX"]) / "bin"
        prefix_bin.mkdir(parents=True, exist_ok=True)
        daemon = prefix_bin / "kilix-voiced"
        daemon.write_text(
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = --version ]; then\n"
            "  printf '%s\\n' 'kilix-voiced prefix fixture'\n"
            "  exit 0\n"
            "fi\n"
            "printf '%s\\n' 'prefix daemon foreground'\n"
        )
        daemon.chmod(0o755)
        return daemon

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
        self.assertEqual(requests[1], {"op": "stop-dictation"})
        self.assertFalse(any(voice_session.glob("cli-*.sock")))


if __name__ == "__main__":
    unittest.main()
