"""What installing Kilix's build dependencies does to a Debian machine.

``scripts/install-build-deps.sh`` runs ``sudo apt-get install`` on the owner's
own machine, so the thing that matters is not which names are in its list but
what the transaction does to what is already there. These tests drive the
script's real Debian backend against fixture dpkg roots and let apt's own
resolver answer, in simulation (``apt-get install -s``): nothing is installed,
removed or written outside a temporary directory, and no root is needed.

The invariant asserted is the one a user cares about: **the install removes
nothing** -- ``N to remove`` is 0. No assertion names a package. A test that
pinned a particular JACK package would have locked in one of the two defects
below rather than the property both of them break:

* on a jack2 machine (``libjack-jackd2-0``) the list as it stood removed jack2,
  because ``libfluidsynth-dev`` depends on ``libjack-dev | libjack-jackd2-dev``
  and apt takes the first alternative, jack1, which conflicts with jack2;
* naming jack2's development package unconditionally fixes that machine and
  removes ``libjack0`` from a jack1 machine instead, and with it every package
  linked to it.

Reach, stated rather than assumed: the simulation needs ``apt-get``,
``apt-cache`` and ``dpkg-query`` and an archive that knows the packages, so it
skips -- visibly, with the reason -- anywhere else. The package relations it
measures are that archive's; a Debian runner and an Ubuntu runner can each only
speak for their own distribution.
"""

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env_support import sandbox_env  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install-build-deps.sh"

APT_GET = shutil.which("apt-get")
APT_CACHE = shutil.which("apt-cache")
DPKG_QUERY = shutil.which("dpkg-query")

# apt's own summary line. Parsed, never guessed: a run that does not print it
# did not resolve, and must not read as "removes nothing".
SUMMARY = re.compile(
    r"^(\d+) upgraded, (\d+) newly installed, (\d+) to remove", re.M)

# The two JACK runtimes a machine can have. They conflict with each other,
# which is the whole of the hazard.
JACK1_RUNTIME = "libjack0"
JACK2_RUNTIME = "libjack-jackd2-0"

# Fields that decide what apt must remove to make room. Depends is left out on
# purpose: the fixture models one installed package, not a whole machine, and
# an installed package with unmet Depends makes apt refuse the transaction for
# a reason that has nothing to do with this test.
_RELATION_FIELDS = ("Version", "Architecture", "Multi-Arch", "Provides",
                    "Replaces", "Conflicts", "Breaks")


def archive_stanza(package):
    """The archive's own control stanza for *package*, or None."""
    if not APT_CACHE:
        return None
    result = subprocess.run([APT_CACHE, "show", package],
                            capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    fields = {}
    for line in result.stdout.split("\n\n", 1)[0].splitlines():
        if line[:1] in (" ", "\t") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields.setdefault(key, value.strip())
    return fields


def installed_status(packages):
    """A dpkg status file in which exactly *packages* are installed."""
    stanzas = []
    for package in packages:
        fields = archive_stanza(package)
        lines = [f"Package: {package}", "Status: install ok installed",
                 "Maintainer: fixture <fixture@example.invalid>"]
        lines += [f"{key}: {fields[key]}" for key in _RELATION_FIELDS
                  if key in fields]
        lines.append("Description: fixture")
        stanzas.append("\n".join(lines))
    return "\n\n".join(stanzas) + ("\n" if stanzas else "")


def summary(output):
    """(upgraded, newly installed, to remove) from apt's summary line."""
    match = SUMMARY.search(output)
    if match is None:
        return None
    return tuple(int(group) for group in match.groups())


@unittest.skipUnless(APT_GET and APT_CACHE and DPKG_QUERY,
                     "needs apt-get, apt-cache and dpkg-query")
class DebianInstallRemovesNothingTests(unittest.TestCase):
    """The Debian backend, run for real against fixture roots, in simulation."""

    @classmethod
    def setUpClass(cls):
        for package in ("libfluidsynth-dev", JACK1_RUNTIME, JACK2_RUNTIME):
            if archive_stanza(package) is None:
                raise unittest.SkipTest(
                    f"this machine's apt archive does not know {package}")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def simulate(self, installed, *install_args):
        """Run `debian_install` on a machine where only *installed* is present.

        `sudo` is replaced by a pass-through, `apt-get install` by apt's own
        simulation over the fixture's dpkg database, and `dpkg-query` reads that
        same database -- so the script decides what to ask for from the
        fixture, exactly as it would from a real machine.
        """
        admin = self.base / "dpkg"
        admin.mkdir()
        (admin / "updates").mkdir()
        (admin / "status").write_text(installed_status(installed))
        extended = self.base / "extended_states"
        extended.write_text("")
        bindir = self.base / "bin"
        bindir.mkdir()
        record = self.base / "apt-get.out"
        stubs = {
            "sudo": 'exec "$@"\n',
            "apt-get": textwrap.dedent(f"""\
                case "$1" in
                  update) exit 0 ;;
                  install)
                    shift
                    {shlex.quote(APT_GET)} install -s \\
                      -o Dir::State::status={shlex.quote(str(admin / "status"))} \\
                      -o Dir::State::extended_states={shlex.quote(str(extended))} \\
                      "$@" > {shlex.quote(str(record))} 2>&1
                    rc=$?
                    cat {shlex.quote(str(record))}
                    exit $rc ;;
                esac
                echo "unexpected apt-get $*" >&2
                exit 97
                """),
            "dpkg-query": (f"exec {shlex.quote(DPKG_QUERY)} "
                           f"--admindir={shlex.quote(str(admin))} \"$@\"\n"),
        }
        for name, body in stubs.items():
            path = bindir / name
            path.write_text("#!/bin/sh\n" + body)
            path.chmod(0o755)

        # Absent is not invisible: prove apt reads the fixture before
        # believing anything it says about it.
        for package in installed:
            policy = subprocess.run(
                [APT_CACHE, "-o", f"Dir::State::status={admin / 'status'}",
                 "policy", package], capture_output=True, text=True)
            self.assertRegex(policy.stdout, r"Installed: (?!\(none\))\S",
                             f"apt does not see the fixture's {package}")

        env = sandbox_env(
            HOME=str(self.base / "home"),
            PATH=str(bindir) + os.pathsep + os.environ.get("PATH", ""),
            LC_ALL="C.UTF-8", LANG="C.UTF-8")
        result = subprocess.run(
            ["bash", "-c", 'source "$1"; shift; debian_install "$@"', "_",
             str(SCRIPT), *install_args],
            env=env, capture_output=True, text=True, timeout=600)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        output = record.read_text()
        counts = summary(output)
        self.assertIsNotNone(counts, "apt printed no summary line:\n" + output)
        self.assertGreater(counts[1], 0,
                           "the simulation resolved nothing:\n" + output)
        return counts, output

    def assert_removes_nothing(self, installed, *install_args):
        (upgraded, new, removed), output = self.simulate(installed,
                                                         *install_args)
        removals = [line for line in output.splitlines()
                    if line.startswith("Remv ")]
        self.assertEqual(removed, 0, "\n".join(removals) or output)

    def test_a_jack1_machine_loses_nothing(self):
        self.assert_removes_nothing([JACK1_RUNTIME])

    def test_a_jack2_machine_loses_nothing(self):
        self.assert_removes_nothing([JACK2_RUNTIME])

    def test_a_machine_without_jack_loses_nothing(self):
        self.assert_removes_nothing([])


class VerifyReportsWhatTheBuildNeedsTests(unittest.TestCase):
    """`--verify` against a machine assembled from stubs, so its verdict is
    decided by the one thing each test varies.

    Everything verify() checks other than the build interpreter is stubbed to
    pass, and the PATH holds nothing else, so the host's own interpreters cannot
    leak in. The control arm (`test_..._passes_...`) is what makes the failing
    arms mean something: with the same stubs and headers present, the verdict
    is OK, so a failure below is the interpreter's headers and nothing else.
    """

    _TOOLS = ("bash", "env", "dirname", "mkdir", "chmod", "awk", "sort",
              "head", "sed", "grep", "cat", "sh")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.bindir = self.base / "bin"
        self.bindir.mkdir()
        for tool in self._TOOLS:
            real = shutil.which(tool)
            self.assertIsNotNone(real, f"the fixture needs {tool}")
            (self.bindir / tool).symlink_to(real)
        stubs = {
            "pkg-config": "exit 0\n",
            "gcc": "cat >/dev/null; exit 0\n",
            "make": "exit 0\n", "git": "exit 0\n", "curl": "exit 0\n",
            "zstd": "exit 0\n",
            "go": 'echo "go version go1.99.0 linux/amd64"\n',
            # The desktop's own checks run `python3`; it is too old to be a
            # build interpreter, so it can never be the one selected.
            "python3": self._interpreter_body("3.11.9", None),
        }
        for name, body in stubs.items():
            self._write(name, body)

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, name, body):
        path = self.bindir / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)
        return path

    def _interpreter_body(self, version, include):
        include_line = (f'  *get_paths*) echo {shlex.quote(str(include))}; exit 0 ;;\n'
                        if include is not None else
                        '  *get_paths*) exit 1 ;;\n')
        return (
            'case "$2" in\n'
            '  *ensurepip*) exit 0 ;;\n'
            '  *PIL*) echo "   Pillow: 0.0-fixture"; exit 0 ;;\n'
            f'  *version_info*) echo {version}; exit 0 ;;\n'
            + include_line +
            'esac\n'
            'exit 3\n')

    def interpreter(self, name, version, headers):
        """A build interpreter whose include directory does or does not hold
        Python.h."""
        include = self.base / f"include-{name}"
        include.mkdir()
        if headers:
            (include / "Python.h").write_text("/* fixture */\n")
        return self._write(name, self._interpreter_body(version, include))

    def verify(self, **extra):
        env = {"PATH": str(self.bindir), "HOME": str(self.base / "home"),
               "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8", **extra}
        return subprocess.run([str(SCRIPT), "--verify"], env=env,
                              capture_output=True, text=True, timeout=120)

    def selected(self, result):
        line, = [line for line in result.stdout.splitlines()
                 if line.startswith("   build Python: ")]
        return line.rsplit("(", 1)[1].rstrip(")")

    def test_verify_passes_when_the_interpreter_has_its_headers(self):
        self.interpreter("python3.13", "3.13.5", headers=True)
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("==> OK", result.stdout)
        self.assertIn("build Python headers: yes", result.stdout)

    def test_verify_fails_when_the_build_would_not_find_python_h(self):
        # The measured failure: an interpreter new enough to be chosen, and no
        # Python.h for it. verify() used to report `build Python: 3.13.x` and
        # `==> OK`; the build then died on its first `#include <Python.h>`.
        self.interpreter("python3.13", "3.13.5", headers=False)
        result = self.verify()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("==> OK", result.stdout)
        self.assertIn("==> INCOMPLETE", result.stdout)
        self.assertIn("build Python headers: MISSING", result.stdout)

    def test_an_interpreter_that_can_build_is_preferred_to_a_newer_one(self):
        # The machine the defect was found on: the newest interpreter came from
        # elsewhere and had no headers; the distro default had them.
        self.interpreter("python3.14", "3.14.0", headers=False)
        buildable = self.interpreter("python3.13", "3.13.5", headers=True)
        result = self.verify()
        self.assertEqual(self.selected(result), str(buildable))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_an_explicit_interpreter_without_headers_is_reported_not_trusted(self):
        self.interpreter("python3.13", "3.13.5", headers=True)
        chosen = self.interpreter("python3.14", "3.14.0", headers=False)
        result = self.verify(KILIX_PYTHON=str(chosen))
        self.assertEqual(self.selected(result), str(chosen))
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("build Python headers: MISSING", result.stdout)


if __name__ == "__main__":
    unittest.main()
