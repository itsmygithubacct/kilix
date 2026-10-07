"""Regression tests for the A2 round-6 review (reviews/A2-r6/REVIEW.md): N7 and N8.

N7: the multiplexer build test gives every class its own fixture directory (made with mkdtemp below a
verified base), never follows a symlink at a shared name, and removes only what it created. N8: every
repeated --timeout is validated, by the launcher and by the helper alike. Each fails on fcf4782f.
"""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
from test_pty_cli import LAUNCHER, PtyCliCase  # noqa: E402
import test_multiplexer_build as multiplexer  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "config" / "kilix_pty.py"
CASE = "test_missing_or_mutated_package_refuses_without_disabled_build"
SEQUENCES = [("nan", "1"), ("abc", "1"), ("60.01", "1"), ("0", "1"), ("", "1"), ("1", "nan"), ("1", "0.1"),
             ("60", "2.5"), ("2.5", "60"), ("1", "abc", "2"), ("1", "2", "3"), ("0.1", "0.1")]


class RepeatedTimeoutTests(PtyCliCase):
    def run_both(self, values):
        flags = [item for value in values for item in ("--timeout", value)]
        public = subprocess.run(["bash", str(LAUNCHER), "pty", *flags, "capabilities", "--json"],
                                env=self.env(), capture_output=True, text=True, timeout=30,
                                stdin=subprocess.DEVNULL)
        direct = subprocess.run(["python3", str(HELPER), "--runtime", str(self.runtime), *flags,
                                 "capabilities", "--json"], env=self.env(), capture_output=True, text=True,
                                timeout=30, stdin=subprocess.DEVNULL)
        return public, direct

    def test_n8_any_invalid_occurrence_is_refused_and_all_valid_means_the_last_wins(self):
        for values in SEQUENCES:
            with self.subTest(values=values):
                public, direct = self.run_both(values)
                valid = all(multiplexer_valid(value) for value in values)
                if valid:
                    for result in (public, direct):
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(json.loads(result.stdout)["timeout_seconds"], float(values[-1]))
                else:
                    self.assertEqual((public.returncode, direct.returncode), (2, 2), public.stderr + direct.stderr)
                    for result in (public, direct):
                        self.assertNotIn("Traceback", result.stderr)
                        self.assertEqual(result.stdout, "")
                self.assertEqual(public.returncode, direct.returncode, "launcher and helper must agree")


def multiplexer_valid(value):
    import kilix_pty
    return kilix_pty.valid_timeout(value)


def planted(base):
    """A base directory and the fixture settings that point the test at it (and nowhere else)."""
    checkout = base / "checkout"
    checkout.mkdir()
    alternative = base / "alternative"
    alternative.mkdir(mode=0o700)
    original = multiplexer.quiet_fixture_parent
    return checkout / ".test-tmp", alternative, [
        mock.patch.object(multiplexer, "quiet_fixture_parent", lambda: original(window=0))]


class Fixtures:
    """Two classes of the real fixture test, each allocating through the real selection code."""

    def __init__(self, test, candidate, alternative, extra=()):
        class First(multiplexer.MultiplexerBuildTests):
            pass

        class Second(multiplexer.MultiplexerBuildTests):
            pass
        self.First, self.Second = First, Second
        original = multiplexer.quiet_fixture_parent
        self.patches = [mock.patch.object(multiplexer, "FIXTURE_PARENT", candidate),
                        mock.patch.object(multiplexer, "ALTERNATIVE_PARENTS", (alternative,)),
                        mock.patch.object(multiplexer, "quiet_fixture_parent", lambda: original(window=0)), *extra]
        self.test = test

    def __enter__(self):
        for patch in self.patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in reversed(self.patches):
            patch.stop()

    def case(self, cls):
        case = cls(CASE)
        case.setUp()
        return case


class FixtureNamespaceTests(unittest.TestCase):
    def setUp(self):
        self.private = tempfile.TemporaryDirectory(prefix="a2r6-fixtures.")
        self.addCleanup(self.private.cleanup)
        self.base = Path(self.private.name)
        self.candidate, self.alternative, _ = planted(self.base)
        self.target = self.base / "unrelated"
        self.target.mkdir()

    def test_n7_a_symlink_at_the_checkouts_candidate_is_never_used(self):
        self.candidate.symlink_to(self.target, target_is_directory=True)
        with Fixtures(self, self.candidate, self.alternative) as f:
            f.First.setUpClass()
            case = f.case(f.First)
            try:
                self.assertEqual(f.First.fixture_parent.parent, self.alternative)
                self.assertEqual(list(self.target.iterdir()), [], "a fixture was written through the link")
            finally:
                case.doCleanups()
                f.First.doClassCleanups()
        self.assertTrue(self.candidate.is_symlink(), "the link itself is not ours to remove")
        self.assertEqual(list(self.target.iterdir()), [])

    def test_n7_a_symlink_at_the_old_shared_name_is_ignored(self):
        self.candidate.parent.chmod(0o500)     # the checkout candidate cannot be made, so the alternative is used
        self.addCleanup(self.candidate.parent.chmod, 0o700)
        (self.alternative / f".kilix-test-{os.getuid()}").symlink_to(self.target, target_is_directory=True)
        with Fixtures(self, self.candidate, self.alternative) as f:
            f.First.setUpClass()
            case = f.case(f.First)
            try:
                self.assertEqual(case.root.resolve().parent.parent, self.alternative.resolve())
                self.assertEqual(list(self.target.iterdir()), [])
            finally:
                case.doCleanups()
                f.First.doClassCleanups()
        self.assertEqual(list(self.target.iterdir()), [])

    def test_n7_each_class_has_its_own_directory_and_cleanup_touches_only_that(self):
        with Fixtures(self, self.candidate, self.alternative) as f:
            f.First.setUpClass()
            f.Second.setUpClass()
            first = second = None
            try:
                self.assertNotEqual(f.First.fixture_parent, f.Second.fixture_parent)
                first, second = f.case(f.First), f.case(f.Second)
                first.doCleanups()
                f.First.doClassCleanups()
                self.assertFalse(f.First.fixture_parent.exists(), "a class removes what it made")
                self.assertTrue(second.trace.exists(), "another class's live fixture was deleted")
                second.doCleanups()
                f.Second.doClassCleanups()
                self.assertFalse(f.Second.fixture_parent.exists())
                self.assertEqual(list(self.alternative.iterdir()), [])
            finally:
                for case in (first, second):
                    if case:
                        case.doCleanups()
                f.First.doClassCleanups()
                f.Second.doClassCleanups()

    def test_n7_a_link_planted_where_a_finished_class_was_does_not_redirect_the_next(self):
        with Fixtures(self, self.candidate, self.alternative) as f:
            f.First.setUpClass()
            old = f.First.fixture_parent
            f.First.doClassCleanups()
            old.symlink_to(self.target, target_is_directory=True)
            f.Second.setUpClass()
            case = f.case(f.Second)
            try:
                self.assertNotEqual(f.Second.fixture_parent, old)
                self.assertEqual(list(self.target.iterdir()), [])
            finally:
                case.doCleanups()
                f.Second.doClassCleanups()

    def test_n7_a_retargeted_run_directory_is_detected_and_no_peer_fixture_is_deleted(self):
        with Fixtures(self, self.candidate, self.alternative) as f:
            f.First.setUpClass()
            case = f.case(f.First)
            run = f.First.fixture_parent
            moved = self.base / "moved-away"
            run.rename(moved)
            peer_parent = self.base / "peer"
            peer_parent.mkdir()
            peer = peer_parent / case.root.name
            peer.mkdir()
            sentinel = peer / "sentinel"
            sentinel.write_text("a peer's fixture")
            run.symlink_to(peer_parent, target_is_directory=True)
            with self.assertRaises(multiplexer.FixtureTampered):
                case.remove_fixture()
            f.First.doClassCleanups()
            self.assertTrue(sentinel.exists(), "a peer fixture was deleted through the replaced parent")
            self.assertTrue(run.is_symlink(), "the replacement link is not ours to remove")
            self.assertTrue((moved / case.root.name / "source" / "main.c").exists())

    def test_n7_a_run_directory_cannot_be_used_after_it_is_replaced(self):
        with Fixtures(self, self.candidate, self.alternative) as f:
            f.First.setUpClass()
            run = f.First.fixture_parent
            run.rename(self.base / "moved-away")
            run.symlink_to(self.target, target_is_directory=True)
            with self.assertRaises(multiplexer.FixtureTampered):
                f.case(f.First)
            self.assertEqual(list(self.target.iterdir()), [])
            f.First.doClassCleanups()
            self.assertEqual(list(self.target.iterdir()), [])


class VerifiedBaseTests(unittest.TestCase):
    def setUp(self):
        self.private = tempfile.TemporaryDirectory(prefix="a2r6-bases.")
        self.addCleanup(self.private.cleanup)
        self.base = Path(self.private.name)

    def test_n7_only_a_real_closed_directory_is_a_base(self):
        good = self.base / "good"
        good.mkdir(mode=0o700)
        self.assertTrue(multiplexer.verified_base(good, True))
        self.assertTrue(multiplexer.verified_base(good, False))
        link = self.base / "link"
        link.symlink_to(good, target_is_directory=True)
        self.assertFalse(multiplexer.verified_base(link, True))
        self.assertFalse(multiplexer.verified_base(link, False))
        plain = self.base / "plain"
        plain.write_text("x")
        self.assertFalse(multiplexer.verified_base(plain, False))
        self.assertFalse(multiplexer.verified_base(self.base / "missing", False))

    def test_n7_a_directory_open_to_others_is_refused_unless_sticky_and_a_private_one_must_be_closed(self):
        open_dir = self.base / "open"
        open_dir.mkdir()
        os.chmod(open_dir, 0o777)
        self.assertFalse(multiplexer.verified_base(open_dir, False))
        self.assertFalse(multiplexer.verified_base(open_dir, True))
        os.chmod(open_dir, 0o1777)
        self.assertTrue(multiplexer.verified_base(open_dir, False), "a sticky shared directory is fine")
        self.assertFalse(multiplexer.verified_base(open_dir, True), "our own directory must not be open")
        group = self.base / "group"
        group.mkdir()
        os.chmod(group, 0o770)
        self.assertFalse(multiplexer.verified_base(group, True))

    def test_n7_the_real_system_bases_pass(self):
        for path in (Path("/var/tmp"), Path("/dev/shm")):
            if path.is_dir():
                self.assertTrue(multiplexer.verified_base(path, False), str(path))


class ProcessTests(unittest.TestCase):
    def test_n7_two_suite_processes_keep_each_others_fixtures(self):
        with tempfile.TemporaryDirectory(prefix="a2r6-procs.") as private:
            alternative = Path(private) / "alternative"
            alternative.mkdir(mode=0o700)
            code = '''import pathlib, sys
sys.path.insert(0, sys.argv[1])
import test_multiplexer_build as m
from unittest import mock
alternative = pathlib.Path(sys.argv[2])
original = m.quiet_fixture_parent
class Peer(m.MultiplexerBuildTests):
    pass
with mock.patch.object(m, "FIXTURE_PARENT", alternative.parent / "absent" / ".test-tmp"), \\
        mock.patch.object(m, "ALTERNATIVE_PARENTS", (alternative,)), \\
        mock.patch.object(m, "quiet_fixture_parent", lambda: original(window=0)):
    Peer.setUpClass()
    case = Peer(sys.argv[3])
    case.setUp()
    print(case.root, flush=True)
    sys.stdin.readline()
    assert case.trace.exists(), "a peer removed the live fixture"
    case.doCleanups()
    Peer.doClassCleanups()
'''
            children = []
            try:
                for _ in range(2):
                    child = subprocess.Popen([sys.executable, "-c", code, str(ROOT / "tests"), str(alternative), CASE],
                                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                             text=True)
                    children.append(child)
                    self.assertTrue(Path(child.stdout.readline().strip()).is_dir())
                children[0].stdin.write("finish\n")
                children[0].stdin.flush()
                _, err = children[0].communicate(timeout=30)
                self.assertEqual(children[0].returncode, 0, err)
                self.assertEqual(len(list(alternative.iterdir())), 1, "only the first process's directory is gone")
                children[1].stdin.write("finish\n")
                children[1].stdin.flush()
                _, err = children[1].communicate(timeout=30)
                self.assertEqual(children[1].returncode, 0, err)
                self.assertEqual(list(alternative.iterdir()), [])
            finally:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                    child.wait()


if __name__ == "__main__":
    unittest.main()
