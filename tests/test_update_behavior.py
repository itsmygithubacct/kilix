import os
import shutil
import stat
import subprocess
import tempfile
import time
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
        self.src_remote = root / "src-remote.git"
        self.src_seed = root / "src-seed"
        self.checkout = root / "checkout"
        self.config = root / "config"
        self.storage = root / "storage"
        self.calls = root / "build-calls"
        self.config.mkdir()

        git("init", "--bare", "--initial-branch=main", str(self.src_remote),
            cwd=root)
        git("init", "-b", "main", str(self.src_seed), cwd=root)
        git("config", "user.email", "test@example.invalid", cwd=self.src_seed)
        git("config", "user.name", "Kilix Test", cwd=self.src_seed)
        (self.src_seed / "source.txt").write_text("source 0\n")
        git("add", "source.txt", cwd=self.src_seed)
        git("commit", "-m", "source 0", cwd=self.src_seed)
        git("remote", "add", "origin", str(self.src_remote), cwd=self.src_seed)
        git("push", "-u", "origin", "main", cwd=self.src_seed)

        git("init", "--bare", "--initial-branch=main", str(self.remote),
            cwd=root)
        git("init", "-b", "main", str(self.seed), cwd=root)
        git("config", "user.email", "test@example.invalid", cwd=self.seed)
        git("config", "user.name", "Kilix Test", cwd=self.seed)
        shutil.copy2(ROOT / "kilix", self.seed / "kilix")
        shutil.copy2(ROOT / "kilix-settings", self.seed / "kilix-settings")
        shutil.copytree(
            ROOT / "config" / "kilix_sdk",
            self.seed / "config" / "kilix_sdk",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        shutil.copy2(ROOT / "VERSION", self.seed / "VERSION")
        (self.seed / "build.sh").write_text(self._fake_builder())
        (self.seed / "build.sh").chmod(0o755)
        git("-c", "protocol.file.allow=always", "submodule", "add",
            str(self.src_remote), "src", cwd=self.seed)
        git("add", "kilix", "kilix-settings", "config/kilix_sdk", "VERSION",
            "build.sh", ".gitmodules", "src", cwd=self.seed)
        git("commit", "-m", "initial", cwd=self.seed)
        git("remote", "add", "origin", str(self.remote), cwd=self.seed)
        git("push", "-u", "origin", "main", cwd=self.seed)
        git("-c", "protocol.file.allow=always", "clone", "--recurse-submodules",
            str(self.remote), str(self.checkout), cwd=root)

        self.bindir = root / "bin"
        self.bindir.mkdir()
        go = self.bindir / "go"
        go.write_text("#!/bin/sh\nexit 0\n")
        go.chmod(0o755)
        self.prebuilt = root / "prebuilt" / "kitty.app" / "bin" / "kitty"
        self.prebuilt.parent.mkdir(parents=True)
        self._write_launcher(self.prebuilt, "prebuilt")
        self.env = dict(os.environ)
        for name in tuple(self.env):
            if name.startswith("KILIX_") or name in (
                    "GPU_TERMINAL_HOME", "GPU_TERMINAL_SETTINGS_FILE"):
                self.env.pop(name)
        self.env.update({
            "GIT_ALLOW_PROTOCOL": "file",
            "KILIX_REPO": str(self.remote),
            "KILIX_CONFIG_DIRECTORY": str(self.config),
            "KILIX_STORAGE_HOME": str(self.storage),
            "KILIX_PREBUILT_HOME": str(self.prebuilt.parents[1]),
            "FAKE_KILIX_BUILD_CALLS": str(self.calls),
            "HOME": str(root / "home"),
            "PATH": str(self.bindir) + os.pathsep + os.environ["PATH"],
        })

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _fake_builder():
        return r'''#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
build=${KILIX_BUILD_DIRECTORY:?}
state=${KILIX_STATE_DIRECTORY:?}
mkdir -p "$build/generations" "$state"
[ -z "${FAKE_KILIX_BUILD_CALLS:-}" ] || echo called >>"$FAKE_KILIX_BUILD_CALLS"
stage=$(mktemp -d "$build/generations/build.XXXXXX")
promoted=0
cleanup_stage() {
  [ "$promoted" = 1 ] || rm -rf -- "$stage"
}
trap cleanup_stage EXIT
launcher="$stage/src/kitty/launcher"
mkdir -p "$launcher"
case "${FAKE_KILIX_BUILD_INVALID:-}" in
  probe_kitty) kitty_rc=42;; *) kitty_rc=0;;
esac
case "${FAKE_KILIX_BUILD_INVALID:-}" in
  probe_kitten) kitten_rc=43;; *) kitten_rc=0;;
esac
printf '#!/bin/sh\necho kitty-test\nexit %s\n' "$kitty_rc" >"$launcher/kitty"
printf '#!/bin/sh\necho kitten-test\nexit %s\n' "$kitten_rc" >"$launcher/kitten"
chmod 0700 "$launcher/kitty" "$launcher/kitten"
head=$(git -C src rev-parse HEAD)
case "${FAKE_KILIX_BUILD_INVALID:-}" in
  source_missing) ;;
  source_wrong) printf 'wrong\n' >"$stage/source-id";;
  source_extra) printf '%s\n\n' "$head" >"$stage/source-id";;
  *) printf '%s\n' "$head" >"$stage/source-id";;
esac
if [ "${FAKE_KILIX_BUILD_INVALID:-}" = missing_kitty ]; then rm -f "$launcher/kitty"; fi
if [ "${FAKE_KILIX_BUILD_INVALID:-}" = missing_kitten ]; then rm -f "$launcher/kitten"; fi
if [ "${FAKE_KILIX_BUILD_FAIL:-}" = before ]; then exit 23; fi
rm -rf -- "$build/previous"
if [ -e "$build/current" ] || [ -L "$build/current" ]; then
  mv -- "$build/current" "$build/previous"
fi
target="generations/${stage##*/}"
ln -s "$target" "$build/.current.$$"
mv -Tf -- "$build/.current.$$" "$build/current"
promoted=1
if [ "${FAKE_KILIX_BUILD_FAIL:-}" = after_current ]; then exit 24; fi
case "${FAKE_KILIX_BUILD_INVALID:-}" in
  stamp_missing) rm -f -- "$state/fork-built-ref";;
  stamp_wrong) printf 'wrong\n' >"$state/fork-built-ref.tmp";;
  stamp_extra) printf '%s\t%s\n\n' "$(pwd -P)" "$head" >"$state/fork-built-ref.tmp";;
  *) printf '%s\t%s\n' "$(pwd -P)" "$head" >"$state/fork-built-ref.tmp";;
esac
if [ "${FAKE_KILIX_BUILD_INVALID:-}" != stamp_missing ]; then
  chmod 0600 "$state/fork-built-ref.tmp"
  mv -Tf -- "$state/fork-built-ref.tmp" "$state/fork-built-ref"
fi
if [ "${FAKE_KILIX_BUILD_FAIL:-}" = after_stamp ]; then exit 25; fi
'''

    @staticmethod
    def _write_launcher(path, label="fork", rc=0):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!/bin/sh\necho {label}-test\nexit {rc}\n")
        path.chmod(0o700)

    def _update(self, **extra):
        env = dict(self.env)
        env.update(extra)
        return subprocess.run(
            [str(self.checkout / "kilix"), "update"], env=env,
            capture_output=True, text=True,
        )

    def _publish(self, name="next"):
        marker = self.seed / f"{name}.txt"
        marker.write_text(name + "\n")
        git("add", marker.name, cwd=self.seed)
        git("commit", "-m", name, cwd=self.seed)
        git("push", "origin", "main", cwd=self.seed)
        return git("rev-parse", "HEAD", cwd=self.seed,
                   capture=True).stdout.strip()

    def _publish_with_src_change(self, name="source-next"):
        marker = self.src_seed / f"{name}.txt"
        marker.write_text(name + "\n")
        git("add", marker.name, cwd=self.src_seed)
        git("commit", "-m", name, cwd=self.src_seed)
        git("push", "origin", "main", cwd=self.src_seed)
        src_head = git("rev-parse", "HEAD", cwd=self.src_seed,
                       capture=True).stdout.strip()
        git("-c", "protocol.file.allow=always", "fetch", "origin", cwd=self.seed / "src")
        git("checkout", src_head, cwd=self.seed / "src")
        git("add", "src", cwd=self.seed)
        git("commit", "-m", f"advance {name}", cwd=self.seed)
        git("push", "origin", "main", cwd=self.seed)
        top_head = git("rev-parse", "HEAD", cwd=self.seed,
                       capture=True).stdout.strip()
        return top_head, src_head

    def _src_head(self):
        return git("rev-parse", "HEAD", cwd=self.checkout / "src",
                   capture=True).stdout.strip()

    def _install_generation(self, name, *, current=True, kitty_rc=0,
                            kitten_rc=0, source_id=None):
        generation = self.storage / "build" / "generations" / f"build.{name}"
        launcher = generation / "src" / "kitty" / "launcher"
        self._write_launcher(launcher / "kitty", "kitty", kitty_rc)
        self._write_launcher(launcher / "kitten", "kitten", kitten_rc)
        (generation / "source-id").write_text(
            (source_id if source_id is not None else self._src_head()) + "\n")
        link = self.storage / "build" / ("current" if current else "previous")
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() or link.exists():
            if link.is_dir() and not link.is_symlink():
                shutil.rmtree(link)
            else:
                link.unlink()
        link.symlink_to(f"generations/build.{name}")
        return generation, link

    def _write_stamp(self, payload=None):
        stamp = self.storage / "state" / "fork-built-ref"
        stamp.parent.mkdir(parents=True, exist_ok=True)
        if payload is None:
            payload = f"{self.checkout.resolve()}\t{self._src_head()}\n".encode()
        if stamp.is_symlink() or stamp.exists():
            stamp.unlink()
        stamp.write_bytes(payload)
        stamp.chmod(0o600)
        return stamp

    def _install_coherent_fork(self, name="Current"):
        generation, link = self._install_generation(name)
        stamp = self._write_stamp()
        return generation, link, stamp

    @staticmethod
    def _entry_identity(path):
        info = path.lstat()
        target = os.readlink(path) if path.is_symlink() else None
        return stat.S_IFMT(info.st_mode), info.st_dev, info.st_ino, target

    @staticmethod
    def _stamp_snapshot(path):
        info = path.stat()
        return path.read_bytes(), stat.S_IMODE(info.st_mode), info.st_uid, info.st_nlink

    def _clear_storage(self):
        shutil.rmtree(self.storage, ignore_errors=True)
        if self.calls.exists():
            self.calls.unlink()

    def test_clean_origin_branch_fast_forwards_with_prebuilt_only(self):
        target = self._publish()
        result = self._update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.checkout,
                             capture=True).stdout.strip(), target)
        self.assertFalse(self.calls.exists())

    def test_modified_checkout_is_refused(self):
        (self.checkout / "VERSION").write_text("locally changed\n")
        result = self._update()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to overwrite a modified checkout", result.stderr)

    def test_unexpected_origin_is_refused(self):
        result = self._update(KILIX_REPO="https://example.invalid/not-origin")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("origin is", result.stderr)

    def test_legacy_xdg_tree_is_never_moved(self):
        old_root = Path(self.temp.name) / "xdg" / "kilix"
        old_root.mkdir(parents=True)
        sentinel = old_root / "keep-me"
        sentinel.write_text("legacy bytes\n")
        env = dict(self.env)
        env.pop("KILIX_CONFIG_DIRECTORY", None)
        env["XDG_CONFIG_HOME"] = str(old_root.parent)
        result = subprocess.run(
            [str(self.checkout / "kilix"), "screen-size", "show"], env=env,
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sentinel.read_text(), "legacy bytes\n")

    def test_exact_ref_is_fetched_and_checked_out_detached(self):
        target = self._publish("pinned")
        result = self._update(KILIX_REF=target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.checkout,
                             capture=True).stdout.strip(), target)
        branch = subprocess.run(
            ["git", "symbolic-ref", "--quiet", "HEAD"], cwd=self.checkout)
        self.assertNotEqual(branch.returncode, 0)

    def test_submodule_failure_rolls_top_level_sources_back(self):
        before = git("rev-parse", "HEAD", cwd=self.checkout,
                     capture=True).stdout.strip()
        self._publish("submodule-change")
        wrapper = self.bindir / "git"
        state_file = Path(self.temp.name) / "submodule-failed-once"
        real_git = shutil.which("git")
        wrapper.write_text(
            "#!/bin/sh\n"
            f"case \"$*\" in *'submodule update'*) if [ ! -e {state_file} ]; "
            f"then : > {state_file}; exit 77; fi;; esac\nexec {real_git} \"$@\"\n")
        wrapper.chmod(0o755)
        result = self._update()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("submodule update failed", result.stderr)
        self.assertIn("rolling changed state back", result.stderr)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.checkout,
                             capture=True).stdout.strip(), before)

    def test_no_engine_fails_and_restores_changed_sources(self):
        self.prebuilt.unlink()
        before_top = git("rev-parse", "HEAD", cwd=self.checkout,
                         capture=True).stdout.strip()
        before_src = self._src_head()
        self._publish_with_src_change("no-engine")
        result = self._update()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no runnable engine", result.stderr)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.checkout,
                             capture=True).stdout.strip(), before_top)
        self.assertEqual(self._src_head(), before_src)

    def test_coherent_fork_does_not_rebuild(self):
        self._install_coherent_fork()
        result = self._update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.calls.exists())

    def test_each_incoherent_fork_rebuilds_once(self):
        variants = (
            "missing_kitty", "missing_kitten", "source_missing", "source_wrong",
            "source_extra", "stamp_missing", "stamp_wrong", "stamp_extra",
            "probe_kitty", "probe_kitten",
        )
        for variant in variants:
            with self.subTest(variant=variant):
                self._clear_storage()
                generation, _, stamp = self._install_coherent_fork(variant)
                launcher = generation / "src" / "kitty" / "launcher"
                if variant == "missing_kitty":
                    (launcher / "kitty").unlink()
                elif variant == "missing_kitten":
                    (launcher / "kitten").unlink()
                elif variant == "source_missing":
                    (generation / "source-id").unlink()
                elif variant == "source_wrong":
                    (generation / "source-id").write_text("wrong\n")
                elif variant == "source_extra":
                    (generation / "source-id").write_text(self._src_head() + "\n\n")
                elif variant == "stamp_missing":
                    stamp.unlink()
                elif variant == "stamp_wrong":
                    stamp.write_text("wrong\n")
                elif variant == "stamp_extra":
                    stamp.write_bytes(stamp.read_bytes() + b"\n")
                elif variant == "probe_kitty":
                    self._write_launcher(launcher / "kitty", rc=42)
                elif variant == "probe_kitten":
                    self._write_launcher(launcher / "kitten", rc=43)
                first = self._update()
                self.assertEqual(first.returncode, 0, first.stderr)
                second = self._update()
                self.assertEqual(second.returncode, 0, second.stderr)
                self.assertEqual(self.calls.read_text().splitlines(), ["called"])

    def test_launcher_probes_have_term_and_kill_deadlines(self):
        launcher = (ROOT / "kilix").read_text()
        builder = (ROOT / "build.sh").read_text()
        self.assertGreaterEqual(launcher.count("timeout --kill-after=2 15"), 2)
        self.assertIn("timeout --kill-after=2 15", builder)

    def test_unsafe_stamps_are_refused_without_touching_targets(self):
        cases = ("symlink", "mode", "hardlink")
        for case in cases:
            with self.subTest(case=case):
                self._clear_storage()
                self._install_generation(case)
                stamp = self.storage / "state" / "fork-built-ref"
                stamp.parent.mkdir(parents=True)
                sentinel = Path(self.temp.name) / f"sentinel-{case}"
                sentinel.write_text("sentinel\n")
                if case == "symlink":
                    stamp.symlink_to(sentinel)
                else:
                    stamp.write_text("unsafe\n")
                    stamp.chmod(0o644 if case == "mode" else 0o600)
                    if case == "hardlink":
                        os.link(stamp, sentinel.with_suffix(".link"))
                before = sentinel.read_bytes()
                result = self._update()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("fork-build stamp", result.stderr)
                self.assertEqual(sentinel.read_bytes(), before)
                self.assertFalse(self.calls.exists())

    def test_storage_boundaries_are_rejected_before_writes(self):
        home = Path(self.env["HOME"])
        home.mkdir()
        home.chmod(0o755)
        result = self._update(KILIX_STORAGE_HOME=str(home))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("broad or source-tree storage", result.stderr)
        self.assertEqual(stat.S_IMODE(home.stat().st_mode), 0o755)
        self.assertFalse((home / "state").exists())

        escaped_state = Path(self.temp.name) / "escaped-state"
        result = self._update(KILIX_STATE_DIRECTORY=str(escaped_state))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("strict descendant", result.stderr)
        self.assertFalse(escaped_state.exists())

        escaped_build = Path(self.temp.name) / "escaped-build"
        escaped_build.mkdir()
        sentinel = escaped_build / "keep"
        sentinel.write_text("keep\n")
        result = self._update(KILIX_BUILD_DIRECTORY=str(escaped_build))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("strict descendants", result.stderr)
        self.assertEqual(sentinel.read_text(), "keep\n")

        source_storage = self.checkout / "writable-storage"
        result = self._update(
            KILIX_STORAGE_HOME=str(source_storage),
            KILIX_STATE_DIRECTORY=str(source_storage / "state"),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("inside the Kilix source checkout", result.stderr)
        self.assertFalse(source_storage.exists())

    def test_failures_restore_exact_generations_previous_and_stamp(self):
        for failure in ("before", "after_current", "after_stamp"):
            with self.subTest(failure=failure):
                self._clear_storage()
                _, current, stamp = self._install_coherent_fork("OldCurrent")
                old_generation = current.resolve()
                _, previous = self._install_generation("OldPrevious", current=False)
                previous_generation = previous.resolve()
                current_identity = self._entry_identity(current)
                previous_identity = self._entry_identity(previous)
                stamp_snapshot = self._stamp_snapshot(stamp)
                before_top = git("rev-parse", "HEAD", cwd=self.checkout,
                                 capture=True).stdout.strip()
                before_src = self._src_head()
                self._publish_with_src_change(f"failure-{failure}")
                result = self._update(FAKE_KILIX_BUILD_FAIL=failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("rolling changed state back", result.stderr)
                self.assertEqual(self._entry_identity(current), current_identity)
                self.assertEqual(self._entry_identity(previous), previous_identity)
                self.assertTrue(old_generation.is_dir())
                self.assertTrue(previous_generation.is_dir())
                self.assertEqual(self._stamp_snapshot(stamp), stamp_snapshot)
                self.assertEqual(git("rev-parse", "HEAD", cwd=self.checkout,
                                     capture=True).stdout.strip(), before_top)
                self.assertEqual(self._src_head(), before_src)
                self.assertEqual(list((self.storage / "build").glob(
                    ".update-rollback.*")), [])
                names = {p.name for p in (self.storage / "build" / "generations").iterdir()}
                self.assertEqual(names, {"build.OldCurrent", "build.OldPrevious"})

    def test_successful_but_invalid_builds_are_rolled_back(self):
        variants = ("missing_kitten", "source_wrong", "source_extra",
                    "stamp_missing", "stamp_wrong", "stamp_extra", "probe_kitty")
        for variant in variants:
            with self.subTest(variant=variant):
                self._clear_storage()
                _, current, stamp = self._install_coherent_fork("OldCurrent")
                _, previous = self._install_generation("OldPrevious", current=False)
                current_identity = self._entry_identity(current)
                previous_identity = self._entry_identity(previous)
                stamp_snapshot = self._stamp_snapshot(stamp)
                # A safe mismatch triggers a same-source rebuild.
                stamp.write_text("stale\n")
                stamp.chmod(0o600)
                stamp_snapshot = self._stamp_snapshot(stamp)
                result = self._update(FAKE_KILIX_BUILD_INVALID=variant)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("failed verification", result.stderr)
                self.assertEqual(self._entry_identity(current), current_identity)
                self.assertEqual(self._entry_identity(previous), previous_identity)
                self.assertEqual(self._stamp_snapshot(stamp), stamp_snapshot)

    def test_retry_commits_new_generation_and_retires_old_previous(self):
        self._install_coherent_fork("OldCurrent")
        _, previous = self._install_generation("OldPrevious", current=False)
        retired = previous.resolve()
        self._publish_with_src_change("retry")
        failed = self._update(FAKE_KILIX_BUILD_FAIL="after_stamp")
        self.assertNotEqual(failed.returncode, 0)
        retry = self._update()
        self.assertEqual(retry.returncode, 0, retry.stderr)
        current = self.storage / "build" / "current"
        previous = self.storage / "build" / "previous"
        self.assertNotEqual(os.readlink(current), "generations/build.OldCurrent")
        self.assertEqual(os.readlink(previous), "generations/build.OldCurrent")
        self.assertFalse(retired.exists())
        self.assertEqual(self.calls.read_text().splitlines(), ["called", "called"])

    def test_update_retains_live_old_generation_then_reaps_it(self):
        self._install_coherent_fork("OldCurrent")
        _, previous = self._install_generation(
            "LiveOldPrevious", current=False)
        retained = previous.resolve()
        live_executable = retained / "live-kilix"
        shutil.copy2(shutil.which("sleep"), live_executable)
        process = subprocess.Popen([str(live_executable), "30"])
        try:
            for _ in range(100):
                try:
                    running = os.path.realpath(f"/proc/{process.pid}/exe")
                except OSError:
                    running = ""
                if running == str(live_executable.resolve()):
                    break
                time.sleep(0.01)
            else:
                self.fail("test executable did not start from old generation")

            self._publish_with_src_change("live-generation")
            result = self._update()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(retained.is_dir())
            self.assertIn(
                "retaining live build generation", result.stderr)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

        result = self._update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(retained.exists())

    def test_update_preserves_recovery_transaction_generation(self):
        self._install_coherent_fork("Current")
        recovery = (
            self.storage / "build" / "generations" / "build.Recovery")
        recovery.mkdir()
        (recovery / "marker").write_text("rollback data\n")
        transaction = self.storage / "build" / ".update-rollback.Test"
        transaction.mkdir()
        (transaction / "previous.entry").symlink_to(
            "generations/build.Recovery")

        result = self._update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (recovery / "marker").read_text(), "rollback data\n")

        (transaction / "previous.entry").unlink()
        transaction.rmdir()
        result = self._update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(recovery.exists())


if __name__ == "__main__":
    unittest.main()
