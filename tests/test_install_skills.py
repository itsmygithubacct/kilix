"""Explicit opt-in after vendor setup; ordinary install/update stays unchanged."""
import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
import install as installer  # noqa: E402
import agent_programs  # noqa: E402


class InstallSkillsTests(unittest.TestCase):
    def run_setup(self, args, vendor_result=0, skill_result=0):
        events = []
        entries = [{"id": x, "kind": "agent"} for x in ("codex", "claude", "kimi")]
        entries.append({"id": "some-app", "kind": "app"})
        def install(name, **kwargs):
            events.append(("install", name, kwargs))
            return vendor_result
        def update(name):
            events.append(("update", name))
            return vendor_result
        def skills(argv):
            events.append(("skills", argv))
            return skill_result
        with mock.patch.object(installer, "rows", return_value=entries), \
             mock.patch.object(installer, "install", side_effect=install), \
             mock.patch.object(installer, "update", side_effect=update), \
             mock.patch.object(installer.agent_skills, "main", side_effect=skills), \
             contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            result = installer.main(args)
        return result, events

    def test_shared_resolver_is_exact_admin_owned_implementation(self):
        self.assertIs(installer._resolve_agent_command, agent_programs.resolve_agent_command)
        self.assertIs(installer._AGENT_PREFIX_BINDIRS, agent_programs.AGENT_PREFIX_BINDIRS)

    def test_ordinary_install_and_update_do_not_enable_skills(self):
        for agent in ("codex", "claude", "kimi"):
            for update in (False, True):
                args = [agent, "--yes"] + (["--update"] if update else [])
                result, events = self.run_setup(args)
                self.assertEqual(result, 0)
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0][0], "update" if update else "install")

    def test_explicit_install_or_update_runs_skills_after_vendor_success(self):
        for agent in ("codex", "claude", "kimi"):
            for update in (False, True):
                result, events = self.run_setup(
                    [agent, "--skills", "--yes"] + (["--update"] if update else []))
                self.assertEqual(result, 0)
                self.assertEqual(events[-1], ("skills", ["install", "--agent", agent]))
                self.assertEqual(len(events), 2)

    def test_vendor_failure_or_decline_never_installs_skills(self):
        for result in (1, 2, 130):
            code, events = self.run_setup(["codex", "--skills"], vendor_result=result)
            self.assertEqual(code, result)
            self.assertEqual(len(events), 1)

    def test_skill_conflict_is_failure_without_reinstalling_agent(self):
        code, events = self.run_setup(["codex", "--skills"], skill_result=1)
        self.assertEqual(code, 1)
        self.assertEqual([event[0] for event in events], ["install", "skills"])

    def test_wrong_missing_or_multiple_targets_refuse_before_vendor(self):
        for args in (["--skills"], ["--skills", "--list"],
                     ["--skills", "some-app"], ["--skills", "codex", "kimi"],
                     ["--skills", "unknown"], ["--skills", "codex", "--bad-option"]):
            result, events = self.run_setup(args)
            self.assertEqual(result, 2)
            self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
