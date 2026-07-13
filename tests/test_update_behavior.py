import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def git(*args, cwd, capture=False):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True,
        capture_output=capture,
    )


class UpdateBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.remote = root / "remote.git"
        self.seed = root / "seed"
        self.checkout = root / "checkout"
        self.config = root / "config"
        self.config.mkdir()
        git("init", "--bare", "--initial-branch=main", str(self.remote), cwd=root)
        git("init", "-b", "main", str(self.seed), cwd=root)
        git("config", "user.email", "test@example.invalid", cwd=self.seed)
        git("config", "user.name", "Kilix Test", cwd=self.seed)
        shutil.copy2(ROOT / "kilix", self.seed / "kilix")
        shutil.copy2(ROOT / "VERSION", self.seed / "VERSION")
        (self.seed / "build.sh").write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            # kilix invokes build.sh from the caller's cwd; anchor to this
            # checkout so the simulated failure can never touch other trees.
            "cd \"$(dirname \"$0\")\"\n"
            "[ -z \"${FAKE_KILIX_BUILD_CALLS:-}\" ] || "
            "echo called >>\"$FAKE_KILIX_BUILD_CALLS\"\n"
            "if [ \"${FAKE_KILIX_BUILD_FAIL:-0}\" = 1 ]; then\n"
            "  printf broken > src/kitty/launcher/kitty\n"
            "  printf broken > src/kitty/fast_data_types.so\n"
            "  exit 23\n"
            "fi\n")
        (self.seed / "build.sh").chmod(0o755)
        (self.seed / ".gitignore").write_text("src/\n")
        git("add", "kilix", "VERSION", "build.sh", ".gitignore", cwd=self.seed)
        git("commit", "-m", "initial", cwd=self.seed)
        git("remote", "add", "origin", str(self.remote), cwd=self.seed)
        git("push", "-u", "origin", "main", cwd=self.seed)
        git("clone", str(self.remote), str(self.checkout), cwd=root)
        self.env = dict(os.environ)
        self.env.update({
            "KILIX_REPO": str(self.remote),
            "KILIX_CONFIG_DIRECTORY": str(self.config),
            "HOME": str(root / "home"),
        })

    def tearDown(self):
        self.temp.cleanup()

    def _update(self, **extra):
        env = dict(self.env)
        env.update(extra)
        return subprocess.run(
            [str(self.checkout / "kilix"), "update"], env=env,
            capture_output=True, text=True,
        )

    def _publish(self, name="next"):
        marker = self.seed / f"{name}.txt"
        marker.write_text(name)
        git("add", marker.name, cwd=self.seed)
        git("commit", "-m", name, cwd=self.seed)
        git("push", "origin", "main", cwd=self.seed)
        return git("rev-parse", "HEAD", cwd=self.seed, capture=True).stdout.strip()

    def test_clean_origin_branch_fast_forwards(self):
        target = self._publish()
        result = self._update()
        self.assertEqual(result.returncode, 0, result.stderr)
        head = git("rev-parse", "HEAD", cwd=self.checkout,
                   capture=True).stdout.strip()
        self.assertEqual(head, target)

    def test_modified_checkout_is_refused(self):
        (self.checkout / "VERSION").write_text("locally changed\n")
        result = self._update()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to overwrite a modified checkout", result.stderr)

    def test_unexpected_origin_is_refused(self):
        result = self._update(KILIX_REPO="https://example.invalid/not-origin")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("origin is", result.stderr)

    def test_legacy_tracked_config_is_migrated_and_checkout_cleaned(self):
        config = self.seed / "config"
        config.mkdir()
        (config / "kitty.conf").write_text("font_size 11\n")
        (config / "kilix.env").write_text("# defaults\n")
        git("add", "config", cwd=self.seed)
        git("commit", "-m", "config defaults", cwd=self.seed)
        git("push", "origin", "main", cwd=self.seed)
        synced = self._update()
        self.assertEqual(synced.returncode, 0, synced.stderr)

        tracked = self.checkout / "config" / "kitty.conf"
        tracked.write_text("font_size 17\n# legacy user setting\n")
        env = dict(self.env)
        env.pop("KILIX_CONFIG_DIRECTORY", None)
        env["XDG_CONFIG_HOME"] = str(Path(self.temp.name) / "xdg")
        result = subprocess.run(
            [str(self.checkout / "kilix"), "update"], env=env,
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(tracked.read_text(), "font_size 11\n")
        self.assertEqual(
            git("status", "--porcelain", "--untracked-files=no",
                cwd=self.checkout, capture=True).stdout, "")
        user = Path(env["XDG_CONFIG_HOME"]) / "kilix" / "kitty.conf"
        self.assertIn("font_size 17", user.read_text())
        self.assertIn("include .kilix-defaults.conf", user.read_text())

    def test_exact_ref_is_fetched_and_checked_out_detached(self):
        target = self._publish("pinned")
        result = self._update(KILIX_REF=target)
        self.assertEqual(result.returncode, 0, result.stderr)
        head = git("rev-parse", "HEAD", cwd=self.checkout,
                   capture=True).stdout.strip()
        self.assertEqual(head, target)
        branch = subprocess.run(
            ["git", "symbolic-ref", "--quiet", "HEAD"], cwd=self.checkout)
        self.assertNotEqual(branch.returncode, 0)

    def test_submodule_failure_rolls_top_level_sources_back(self):
        before = git("rev-parse", "HEAD", cwd=self.checkout,
                     capture=True).stdout.strip()
        self._publish("submodule-change")
        bindir = Path(self.temp.name) / "bin"
        bindir.mkdir()
        state = Path(self.temp.name) / "submodule-failed-once"
        wrapper = bindir / "git"
        real_git = shutil.which("git")
        wrapper.write_text(
            "#!/bin/sh\n"
            f"case \"$*\" in *'submodule update'*) "
            f"if [ ! -e {state!s} ]; then : > {state!s}; exit 77; fi;; esac\n"
            f"exec {real_git} \"$@\"\n")
        wrapper.chmod(0o755)
        env = dict(self.env)
        env["PATH"] = str(bindir) + os.pathsep + env["PATH"]
        result = subprocess.run(
            [str(self.checkout / "kilix"), "update"], env=env,
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("submodule update failed; rolling sources back",
                      result.stderr)
        self.assertEqual(
            git("rev-parse", "HEAD", cwd=self.checkout,
                capture=True).stdout.strip(), before)

    def test_fork_rebuild_failure_is_reported_nonzero(self):
        fork = self.checkout / "src" / "kitty" / "launcher" / "kitty"
        fork.parent.mkdir(parents=True)
        fork.write_text("#!/bin/sh\necho original\n")
        fork.chmod(0o755)
        extension = fork.parents[1] / "fast_data_types.so"
        extension.write_text("original-extension")
        before = git("rev-parse", "HEAD", cwd=self.checkout,
                     capture=True).stdout.strip()
        target = self._publish("engine-change")
        result = self._update(FAKE_KILIX_BUILD_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fork rebuild failed", result.stderr)
        self.assertIn("rolling sources and engine back", result.stderr)
        self.assertEqual(
            git("rev-parse", "HEAD", cwd=self.checkout,
                capture=True).stdout.strip(), before)
        self.assertIn("original", fork.read_text())
        self.assertEqual(extension.read_text(), "original-extension")

        # The failed target remains retryable; a successful rebuild advances it.
        retry = self._update()
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(
            git("rev-parse", "HEAD", cwd=self.checkout,
                capture=True).stdout.strip(), target)

    def test_missing_build_stamp_rebuilds_once_without_source_change(self):
        src = self.checkout / "src"
        src.mkdir()
        git("init", "-b", "main", cwd=src)
        git("config", "user.email", "test@example.invalid", cwd=src)
        git("config", "user.name", "Kilix Test", cwd=src)
        fork = src / "kitty" / "launcher" / "kitty"
        fork.parent.mkdir(parents=True)
        fork.write_text("#!/bin/sh\nexit 0\n")
        fork.chmod(0o755)
        (src / "source.txt").write_text("source\n")
        git("add", "source.txt", cwd=src)
        git("commit", "-m", "source", cwd=src)
        calls = Path(self.temp.name) / "build-calls"
        env = dict(self.env)
        env["XDG_STATE_HOME"] = str(Path(self.temp.name) / "state")
        env["FAKE_KILIX_BUILD_CALLS"] = str(calls)

        first = subprocess.run(
            [str(self.checkout / "kilix"), "update"], env=env,
            capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        second = subprocess.run(
            [str(self.checkout / "kilix"), "update"], env=env,
            capture_output=True, text=True)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(calls.read_text().splitlines(), ["called"])
        head = git("rev-parse", "HEAD", cwd=src, capture=True).stdout.strip()
        stamp = Path(env["XDG_STATE_HOME"]) / "kilix" / "fork-built-ref"
        self.assertEqual(stamp.read_text().strip(), f"{self.checkout}\t{head}")


if __name__ == "__main__":
    unittest.main()
