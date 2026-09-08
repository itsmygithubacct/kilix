"""Real isolated discovery roots, receipts and interruption/refusal controls."""
import contextlib
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
import agent_skills as skills  # noqa: E402


class SkillsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.home.mkdir(mode=0o700)
        self.source = self.base / "source"
        self.source.mkdir(mode=0o700)
        (self.source / "VERSION").write_text("0.2.2\n")
        for name in skills.NAMES:
            folder = self.source / "skills" / name
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: Test {name}.\n---\nInstructions.\n")
            (folder / "references").mkdir()
            (folder / "references" / "example.txt").write_text("supporting bytes\n")
        self.root = self.home / ".agents/skills"
        self.store = self.root.parent / skills.STORE
        self.before_fds = len(os.listdir("/proc/self/fd"))

    def tearDown(self):
        self.assertEqual(self.before_fds, len(os.listdir("/proc/self/fd")))

    def call(self, action="install", agent="codex", **kwargs):
        before = len(os.listdir("/proc/self/fd"))
        try:
            return skills.operate(action, agent, source=self.source,
                                  home=self.home, env=kwargs.pop("env", {}), **kwargs)
        finally:
            self.assertEqual(len(os.listdir("/proc/self/fd")), before)

    def snapshot(self, path=None):
        path = self.home if path is None else path
        result = {}
        for folder, dirs, files in os.walk(path, followlinks=False):
            for name in dirs + files:
                item = Path(folder) / name
                stat = item.lstat()
                result[str(item.relative_to(path))] = (
                    stat.st_ino, stat.st_mode,
                    os.readlink(item) if item.is_symlink() else
                    item.read_bytes() if item.is_file() else None)
        return result

    def test_read_only_status_and_remove_do_not_create_roots(self):
        original = self.snapshot()
        for agent in skills.AGENTS:
            self.assertEqual(self.call("status", agent)["state"], "not-installed")
            self.assertEqual(self.call("remove", agent)["state"], "not-installed")
        self.assertEqual(self.snapshot(), original)

    def test_actual_bundle_has_three_distinct_names_one_version(self):
        record, files, _, descriptions = skills.bundle(ROOT)
        self.assertEqual(set(descriptions), set(skills.NAMES))
        self.assertEqual(record["version"], (ROOT / "VERSION").read_text().strip())
        for name in skills.NAMES:
            self.assertIn(name + "/SKILL.md", files)

    def test_safe_root_owned_shipped_source_is_readable_but_destinations_require_user(self):
        # Simulate the ownership metadata of an /opt or distro source install;
        # all reads and writes still use only these actual private fixture files.
        fstat = os.fstat
        uid_index = 4
        def root_owned(fd):
            value = fstat(fd)
            path = Path(os.readlink(f"/proc/self/fd/{fd}"))
            if path == self.source or self.source in path.parents or path == self.root:
                fields = list(value)
                fields[uid_index] = 0
                return os.stat_result(fields)
            return value
        with mock.patch.object(skills.os, "fstat", side_effect=root_owned):
            self.assertEqual(skills.bundle(self.source)[0]["version"], "0.2.2")
            with self.assertRaises(skills.Conflict):
                self.call()
        self.assertFalse((self.store / "current").exists())

    def test_install_idempotent_update_remove_and_reinstall(self):
        installed = self.call()
        self.assertEqual(installed["state"], "installed")
        before = self.snapshot()
        self.assertEqual(self.call(), installed)
        self.assertEqual(self.snapshot(), before)
        old_target = os.readlink(self.store / "current")
        (self.source / "skills/kilix/SKILL.md").write_text(
            (self.source / "skills/kilix/SKILL.md").read_text() + "new bytes\n")
        updated = self.call()
        self.assertNotEqual(installed["digest"], updated["digest"])
        self.assertNotEqual(old_target, os.readlink(self.store / "current"))
        for name in skills.NAMES:
            self.assertEqual((self.root / name / "SKILL.md").read_bytes(),
                             (self.source / "skills" / name / "SKILL.md").read_bytes())
        self.assertEqual(self.call("remove")["state"], "not-installed")
        self.assertFalse(any(os.path.lexists(self.root / name) for name in skills.NAMES))
        self.assertTrue((self.store / old_target).is_dir())
        self.assertEqual(self.call()["state"], "installed")

    def test_codex_kimi_share_set_and_removal_preserves_other_registration(self):
        self.call()
        self.assertEqual(self.call("status", "kimi")["state"], "shared")
        before = self.snapshot()
        self.assertEqual(self.call("remove", "kimi")["state"], "shared")
        self.assertEqual(before, self.snapshot())
        links = {name: (self.root / name).lstat().st_ino for name in skills.NAMES}
        self.assertEqual(self.call(agent="kimi")["owners"], ["codex", "kimi"])
        self.assertEqual(self.call(agent="claude")["owners"], ["claude"])
        self.assertEqual(links, {name: (self.root / name).lstat().st_ino for name in skills.NAMES})
        self.assertEqual(self.call("remove")["state"], "shared")
        self.assertEqual(self.call("status", "kimi")["owners"], ["kimi"])
        self.assertEqual(self.call("remove", "kimi")["state"], "not-installed")
        self.assertEqual(self.call("status", "claude")["state"], "installed")

    def test_custom_roots_and_codex_home_do_not_redirect_shared_discovery(self):
        env = {"CODEX_HOME": str(self.home / "codex-other"),
               "CLAUDE_CONFIG_DIR": str(self.home / "claude-other"),
               "KIMI_CODE_HOME": str(self.home / "kimi-other")}
        for agent, expected in (("codex", self.root),
                                ("claude", self.home / "claude-other/skills")):
            self.assertEqual(self.call(agent=agent, env=env)["root"], str(expected))
        # A custom Kimi root cannot duplicate the already registered generic set.
        with self.assertRaises(skills.Conflict):
            self.call(agent="kimi", env=env)
        self.call("remove")
        self.assertEqual(self.call(agent="kimi", env=env)["root"],
                         str(self.home / "kimi-other/skills"))
        self.assertFalse((self.home / "codex-other").exists())

    def test_custom_root_must_be_absolute_and_no_parent_segments(self):
        for value in ("relative", str(self.home / "x/../y")):
            with self.assertRaises(skills.Conflict):
                self.call(agent="claude", env={"CLAUDE_CONFIG_DIR": value})
        self.assertEqual(self.snapshot(), {})

    def test_foreign_entries_and_dangling_links_are_preserved(self):
        self.root.mkdir(parents=True)
        for kind in ("file", "directory", "dangling", "live", "fifo"):
            with self.subTest(kind=kind):
                entry = self.root / "kilix-model-switch"
                if kind == "file":
                    entry.write_text("user")
                elif kind == "directory":
                    entry.mkdir()
                elif kind == "fifo":
                    os.mkfifo(entry)
                else:
                    entry.symlink_to(self.source if kind == "live" else self.base / "absent")
                before = (entry.lstat(), os.readlink(entry) if entry.is_symlink() else None)
                with self.assertRaises(skills.Conflict):
                    self.call()
                self.assertEqual(entry.lstat(), before[0])
                self.assertFalse(os.path.lexists(self.root / "kilix"))
                if entry.is_dir() and not entry.is_symlink():
                    entry.rmdir()
                else:
                    entry.unlink()

    def test_flat_and_legacy_copies_refuse_without_duplicate_registration(self):
        for relative in (".agents/skills/kilix.md", ".codex/skills/kilix",
                         ".kimi/skills/kilix", ".config/agents/skills/kilix"):
            with self.subTest(relative=relative):
                target = self.home / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("user")
                before = self.snapshot()
                agent = "kimi" if relative.startswith((".kimi", ".config")) else "codex"
                with self.assertRaises(skills.Conflict):
                    self.call(agent=agent)
                self.assertEqual(before, self.snapshot())
                target.unlink()

    def test_read_only_discovery_accepts_ordinary_shared_unrelated_directories(self):
        for directory in (".codex", ".claude", ".kimi-code"):
            folder = self.home / directory / "skills"
            folder.mkdir(parents=True)
            folder.chmod(0o775)
            folder.parent.chmod(0o775)
            (folder / "unrelated").mkdir()
        before = self.snapshot()
        for agent in skills.AGENTS:
            self.assertEqual(self.call("status", agent)["state"], "not-installed")
        self.assertEqual(before, self.snapshot())
        # User-owned 0775 is also supported for registration, without chmod.
        self.assertEqual(self.call(agent="claude")["state"], "installed")
        for relative, values in before.items():
            self.assertEqual(self.snapshot()[relative], values)
        self.assertEqual(self.call()["state"], "installed")
        duplicate = self.home / ".codex/skills/kilix"
        duplicate.write_text("user")
        with self.assertRaisesRegex(skills.Conflict, "existing discovery entry"):
            self.call("status")
        # Removal can resolve the duplicate without touching its user copy.
        self.assertEqual(self.call("remove")["state"], "not-installed")
        self.assertEqual(duplicate.read_text(), "user")

    def test_existing_user_owned_0775_brand_root_preserves_modes_through_removal(self):
        brand = self.home / ".claude"
        destination = brand / "skills"
        destination.mkdir(parents=True)
        brand.chmod(0o775)
        destination.chmod(0o775)
        config = brand / "settings.json"
        config.write_text('{"user":true}\n')
        before = {path: (path.stat().st_ino, path.stat().st_mode)
                  for path in (self.home, brand, destination, config)}
        self.assertEqual(self.call("status", "claude")["state"], "not-installed")
        self.assertEqual(self.call(agent="claude")["state"], "installed")
        self.assertEqual((brand / skills.STORE).stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.call("remove", "claude")["state"], "not-installed")
        self.assertEqual(before, {p: (p.stat().st_ino, p.stat().st_mode) for p in before})
        self.assertEqual(config.read_text(), '{"user":true}\n')

    def test_read_only_alternate_symlink_is_inspected_without_mutation(self):
        actual = self.home / "ordinary"
        actual.mkdir()
        (self.home / ".codex").symlink_to(actual)
        before = self.snapshot()
        self.assertEqual(self.call("status")["state"], "not-installed")
        self.assertEqual(before, self.snapshot())
        (actual / "skills").mkdir()
        (actual / "skills/kilix").symlink_to(self.base / "missing")
        with self.assertRaisesRegex(skills.Conflict, "existing discovery entry"):
            self.call()

    def test_unrelated_config_and_skills_survive_all_operations(self):
        self.root.mkdir(parents=True)
        user = self.root / "other"
        user.mkdir()
        (user / "SKILL.md").write_text("user skill")
        config = self.root.parent / "config.toml"
        config.write_bytes(b"user configuration\n")
        keep = {p: (p.stat().st_ino, p.stat().st_mode, p.read_bytes())
                for p in (config, user / "SKILL.md")}
        self.call()
        self.call("remove")
        self.assertEqual(keep, {p: (p.stat().st_ino, p.stat().st_mode, p.read_bytes()) for p in keep})

    def test_installed_file_edits_extra_files_modes_and_links_refuse(self):
        self.call()
        file = self.root / "kilix/SKILL.md"
        original = file.read_bytes()
        actions = ("bytes", "extra", "mode", "link", "hardlink", "directory-mode")
        for action in actions:
            with self.subTest(action=action):
                extra = self.root / "kilix/user.txt"
                if action == "bytes":
                    file.write_bytes(original + b"user")
                elif action == "extra":
                    extra.write_text("user")
                elif action == "mode":
                    file.chmod(0o644)
                elif action == "link":
                    extra.symlink_to(self.source)
                elif action == "hardlink":
                    os.link(file, self.base / "user-link")
                else:
                    (self.root / "kilix").chmod(0o755)
                before = self.snapshot()
                for operation in ("status", "install", "remove"):
                    with self.assertRaises(skills.Conflict):
                        self.call(operation)
                    self.assertEqual(self.snapshot(), before)
                if action == "bytes":
                    file.write_bytes(original)
                elif action in ("extra", "link"):
                    extra.unlink()
                elif action == "mode":
                    file.chmod(0o600)
                elif action == "hardlink":
                    (self.base / "user-link").unlink()
                else:
                    (self.root / "kilix").chmod(0o700)

    def test_replaced_even_identical_registration_is_not_owned(self):
        self.call()
        entry = self.root / "kilix"
        target = os.readlink(entry)
        entry.rename(self.base / "original-link")
        entry.symlink_to(target)
        before = self.snapshot()
        for action in ("status", "install", "remove"):
            with self.assertRaises(skills.Conflict):
                self.call(action)
        self.assertEqual(self.snapshot(), before)

    def test_symlink_or_shared_ancestor_refuses_without_touching_target(self):
        outside = self.base / "outside"
        outside.mkdir()
        (self.home / ".agents").symlink_to(outside)
        with self.assertRaises(OSError):
            self.call()
        self.assertEqual(list(outside.iterdir()), [])
        (self.home / ".agents").unlink()
        (self.home / ".agents").mkdir()
        (self.home / ".agents").chmod(0o777)
        with self.assertRaises(skills.Conflict):
            self.call()
        self.assertFalse(self.root.exists())

    def test_failed_preparation_preserves_prior_set_and_no_discovery_on_first_install(self):
        write = skills.write_new
        def fail(fd, name, *args):
            if name == "receipt.json":
                raise OSError("injected storage failure")
            return write(fd, name, *args)
        with mock.patch.object(skills, "write_new", side_effect=fail):
            with self.assertRaises(OSError):
                self.call()
        self.assertFalse(any(os.path.lexists(self.root / n) for n in skills.NAMES))
        self.call()
        old = os.readlink(self.store / "current")
        (self.source / "VERSION").write_text("0.2.3\n")
        with mock.patch.object(skills, "write_new", side_effect=fail):
            with self.assertRaises(OSError):
                self.call()
        self.assertEqual(os.readlink(self.store / "current"), old)
        self.assertEqual(self.call("status")["version"], "0.2.2")

    def test_failed_pointer_publication_restores_removed_link_inodes(self):
        self.call()
        before = {n: (self.root / n).lstat().st_ino for n in skills.NAMES}
        with mock.patch.object(skills.os, "replace", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                self.call("remove")
        self.assertEqual(before, {n: (self.root / n).lstat().st_ino for n in skills.NAMES})
        self.assertEqual(self.call("status")["state"], "installed")

    def test_late_foreign_current_entry_is_preserved_on_initial_install(self):
        write = skills.write_new
        def insert(fd, name, *args):
            result = write(fd, name, *args)
            if name == "receipt.json":
                (self.store / "current").write_text("user entry")
            return result
        with mock.patch.object(skills, "write_new", side_effect=insert):
            with self.assertRaisesRegex(skills.Conflict, "appeared during preparation"):
                self.call()
        self.assertEqual((self.store / "current").read_text(), "user entry")
        self.assertFalse(any(os.path.lexists(self.root / n) for n in skills.NAMES))

    def test_relocated_root_is_refused_after_generation_preparation(self):
        self.call()
        write = skills.write_new
        moved = self.home / "relocated"
        def relocate(fd, name, *args):
            result = write(fd, name, *args)
            if not moved.exists():
                self.root.rename(moved)
                self.root.mkdir()
                (self.root / "sentinel").write_text("user")
            return result
        (self.source / "VERSION").write_text("0.2.3\n")
        with mock.patch.object(skills, "write_new", side_effect=relocate):
            with self.assertRaises(skills.Conflict):
                self.call()
        self.assertEqual([p.name for p in self.root.iterdir()], ["sentinel"])
        self.assertTrue((moved / "kilix").is_symlink())

    def test_missing_or_duplicate_receipt_refuses(self):
        self.call()
        receipt = self.store / "current/receipt.json"
        original = receipt.read_bytes()
        for data in (b'{"schema":1,"schema":1}', b'[]', b'{"owners":[[]]}'):
            receipt.write_bytes(data)
            with self.assertRaises(skills.Conflict):
                self.call("status")
        receipt.unlink()
        with self.assertRaises(skills.Conflict):
            self.call("status")
        receipt.write_bytes(original)
        receipt.chmod(0o600)

    def test_incomplete_source_and_source_symlinks_never_register(self):
        skill = self.source / "skills/kilix/SKILL.md"
        skill.unlink()
        with self.assertRaises(skills.Conflict):
            self.call()
        skill.symlink_to(self.source / "VERSION")
        with self.assertRaises(skills.Conflict):
            self.call()
        self.assertEqual(self.snapshot(), {})

    def test_cli_json_mutations_require_agent_and_failures_are_machine_readable(self):
        for action in ("install", "remove"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                skills.main([action, "--json"])
            self.assertEqual(caught.exception.code, 2)
        with mock.patch.object(skills, "operate", side_effect=skills.Conflict("user edit")):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = skills.main(["status", "--agent", "codex", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out.getvalue())[0]["state"], "conflict")

    def test_real_wrapper_dispatch_works_without_catalog_or_paid_sessions(self):
        # A source package with no submodules, exactly the new dispatch boundary.
        wrapper = self.base / "wrapper"
        wrapper.mkdir()
        shutil.copy2(ROOT / "kilix", wrapper / "kilix")
        shutil.copytree(ROOT / "skills", wrapper / "skills")
        shutil.copy2(ROOT / "VERSION", wrapper / "VERSION")
        (wrapper / "config").mkdir()
        shutil.copy2(ROOT / "config/agent_skills.py", wrapper / "config/agent_skills.py")
        (wrapper / "config/agent_control.py").write_text(
            "import json,sys; print(json.dumps(sys.argv[1:]))\n")
        env = {"HOME": str(self.home), "PATH": os.environ["PATH"], "LANG": "C.UTF-8"}
        for args in (("skills", "list", "--json"),
                     ("skills", "install", "--agent", "codex", "--json"),
                     ("skills", "status", "--agent", "codex", "--json"),
                     ("skills", "remove", "--agent", "codex", "--json"),
                     ("agent-control", "--help", "space and café")):
            run = subprocess.run([str(wrapper / "kilix"), *args], env=env,
                                 capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            data = json.loads(run.stdout)
            if args[0] == "agent-control":
                self.assertEqual(data, ["--help", "space and café"])
            else:
                self.assertTrue(data)


if __name__ == "__main__":
    unittest.main()
