"""Discovery reports observed installation facts without running components."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
import agent_capabilities as capabilities


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
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
        self.assertEqual(result["kilix"]["path_selected"]["status"], "missing")
        self.assertEqual(result["needle"]["status"], "missing")
        self.assertEqual(result["os_provenance"]["status"], "missing")

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
        (self.source / "kilix").write_bytes(b"x" * (capabilities.MAX_FILE_BYTES + 1))
        self.assertEqual(self.discover()["kilix"]["invoked_source"]["status"], "too_large")

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
