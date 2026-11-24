"""The host-portability shell helpers: one block, three copies, same answers.

scripts/portable.sh is the canonical copy. build.sh and kilix carry the same
block inline because each must work when copied on its own. These tests keep
the copies identical and check each helper against Python's own view of the
file system, on whichever host runs them (GNU userland on Linux, BSD userland
on FreeBSD).
"""
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "# --- Host portability ---"
END = "# " + "-" * 75 + "\n"


def block(path: Path) -> str:
    text = path.read_text()
    start = text.index(MARKER)
    return text[start:text.index(END, start) + len(END)]


def helper(snippet: str, *args: str) -> subprocess.CompletedProcess:
    script = f'. "{ROOT}/scripts/portable.sh"\n{snippet}\n'
    return subprocess.run(["bash", "-c", script, "helper", *args],
                          capture_output=True, text=True, timeout=30)


class CopiesAgree(unittest.TestCase):
    def test_build_and_kilix_carry_the_canonical_block(self):
        canonical = block(ROOT / "scripts" / "portable.sh")
        for name in ("build.sh", "kilix"):
            with self.subTest(name=name):
                self.assertEqual(block(ROOT / name), canonical,
                                 f"{name}'s portability block differs from scripts/portable.sh")

    def test_no_gnu_only_calls_outside_the_block(self):
        forbidden = ("stat -c", "stat -Lc", "realpath -m", "mv -T", "date -d",
                     "sha256sum -c --status", "/proc/$$/fd")
        for name in ("build.sh", "kilix"):
            text = (ROOT / name).read_text()
            text = text.replace(block(ROOT / name), "")
            for pattern in forbidden:
                with self.subTest(name=name, pattern=pattern):
                    self.assertNotIn(pattern, text)


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class HelpersAnswerLikePython(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_ok(self, snippet, *args):
        result = helper(snippet, *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_stat_helpers(self):
        path = self.tmp / "file"
        path.write_bytes(b"12345")
        for mode in (0o600, 0o644, 0o4755):
            os.chmod(path, mode)
            info = os.lstat(path)
            with self.subTest(mode=oct(mode)):
                self.assertEqual(self.run_ok('kilix_stat_mode "$1"', str(path)),
                                 format(stat.S_IMODE(info.st_mode), "o"))
        # FreeBSD lets only root set the sticky bit on a regular file.
        sticky = self.tmp / "sticky"
        sticky.mkdir()
        os.chmod(sticky, 0o1777)
        self.assertEqual(self.run_ok('kilix_stat_mode "$1"', str(sticky)), "1777")
        os.chmod(path, 0o600)
        info = os.lstat(path)
        self.assertEqual(self.run_ok('kilix_stat_uid "$1"', str(path)), str(info.st_uid))
        self.assertEqual(self.run_ok('kilix_stat_meta "$1"', str(path)),
                         f"{info.st_uid}:600:{info.st_nlink}")
        self.assertEqual(self.run_ok('kilix_stat_size "$1"', str(path)), "5")
        self.assertEqual(self.run_ok('kilix_stat_identity "$1"', str(path)),
                         f"{info.st_dev}:{info.st_ino}")

    def test_symlink_follow_flags(self):
        target = self.tmp / "target"
        target.write_text("x")
        os.chmod(target, 0o640)
        link = self.tmp / "link"
        link.symlink_to(target)
        info = os.stat(target)
        self.assertEqual(self.run_ok('kilix_stat_identity -L "$1"', str(link)),
                         f"{info.st_dev}:{info.st_ino}")
        self.assertEqual(self.run_ok('kilix_stat_mode -L "$1"', str(link)), "640")
        linfo = os.lstat(link)
        self.assertEqual(self.run_ok('kilix_stat_identity "$1"', str(link)),
                         f"{linfo.st_dev}:{linfo.st_ino}")

    def test_type_identity_distinguishes_entries(self):
        (self.tmp / "f").write_text("x")
        (self.tmp / "d").mkdir()
        (self.tmp / "l").symlink_to("f")
        values = {name: self.run_ok('kilix_stat_type_identity "$1"', str(self.tmp / name))
                  for name in ("f", "d", "l")}
        self.assertEqual(len({v.split("\t")[0] for v in values.values()}), 3, values)

    def test_realpath_m(self):
        real = self.tmp / "real"
        (real / "sub").mkdir(parents=True)
        (self.tmp / "alias").symlink_to(real)
        resolved = os.path.realpath(real)
        cases = {
            str(self.tmp / "alias" / "sub"): f"{resolved}/sub",
            str(self.tmp / "alias" / "missing" / "deeper"): f"{resolved}/missing/deeper",
            str(self.tmp / "alias" / "sub" / ".." / "x"): f"{resolved}/x",
            "/": "/",
            "/nonexistent-kilix-root/a/./b": "/nonexistent-kilix-root/a/b",
        }
        for given, expected in cases.items():
            with self.subTest(given=given):
                self.assertEqual(self.run_ok('kilix_realpath_m "$1"', given), expected)

    def test_mv_onto_replaces_symlink_not_its_directory(self):
        (self.tmp / "gen-a").mkdir()
        (self.tmp / "gen-b").mkdir()
        current = self.tmp / "current"
        current.symlink_to("gen-a")
        staged = self.tmp / ".current.new"
        staged.symlink_to("gen-b")
        self.run_ok('kilix_mv_onto -f "$1" "$2"', str(staged), str(current))
        self.assertEqual(os.readlink(current), "gen-b")
        self.assertEqual(list((self.tmp / "gen-a").iterdir()), [])

    def test_mv_onto_refuses_a_real_directory(self):
        (self.tmp / "dest").mkdir()
        source = self.tmp / "src"
        source.write_text("x")
        result = helper('kilix_mv_onto -f "$1" "$2"', str(source), str(self.tmp / "dest"))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(source.exists())
        self.assertEqual(list((self.tmp / "dest").iterdir()), [])

    def test_sha256(self):
        path = self.tmp / "data"
        path.write_bytes(b"kilix")
        import hashlib
        self.assertEqual(self.run_ok('kilix_sha256 "$1"', str(path)),
                         hashlib.sha256(b"kilix").hexdigest())

    def test_lock_fd_excludes_a_second_holder(self):
        lock = self.tmp / "lock"
        lock.touch()
        snippet = ('exec 9>"$1"; kilix_lock_fd 9 || exit 3; '
                   'if ( exec 8>"$1"; kilix_lock_fd_try 8 ); then exit 4; fi; '
                   'kilix_fd_is_open 9 || exit 5; '
                   '[ "$(kilix_fd_identity 9)" = "$(kilix_stat_identity "$1")" ] || exit 6')
        result = helper(snippet, str(lock))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fd_is_open_reports_closed_descriptors(self):
        self.assertNotEqual(helper("exec 9>&-; kilix_fd_is_open 9").returncode, 0)

    def test_process_start_token_is_stable_and_refuses_the_dead(self):
        sleeper = subprocess.Popen(["sleep", "30"])
        self.addCleanup(sleeper.kill)
        first = self.run_ok('kilix_process_start_token "$1"', str(sleeper.pid))
        second = self.run_ok('kilix_process_start_token "$1"', str(sleeper.pid))
        self.assertTrue(first)
        self.assertEqual(first, second)
        sleeper.kill()
        sleeper.wait()
        self.assertNotEqual(helper('kilix_process_start_token "$1"', str(sleeper.pid)).returncode, 0)

    def test_process_executables_lists_a_live_process(self):
        sleeper = subprocess.Popen([shutil.which("sleep"), "30"])
        self.addCleanup(sleeper.kill)
        listed = self.run_ok("kilix_process_executables").splitlines()
        self.assertIn(os.path.realpath(shutil.which("sleep")), listed)

    def test_format_epoch(self):
        self.assertEqual(self.run_ok('TZ=UTC kilix_format_epoch 0 "+%Y-%m-%d %H:%M:%S"'),
                         "1970-01-01 00:00:00")

    def test_unix_socket_path_max(self):
        expected = "108" if os.uname().sysname == "Linux" else "104"
        self.assertEqual(self.run_ok("kilix_unix_socket_path_max"), expected)


if __name__ == "__main__":
    unittest.main()
