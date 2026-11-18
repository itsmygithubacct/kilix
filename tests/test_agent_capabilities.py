"""Discovery reports observed installation facts without running components."""
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import tracemalloc
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
import agent_capabilities as capabilities
import agent_control as control

OPERATIONS = ["agent.deliver", "agent.launch", "operation.status", "pane.open"]


def whole_text_labels(raw):
    """The former whole-file reading, kept as an oracle for the streaming scan."""
    labels = set()
    for dispatch in re.findall(r"^\s*([^()\s]+)\)", raw.decode("utf-8"), re.M):
        labels.update(dispatch.split("|"))
    return labels & capabilities.COMMAND_NAMES


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        # Resolved: results carry resolved roots even when TMPDIR is a symlink.
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        self.provenance = self.root / "versions.env"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = {"PATH": str(self.bin), "HOME": str(self.root / "unused-home")}

    def tree(self, path, *, version="0.2.2", commands=("action", "pane", "tab")):
        path.mkdir(exist_ok=True)
        (path / "config/kilix_sdk").mkdir(parents=True)
        (path / "VERSION").write_text(version)
        (path / "kilix").write_text("#!/bin/sh\nexit 99\n" + "\n".join(name + ")" for name in commands))
        (path / "kilix").chmod(0o755)
        (path / "config/kilix_sdk/panes.py").write_text("raise RuntimeError('must not import')")
        (path / "config/agent_actions.py").write_text(
            "SCHEMA = 'kilix.actions/v1'\n"
            "ACTION_OPERATIONS = {'pane.open': {'params': ['argv','cwd']}}\n"
            "raise RuntimeError('must not import')\n")

    def discover(self):
        with mock.patch.object(capabilities, "_git_revision", return_value=("a" * 40, "observed")):
            return capabilities.discover(self.source, environ=self.env, provenance_path=self.provenance)

    def test_missing_components_and_provenance_are_explicit(self):
        result = self.discover()
        self.assertEqual(result["kilix"]["invoked_source"]["status"], "missing")
        self.assertEqual(result["kilix"]["invoked_source"]["version_status"], "missing")
        self.assertEqual(result["kilix"]["path_selected"]["status"], "missing")
        for state in ("dispatch_status", "version_status", "revision_status"):
            self.assertEqual(result["kilix"]["path_selected"][state], "not_inspected")
        self.assertEqual(result["needle"]["status"], "missing")
        self.assertEqual(result["os_provenance"]["status"], "missing")
        self.assertEqual((result["limits"]["launcher_bytes"], result["limits"]["launcher_line_chars"]),
                         (capabilities.MAX_LAUNCHER_BYTES, capabilities.MAX_LAUNCHER_LINE))

    def test_invoked_tree_and_path_install_are_separate(self):
        self.tree(self.source)
        installed = self.root / "installed"
        self.tree(installed, version="0.2.1", commands=("pane",))
        (self.bin / "kilix").symlink_to(installed / "kilix")
        result = self.discover()["kilix"]
        self.assertFalse(result["same_root"])
        self.assertEqual(result["invoked_source"]["version"], "0.2.2")
        self.assertEqual(result["path_selected"]["version"], "0.2.1")
        self.assertEqual(result["path_selected"]["commands_present"], ["pane"])
        self.assertEqual(result["invoked_source"]["action_backend"]["schema"], "kilix.actions/v1")

    def test_mismatched_expected_revision_is_not_reported_as_installed(self):
        self.tree(self.source)
        self.provenance.write_text("KILIX_REF='" + "b" * 40 + "'\nKILIX_VERSION='0.2.1'\n")
        result = self.discover()
        selected = result["kilix"]["invoked_source"]
        self.assertFalse(selected["revision_matches_expected"])
        self.assertEqual(selected["revision"], "a" * 40)
        self.assertEqual(selected["version"], "0.2.2")
        self.assertEqual(result["os_provenance"]["recorded"]["KILIX_VERSION"], "0.2.1")

    def test_inspection_never_launches_setup_or_imports_backends(self):
        self.tree(self.source)
        (self.bin / "kilix").symlink_to(self.source / "kilix")
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        with mock.patch.object(capabilities.subprocess, "Popen", side_effect=AssertionError("no CLI probes")):
            result = self.discover()
        self.assertTrue(result["read_only"])
        self.assertEqual(before, sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*")))
        self.assertFalse((self.root / "unused-home").exists())

    def test_malformed_state_and_nonliteral_schema_are_not_executed(self):
        self.tree(self.source)
        self.provenance.write_text("KILIX_REF='unterminated\n")
        (self.source / "config/agent_actions.py").write_text("ACTION_OPERATIONS = __import__('os').system('false')\n")
        result = self.discover()
        self.assertEqual(result["os_provenance"]["status"], "malformed")
        self.assertEqual(result["os_provenance"]["recorded"], {})
        self.assertEqual(result["kilix"]["invoked_source"]["action_backend"]["status"], "malformed")

    def test_provenance_filters_unrelated_values(self):
        self.provenance.write_text("TOKEN='private-token'\nKILIX_VERSION='0.2.2'\n")
        self.env["KILIX_RC_PASSWORD"] = "private-password"
        raw = json.dumps(self.discover())
        self.assertNotIn("private-token", raw)
        self.assertNotIn("private-password", raw)

    def test_alias_dispatch_requires_implementation(self):
        self.tree(self.source)
        (self.source / "kilix").write_text("ls|focus|pane|tab|new-tab)\nagent-control)\n")
        self.assertEqual(self.discover()["kilix"]["invoked_source"]["commands_present"], ["pane", "tab"])

    def test_fifo_and_oversized_files_do_not_block(self):
        os.mkfifo(self.source / "kilix")
        self.assertEqual(self.discover()["kilix"]["invoked_source"]["status"], "not_regular")
        (self.source / "kilix").unlink()
        # Above the per-file bound the launcher is still streamed in full...
        (self.source / "kilix").write_bytes(b"x" * (capabilities.MAX_FILE_BYTES + 1))
        selected = self.discover()["kilix"]["invoked_source"]
        self.assertEqual((selected["status"], selected["dispatch_status"]), ("observed", "observed"))
        # ...and above its own bound the scan is refused without reading it.
        os.truncate(self.source / "kilix", capabilities.MAX_LAUNCHER_BYTES + 1)
        with mock.patch.object(capabilities.os, "read", side_effect=AssertionError("read")):
            selected = self.discover()["kilix"]["invoked_source"]
        self.assertEqual((selected["status"], selected["dispatch_status"]), ("observed", "too_large"))
        self.assertEqual(selected["launcher_bytes"], capabilities.MAX_LAUNCHER_BYTES + 1)

    def assert_independent_facts(self, selected):
        """Facts read beside the launcher, never from it."""
        self.assertEqual((selected["version"], selected["version_status"]), ("0.2.2", "observed"))
        self.assertEqual((selected["revision"], selected["revision_status"]), ("a" * 40, "observed"))
        backend = selected["action_backend"]
        self.assertEqual((backend["status"], backend["schema"]), ("observed", "kilix.actions/v1"))
        self.assertEqual(backend["path"], str(self.source / "config/agent_actions.py"))
        self.assertEqual(list(backend["operations"]), ["pane.open"])

    def test_repository_launcher_is_scanned_in_full(self):
        # RC9's 313,682-byte launcher outgrew the 256 KiB per-file bound, which
        # erased its version, revision and actions. Its pane/tab dispatch sits
        # past that offset, so reading only the first 256 KiB would miss them.
        launcher = ROOT / "kilix"
        raw = launcher.read_bytes()
        self.assertLessEqual(len(raw), capabilities.MAX_LAUNCHER_BYTES, "raise the bound deliberately")
        with mock.patch.object(capabilities, "_git_revision", return_value=("c" * 40, "observed")) as git:
            selected = capabilities._component(ROOT)
        git.assert_called_once_with(ROOT)
        self.assertEqual((selected["status"], selected["dispatch_status"]), ("observed", "observed"))
        self.assertEqual(selected["launcher_bytes"], len(raw))
        self.assertEqual(whole_text_labels(raw), capabilities.COMMAND_NAMES)
        self.assertEqual(selected["commands_present"], list(capabilities.COMMAND_FILES))
        self.assertEqual(selected["version"], (ROOT / "VERSION").read_text(encoding="utf-8").strip())
        self.assertEqual((selected["revision"], selected["revision_status"]), ("c" * 40, "observed"))
        backend = selected["action_backend"]
        self.assertEqual((backend["status"], backend["path"]), ("observed", str(ROOT / "config/agent_actions.py")))
        self.assertEqual(sorted(backend["operations"]), OPERATIONS)
        # Static metadata must equal what the runtime accepts; nothing is imported.
        self.assertEqual(backend["operations"]["agent.launch"]["reasoning_effort"]["values"],
                         list(control.REASONING_EFFORTS))
        # Block boundaries, including ones inside multibyte UTF-8, change nothing.
        for block in (1, 7, 4093):
            with self.subTest(block=block), mock.patch.object(capabilities, "LAUNCHER_BLOCK", block), \
                    mock.patch.object(capabilities, "LAUNCHER_TIMEOUT", 60.0):
                self.assertEqual(capabilities._launcher(launcher, True)["labels"], capabilities.COMMAND_NAMES)

    def test_repository_dispatch_survives_beyond_the_file_bound(self):
        # The real dispatch table, moved wholly past the per-file bound.
        self.tree(self.source)
        padding = b"".join(b"# padding line %06d\n" % i for i in range(capabilities.MAX_FILE_BYTES // 20 + 1))
        raw = b"#!/usr/bin/env bash\n" + padding + (ROOT / "kilix").read_bytes()
        (self.source / "kilix").write_bytes(raw)
        self.assertEqual(capabilities._read(self.source / "kilix"), (None, "too_large"))
        selected = self.discover()["kilix"]["invoked_source"]
        self.assertEqual(selected["dispatch_status"], "observed")
        # Only commands whose implementations exist in this tree are reported.
        self.assertEqual(selected["commands_present"], ["pane", "tab", "action"])
        self.assert_independent_facts(selected)

    def test_launcher_refusals_keep_independent_metadata(self):
        labels = b"#!/bin/sh\npane)\ntab)\naction)\n"
        cases = {"missing": None,
                 "fifo": lambda path: os.mkfifo(path),
                 "directory": lambda path: path.mkdir(),
                 "device": lambda path: path.symlink_to(os.devnull),
                 "too_large": lambda path: (path.write_bytes(labels),
                                            os.truncate(path, capabilities.MAX_LAUNCHER_BYTES + 1)),
                 "invalid_utf8": lambda path: path.write_bytes(labels + b"\xff\n"),
                 "truncated_utf8": lambda path: path.write_bytes(labels + b"\xe2\x82"),
                 "timeout": lambda path: path.write_bytes(labels)}
        expected = {"missing": ("missing", "not_inspected"), "fifo": ("not_regular", "not_inspected"),
                    "directory": ("not_regular", "not_inspected"), "device": ("not_regular", "not_inspected"),
                    "too_large": ("observed", "too_large"), "invalid_utf8": ("observed", "unreadable"),
                    "truncated_utf8": ("observed", "unreadable"), "timeout": ("observed", "timeout")}
        if os.geteuid() != 0:
            cases["no_permission"] = lambda path: (path.write_bytes(labels), path.chmod(0))
            expected["no_permission"] = ("unreadable", "not_inspected")
        for name, make in cases.items():
            with self.subTest(launcher=name):
                self.source = self.root / ("case-" + name)
                self.tree(self.source)
                (self.source / "kilix").unlink()
                if make:
                    make(self.source / "kilix")
                # The deadline passes only after some labels were already seen.
                ticks = iter([0.0] * 4 + [capabilities.LAUNCHER_TIMEOUT + 1] * 64)
                clock = (mock.Mock(monotonic=lambda: next(ticks)) if name == "timeout"
                         else capabilities.time)
                with mock.patch.object(capabilities, "time", clock), \
                        mock.patch.object(capabilities, "LAUNCHER_BLOCK", 8):
                    result = self.discover()
                selected = result["kilix"]["invoked_source"]
                self.assertEqual((selected["status"], selected["dispatch_status"]), expected[name])
                # A refused or incomplete scan never reports a partial command list.
                self.assertEqual(selected["commands_present"], [])
                self.assert_independent_facts(selected)
                self.assertLessEqual(len(json.dumps(result, ensure_ascii=True).encode()),
                                     capabilities.MAX_RESPONSE_BYTES)

    def test_scan_bound_holds_even_when_the_size_is_misreported(self):
        # Some regular files (procfs, growing files) report a size below their content.
        path = self.root / "growing"
        path.write_bytes(b"pane)\n")
        os.truncate(path, capabilities.MAX_LAUNCHER_BYTES + 1)
        real_read, consumed = os.read, []

        def counting_read(fd, size):
            data = real_read(fd, size)
            consumed.append(len(data))
            return data

        fd = os.open(path, os.O_RDONLY)
        try:
            with mock.patch.object(capabilities.os, "read", counting_read):
                self.assertEqual(capabilities._dispatch(fd, 0), ("too_large", frozenset()))
        finally:
            os.close(fd)
        self.assertEqual(sum(consumed), capabilities.MAX_LAUNCHER_BYTES + 1)

    def test_huge_and_degenerate_lines_stream_in_bounded_memory_and_time(self):
        self.tree(self.source)
        launcher = self.source / "kilix"
        # One line filling the whole bound: only its head is ever retained.
        launcher.write_bytes(b"pane)")
        os.truncate(launcher, capabilities.MAX_LAUNCHER_BYTES)
        tracemalloc.start()
        try:
            record = capabilities._launcher(launcher, True)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual((record["dispatch_status"], record["labels"]), ("observed", {"pane"}))
        self.assertLess(peak, capabilities.MAX_LAUNCHER_BYTES // 4)
        # A label counts only within the first MAX_LAUNCHER_LINE characters of
        # its line, wherever block boundaries fall; an overlong line does not
        # hide the next one.
        launcher.write_text("#!/bin/sh\n" + " " * capabilities.MAX_LAUNCHER_LINE + "tab)\n" +
                            "action)" + "x" * (3 * capabilities.MAX_LAUNCHER_LINE) + "\npane)\n")
        for block in (5, 4096, capabilities.LAUNCHER_BLOCK):
            with self.subTest(block=block), mock.patch.object(capabilities, "LAUNCHER_BLOCK", block):
                self.assertEqual(capabilities._launcher(launcher, True)["labels"], {"action", "pane"})
        # Blank-line floods are linear; with a newline-crossing `^\s*` this
        # input runs for minutes.
        launcher.write_bytes(b" \n" * (capabilities.MAX_FILE_BYTES * 4) + b"tab)\n")
        with mock.patch.object(capabilities, "LAUNCHER_TIMEOUT", 30.0):
            record = capabilities._launcher(launcher, True)
        self.assertEqual((record["dispatch_status"], record["labels"]), ("observed", {"tab"}))

    def test_response_has_hard_byte_bound(self):
        self.tree(self.source)
        huge = {"op" + str(i): {"detail" + str(j): "x" * 1200 for j in range(8)} for i in range(16)}
        (self.source / "config/agent_actions.py").write_text(
            "SCHEMA = 'kilix.actions/v1'\nACTION_OPERATIONS = " + repr(huge))
        result = self.discover()
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=True).encode()), capabilities.MAX_RESPONSE_BYTES)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["kilix"]["invoked_source"]["action_backend"]["status"], "response_limit")

    def test_needle_symlink_and_directory_kilix_selector(self):
        self.tree(self.source)
        needle = self.root / "needle"
        needle.mkdir()
        (needle / "kilix-needle").write_text("#!/bin/sh\nexit 99\n")
        (needle / "kilix-needle").chmod(0o755)
        (needle / "needle_cli.py").write_text("raise RuntimeError('must not import')")
        (self.bin / "kilix-needle").symlink_to(needle / "kilix-needle")
        self.env["KILIX_NEEDLE_KILIX"] = str(self.source)
        result = self.discover()
        self.assertEqual(result["needle"]["root"], str(needle))
        self.assertIsNone(result["needle"]["version"])
        self.assertEqual(result["needle_kilix_selection"]["resolved_command"], str(self.source / "kilix"))
        self.assertFalse(result["needle"]["action_adapter_present"])
        (needle / "panes_exact.py").write_text("# legacy natural language adapter\n")
        self.assertFalse(self.discover()["needle"]["action_adapter_present"])
        (needle / "action_backend.py").write_text("# exact request transport\n")
        (needle / "action_cli.py").write_text("# no model\n")
        (needle / "needle_cli.py").write_text("from action_cli import main as action_main\n")
        (needle / "mcp_server.py").write_text("name = 'kilix_action_plan'\n")
        result = self.discover()["needle"]
        self.assertTrue(result["action_adapter_present"])
        self.assertTrue(result["action_mcp_tools_present"])

    def test_needle_facts_do_not_depend_on_its_launcher_text(self):
        needle = self.root / "needle"
        needle.mkdir()
        for name, text in {"needle_cli.py": "from action_cli import main as action_main\n",
                           "mcp_server.py": "name = 'kilix_action_plan'\n",
                           "action_cli.py": "# no model\n", "action_backend.py": "# transport\n"}.items():
            (needle / name).write_text(text)
        launcher = needle / "kilix-needle"
        launcher.write_bytes(b"\xff" * (capabilities.MAX_FILE_BYTES + 1))  # neither small nor text
        launcher.chmod(0o755)
        (self.bin / "kilix-needle").symlink_to(launcher)
        result = self.discover()["needle"]
        self.assertEqual((result["status"], result["dispatch_status"]), ("observed", "not_inspected"))
        self.assertTrue(result["action_adapter_present"])
        self.assertTrue(result["action_mcp_tools_present"])
        self.assertEqual(result["version_status"], "unresolved")

    def test_unicode_provenance_cannot_exceed_response_bound(self):
        self.tree(self.source)
        self.provenance.write_text("\n".join(key + "='" + "\U0001f600" * 1000 + "'" for key in capabilities.PROVENANCE_KEYS))
        result = self.discover()
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=True).encode()), capabilities.MAX_RESPONSE_BYTES)

    def test_git_timeout_and_output_limits(self):
        git = self.root / "fake-git"
        git.write_text("#!/bin/sh\nexec sleep 3\n")
        git.chmod(0o755)
        with mock.patch.object(capabilities.shutil, "which", return_value=str(git)), \
                mock.patch.object(capabilities, "GIT_TIMEOUT", 0.05):
            start = time.monotonic()
            self.assertEqual(capabilities._git_revision(self.source), (None, "timeout"))
            self.assertLess(time.monotonic() - start, 0.5)
        git.write_text("#!/bin/sh\npython3 -c 'print(\"x\" * 4096)'\n")
        with mock.patch.object(capabilities.shutil, "which", return_value=str(git)):
            self.assertEqual(capabilities._git_revision(self.source), (None, "too_large"))

    def test_parent_repository_is_not_component_revision(self):
        # Git can resolve HEAD for an untracked subdirectory; reject that root.
        git = self.root / "fake-git"
        git.write_text("#!/bin/sh\nprintf '%s\\n' '" + str(self.root) + "' '" + "a" * 40 + "'\n")
        git.chmod(0o755)
        with mock.patch.object(capabilities.shutil, "which", return_value=str(git)):
            self.assertEqual(capabilities._git_revision(self.source), (None, "unresolved"))


if __name__ == "__main__":
    unittest.main()
