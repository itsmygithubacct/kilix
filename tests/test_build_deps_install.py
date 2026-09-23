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
    """A dpkg status file in which exactly *packages* are present.

    Each entry is a package name, installed, or a ``(name, status)`` pair for
    any other dpkg state -- ``install ok unpacked`` for an install left
    unfinished, ``deinstall ok config-files`` for one removed but not
    purged."""
    stanzas = []
    for entry in packages:
        package, status = ((entry, "install ok installed")
                           if isinstance(entry, str) else entry)
        fields = archive_stanza(package)
        lines = [f"Package: {package}", f"Status: {status}",
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
class _DebianBackendSimulation(unittest.TestCase):
    """The Debian backend, run for real against fixture roots, in simulation.

    Holds no tests of its own; the classes below share it."""

    @classmethod
    def setUpClass(cls):
        for package in ("libfluidsynth-dev", JACK1_RUNTIME, JACK2_RUNTIME):
            if archive_stanza(package) is None:
                raise unittest.SkipTest(
                    f"this machine's apt archive does not know {package}")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        # The two machine-wide places the backend writes besides apt's own:
        # enablement links, and its record of what it did with them. Both are
        # always fixture roots, so no test can reach the host's.
        self.conf = self.base / "etc-systemd-user"
        self.system_state = self.base / "var-lib-kilix"
        self.admin = self.base / "dpkg"

    def tearDown(self):
        self.temp.cleanup()

    def simulate(self, installed, *install_args, after_install="",
                 apt_rc=None, expect_rc=0):
        """Run `debian_install` on a machine where only *installed* is present.

        `sudo` is replaced by a pass-through, `apt-get install` by apt's own
        simulation over the fixture's dpkg database, and `dpkg-query` reads that
        same database -- so the script decides what to ask for from the
        fixture, exactly as it would from a real machine. *after_install* runs
        once apt has resolved, as a package's maintainer scripts would, and
        *apt_rc*, when given, is the exit status apt-get then reports. Called
        again in one test, it runs again on the same machine, as a second run
        of the installer would.
        """
        admin = self.admin
        extended = self.base / "extended_states"
        if not admin.exists():
            admin.mkdir()
            (admin / "updates").mkdir()
            (admin / "status").write_text(installed_status(installed))
            extended.write_text("")
        bindir = self.base / "bin"
        bindir.mkdir(exist_ok=True)
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
                    {after_install}
                    cat {shlex.quote(str(record))}
                    exit {apt_rc if apt_rc is not None else "$rc"} ;;
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

        # Absent is not invisible: prove apt and dpkg read the fixture before
        # believing anything they say about it.
        for entry in installed:
            package, status = ((entry, "install ok installed")
                               if isinstance(entry, str) else entry)
            seen = subprocess.run(
                [DPKG_QUERY, f"--admindir={admin}", "-W",
                 "-f=${Status}", package], capture_output=True, text=True)
            self.assertEqual(seen.stdout, status,
                             f"dpkg does not see the fixture's {package}")
            if status.endswith(" installed"):
                policy = subprocess.run(
                    [APT_CACHE, "-o", f"Dir::State::status={admin / 'status'}",
                     "policy", package], capture_output=True, text=True)
                self.assertRegex(policy.stdout, r"Installed: (?!\(none\))\S",
                                 f"apt does not see the fixture's {package}")

        env = sandbox_env(
            HOME=str(self.base / "home"),
            PATH=str(bindir) + os.pathsep + os.environ.get("PATH", ""),
            LC_ALL="C.UTF-8", LANG="C.UTF-8",
            FIXTURE_USER_CONF=str(self.conf),
            FIXTURE_SYSTEM_STATE=str(self.system_state))
        result = subprocess.run(
            ["bash", "-c",
             'source "$1"; shift; '
             'SYSTEMD_USER_CONF=$FIXTURE_USER_CONF; '
             'KILIX_SYSTEM_STATE=$FIXTURE_SYSTEM_STATE; '
             'debian_install "$@"', "_",
             str(SCRIPT), *install_args],
            env=env, capture_output=True, text=True, timeout=600)
        self.script_output = result.stdout + result.stderr
        self.assertEqual(result.returncode, expect_rc, self.script_output)
        output = record.read_text()
        counts = summary(output)
        self.assertIsNotNone(counts, "apt printed no summary line:\n" + output)
        self.assertGreater(counts[1], 0,
                           "the simulation resolved nothing:\n" + output)
        return counts, output


class DebianInstallRemovesNothingTests(_DebianBackendSimulation):
    """Installing build dependencies never removes a working package."""

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


# What the fluidsynth package's postinst does, modelled from its own text on
# Debian 13 (dh_installsystemduser): `deb-systemd-helper --user was-enabled`
# is true when the package has no record yet or every recorded link exists,
# and then `enable` links the unit into default.target.wants machine-wide and
# records the link; otherwise it only updates the record. apt's simulation
# decides WHETHER the player is configured in this run -- a `Conf` line, which
# it prints for a fresh install and for completing an unfinished one -- from
# the real archive; this models only what the package does then, and that dpkg
# now records the player as installed.
_FLUIDSYNTH_POSTINST_MODEL = r"""
record=$1 conf=$2 state=$3 status=$4 status_after=$5
grep -q '^Conf fluidsynth ' "$record" || exit 0
cp "$status_after" "$status"
enabled=1
if [ -f "$state/fluidsynth.service.dsh-also" ]; then
  while IFS= read -r link; do [ -L "$link" ] || enabled=0; done \
    < "$state/fluidsynth.service.dsh-also"
fi
[ "$enabled" = 1 ] || exit 0
mkdir -p "$conf/default.target.wants" "$state"
ln -sfn /usr/lib/systemd/user/fluidsynth.service \
  "$conf/default.target.wants/fluidsynth.service"
echo "$conf/default.target.wants/fluidsynth.service" \
  > "$state/fluidsynth.service.dsh-also"
"""

PLAYER = "fluidsynth"
PLAYER_UNIT = "fluidsynth.service"
DEFAULT_LINK = f"default.target.wants/{PLAYER_UNIT}"


class DebianInstallLeavesTheSoundCardFreeTests(_DebianBackendSimulation):
    """After the Debian backend runs, no user unit that became enabled for
    every login during its apt run holds the default sound card -- whether
    that run succeeded, failed, or was killed and run again -- and what it
    removed, and why, is written down.

    libfluidsynth-dev, the only package with fluidsynth.pc, depends on the
    fluidsynth player, so the player arrives whatever the list says, and its
    package enables fluidsynth.service machine-wide; that daemon holds the
    default sound card that dictation records from. The backend may leave the
    player installed; it must not leave it enabled when the enablement
    appeared during its own run, it must never touch an enablement that was
    already there, and what it says must be what happened.
    """

    def run_install(self, installed, before=(), **simulate):
        """Run the backend with the player's postinst modelled. *before* are
        enablement links present beforehand, each with the package's record
        of it, as the package leaves them."""
        state = self.base / "deb-systemd-user-helper-enabled"
        for link in before:
            path = self.conf / link
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(f"/usr/lib/systemd/user/{path.name}")
            state.mkdir(exist_ok=True)
            with open(state / f"{path.name}.dsh-also", "a") as recorded:
                recorded.write(f"{path}\n")
        model = self.base / "postinst-model.sh"
        model.write_text(_FLUIDSYNTH_POSTINST_MODEL)
        # dpkg's record once the player is configured: the same machine, with
        # the player installed.
        others = [entry for entry in installed
                  if (entry if isinstance(entry, str) else entry[0]) != PLAYER]
        status_after = self.base / "status-after-player"
        status_after.write_text(installed_status(others + [PLAYER]))
        record = self.base / "apt-get.out"
        _, output = self.simulate(
            installed,
            after_install=(simulate.pop("after_install", "")
                           + f"\nsh {shlex.quote(str(model))} "
                           f"{shlex.quote(str(record))} "
                           f"{shlex.quote(str(self.conf))} "
                           f"{shlex.quote(str(state))} "
                           f"{shlex.quote(str(self.admin / 'status'))} "
                           f"{shlex.quote(str(status_after))}"
                           + simulate.pop("after_model", "")),
            **simulate)
        return output, self.enabled(), state

    def enabled(self):
        return sorted(str(path.relative_to(self.conf))
                      for pattern in ("*.wants/*", "*.requires/*")
                      for path in self.conf.glob(pattern) if path.is_symlink())

    def recorded(self):
        """audio-holdoff.log as (action, link, why) rows."""
        log_file = self.system_state / "audio-holdoff.log"
        if not log_file.exists():
            return []
        return [tuple(line.split("\t")[1:]) for line in
                log_file.read_text().splitlines()]

    @property
    def pending(self):
        return self.system_state / "audio-holdoff.pending"

    def require_player_arrives(self, output):
        if not re.search(rf"^Conf {PLAYER} ", output, re.M):
            self.skipTest("this archive's closure does not bring in the "
                          "player, so there is nothing to hold off")

    def test_a_player_this_install_brought_in_is_not_left_enabled(self):
        # A per-account enablement is the user's own choice, made where this
        # backend never looks; it must survive.
        own = (self.base / "home" / ".config" / "systemd" / "user" /
               DEFAULT_LINK)
        own.parent.mkdir(parents=True)
        own.symlink_to(f"/usr/lib/systemd/user/{PLAYER_UNIT}")

        output, enabled, state = self.run_install([])
        self.require_player_arrives(output)
        # The model fired: the player's package did enable its unit, so an
        # empty result below is the backend's doing, not the fixture's.
        self.assertTrue((state / f"{PLAYER_UNIT}.dsh-also").is_file())
        self.assertEqual(enabled, [], self.script_output)
        self.assertIn("holds the default sound card", self.script_output)
        self.assertIn("apt run installed the fluidsynth player",
                      self.script_output)
        self.assertIn(f"systemctl --global enable {PLAYER_UNIT}",
                      self.script_output)
        self.assertTrue(own.is_symlink(), "a per-account enablement was touched")
        (action, link, why), = self.recorded()
        self.assertEqual((action, link), ("removed",
                                          str(self.conf / DEFAULT_LINK)))
        self.assertIn("player-installed-by-this-run", why)
        self.assertFalse(self.pending.exists(),
                         "a finished run left its pending record behind")

    def test_an_enablement_that_was_already_there_is_left_alone(self):
        # The player installed and enabled before Kilix ran. (apt may still
        # reconfigure it here, because the fixture's stanza carries no
        # Depends; the modelled postinst then re-links over the same link,
        # which is what the real one does.)
        _, enabled, _ = self.run_install([PLAYER], before=[DEFAULT_LINK])
        self.assertEqual(enabled, [DEFAULT_LINK])
        self.assertNotIn("holds the default sound card", self.script_output)
        self.assertEqual(self.recorded(), [])

    def test_an_apt_failure_after_the_player_is_set_up_still_holds_it_off(self):
        # apt configured the player, whose package enabled the daemon, and
        # then failed on something else. The hold-off used to run only after
        # a successful apt-get, so the link stayed, silently, and every later
        # run counted it as already there.
        output, enabled, _ = self.run_install([], apt_rc=100, expect_rc=100)
        self.require_player_arrives(output)
        self.assertEqual(enabled, [], self.script_output)
        self.assertIn("apt-get install failed (exit 100)", self.script_output)
        self.assertIn("holds the default sound card", self.script_output)
        (action, _, why), = self.recorded()
        self.assertEqual(action, "removed")
        self.assertIn("apt-get-exit=100", why)
        # The player may still be half-installed after a failure, so the
        # record stays until an install succeeds.
        self.assertTrue(self.pending.exists())

    def test_a_run_killed_after_the_player_is_set_up_is_finished_by_the_next(self):
        # Killed between apt-get and the check: no trap can run. The next run
        # must compare against what this one recorded before its install,
        # not against the machine this one changed.
        output, enabled, _ = self.run_install(
            [], after_model="\nkill -9 $PPID", expect_rc=-9)
        self.require_player_arrives(output)
        # Control: the kill landed before the check could run.
        self.assertEqual(enabled, [DEFAULT_LINK])
        self.assertEqual(self.recorded(), [])
        self.assertTrue(self.pending.exists())

        _, enabled, _ = self.run_install([])
        self.assertEqual(enabled, [], self.script_output)
        self.assertIn("stopped before its FluidSynth check", self.script_output)
        self.assertIn("apt run installed the fluidsynth player",
                      self.script_output)
        (action, _, why), = self.recorded()
        self.assertEqual(action, "removed")
        self.assertIn("finishing-an-earlier-interrupted-run", why)
        self.assertFalse(self.pending.exists())

    def test_completing_the_users_own_unfinished_player_install_is_said_so(self):
        # The user's own `apt install fluidsynth` stopped after unpacking. apt
        # completes it whatever it is asked for, so this run configures the
        # player and its package enables the daemon. The warning used to say
        # Kilix Amp's package had pulled the player in.
        output, enabled, _ = self.run_install(
            [(PLAYER, "install ok unpacked")])
        self.require_player_arrives(output)
        self.assertEqual(enabled, [], self.script_output)
        self.assertIn("own install was unfinished", self.script_output)
        self.assertIn("dpkg state: unpacked", self.script_output)
        self.assertNotIn("apt run installed the fluidsynth player",
                         self.script_output)
        self.assertNotIn("depends on it", self.script_output)
        (action, _, why), = self.recorded()
        self.assertEqual(action, "removed")
        self.assertIn("unfinished-player-install-completed-by-this-run", why)

    def test_a_dormant_enablement_brought_back_to_life_is_said_so(self):
        # The player was removed but not purged: its link and record stay
        # behind. Reinstalling it makes that link live again. It was there
        # before, so it is left alone -- but no longer silently.
        output, enabled, _ = self.run_install(
            [(PLAYER, "deinstall ok config-files")], before=[DEFAULT_LINK])
        self.require_player_arrives(output)
        self.assertEqual(enabled, [DEFAULT_LINK])
        self.assertIn("removed from this machine but", self.script_output)
        self.assertIn("will start at every login again", self.script_output)
        self.assertIn(f"sudo systemctl --global disable {PLAYER_UNIT}",
                      self.script_output)
        (action, link, why), = self.recorded()
        self.assertEqual((action, link), ("left",
                                          str(self.conf / DEFAULT_LINK)))
        self.assertIn("player-state-before=config-files", why)


class _StubMachine(unittest.TestCase):
    """`--verify` against a machine assembled from stubs, so its verdict is
    decided by the one thing each test varies.

    Everything verify() checks is stubbed to pass, and the PATH holds nothing
    else, so the host's own interpreters and libraries cannot leak in. Holds no
    tests of its own; the classes below share it."""

    _TOOLS = ("bash", "env", "dirname", "mkdir", "chmod", "awk", "sort",
              "head", "sed", "grep", "cat", "sh")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.bindir = self.base / "bin"
        self.bindir.mkdir()
        self.missing_pc = self.base / "pc-missing"
        self.missing_pc.write_text("")
        for tool in self._TOOLS:
            real = shutil.which(tool)
            self.assertIsNotNone(real, f"the fixture needs {tool}")
            (self.bindir / tool).symlink_to(real)
        stubs = {
            # Every module is present except those named in pc-missing.
            "pkg-config": (
                '[ "${1:-}" = --exists ] && shift\n'
                'for m in "$@"; do\n'
                f'  grep -qxF -- "$m" {shlex.quote(str(self.missing_pc))} && exit 1\n'
                'done\n'
                'exit 0\n'),
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


class VerifyReportsWhatTheBuildNeedsTests(_StubMachine):
    """The build interpreter, and the Media Player's packages.

    The control arm (`test_..._passes_...`) is what makes the failing arms mean
    something: with the same stubs and headers present, the verdict is OK, so
    a failure below is the interpreter's headers and nothing else.
    """

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

    # The Media Player's packages. build.sh links none of them; the desktop
    # builds kilix-amp on first use. Named here rather than read from the
    # script, so moving one back into the required set fails a test.
    MEDIA_PLAYER_MODULES = ("sdl2", "SDL2_image", "sndfile", "fluidsynth")

    def test_the_media_players_packages_do_not_gate_the_fork_build(self):
        # pleb runs the installer whenever --verify fails, and on Debian the
        # only package with fluidsynth.pc hard-depends on the fluidsynth player.
        # So while these gated the terminal's verification, a machine that could
        # build the fork was sent to install the Media Player's packages, and
        # the player with them.
        self.interpreter("python3.13", "3.13.5", headers=True)
        self.missing_pc.write_text("\n".join(self.MEDIA_PLAYER_MODULES) + "\n")
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("==> OK", result.stdout)
        for module in self.MEDIA_PLAYER_MODULES:
            self.assertIn(f"   pkg-config {module}: missing (Media Player only",
                          result.stdout)

    def test_a_module_the_fork_links_still_gates_the_build(self):
        # The other direction, so the change cannot be satisfied by a verify
        # that stopped checking modules: zlib moved out of the Media Player's
        # set because the fork links -lz, and libxxhash is one pleb's own
        # messages name.
        self.interpreter("python3.13", "3.13.5", headers=True)
        for module in ("zlib", "libxxhash"):
            with self.subTest(module=module):
                self.missing_pc.write_text(module + "\n")
                result = self.verify()
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("==> INCOMPLETE", result.stdout)
                self.assertIn(f"   pkg-config {module}: MISSING", result.stdout)


# ---- what the fork's build asks pkg-config for --------------------------------
FORK = ROOT / "src"

# The helpers through which the fork's setup.py asks pkg-config for a module,
# each taking the module name first; setup.py hands pkg_config itself on to
# glfw/glfw.py. Their own bodies run pkg-config for whatever name they are
# given, so a call to one of them is a request and their bodies are not.
_PKG_CONFIG_HELPERS = frozenset({"pkg_config", "pkg_version",
                                 "at_least_version"})


def _fork_build_files(fork):
    """setup.py and every module of the fork it imports, transitively --
    the files that decide what the build asks for, found by following the
    build's own imports rather than named here."""
    import ast
    seen, todo = [], [fork / "setup.py"]
    while todo:
        path = todo.pop()
        if path in seen or not path.is_file():
            continue
        seen.append(path)
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            if isinstance(node, ast.Import):
                targets = [(fork, alias.name) for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                anchor = fork
                if node.level:
                    anchor = path.parent
                    for _ in range(node.level - 1):
                        anchor = anchor.parent
                module = node.module or ""
                targets = [(anchor, module)] + [
                    (anchor, f"{module}.{alias.name}".strip("."))
                    for alias in node.names]
            else:
                continue
            for anchor, dotted in targets:
                if not dotted:
                    continue
                stem = anchor.joinpath(*dotted.split("."))
                todo += [stem.with_suffix(".py"), stem / "__init__.py"]
    return seen


def fork_pkg_config_requests(fork=FORK):
    """What the fork's build asks pkg-config for, read from its build files.

    Returns ``(required, optional, unresolved, per_file)``. A module is
    *required* when at least one request for it stops the build if it is
    missing -- every ``pkg_version`` and ``at_least_version`` request, and
    every ``pkg_config`` request not made with ``fatal=False`` or inside a
    ``suppress(SystemExit, ...)`` block. *unresolved* lists every request whose
    module name this reading could not work out, and every use of the
    pkg-config command outside the helpers: a request that cannot be read is a
    failure, never a module silently left out. Every platform's branch is read,
    so a module asked for only on another platform would be demanded here too,
    which fails safe.
    """
    import ast
    required, optional, unresolved, per_file = set(), set(), [], {}

    def names_in(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "split" and not node.args
                and isinstance(node.func.value, ast.Constant)
                and isinstance(node.func.value.value, str)):
            return node.func.value.value.split()
        if isinstance(node, (ast.Tuple, ast.List)):
            found = [names_in(element) for element in node.elts]
            if all(item is not None and len(item) == 1 for item in found):
                return [item[0] for item in found]
        return None

    def suppresses_exit(with_node):
        for item in with_node.items:
            call = item.context_expr
            if (isinstance(call, ast.Call) and getattr(call.func, "id",
                                                       getattr(call.func, "attr", None)) == "suppress"
                    and any(getattr(arg, "id", None) == "SystemExit"
                            for arg in call.args)):
                return True
        return False

    def visit(node, where, loops, suppressed):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name in _PKG_CONFIG_HELPERS:
            return
        if isinstance(node, ast.Name) and node.id == "PKGCONFIG" \
                and isinstance(node.ctx, ast.Load):
            unresolved.append(f"{where}:{node.lineno}: pkg-config run "
                              "directly, not through a helper")
        if isinstance(node, ast.Constant) and node.value in ("pkg-config",
                                                             "pkgconf"):
            unresolved.append(f"{where}:{node.lineno}: pkg-config named "
                              "outside the helpers")
        if isinstance(node, (ast.For, ast.AsyncFor)) \
                and isinstance(node.target, ast.Name):
            values = names_in(node.iter)
            if values is not None:
                loops = {**loops, node.target.id: values}
        if isinstance(node, (ast.With, ast.AsyncWith)) and suppresses_exit(node):
            suppressed = True
        if isinstance(node, ast.Call):
            helper = getattr(node.func, "id", getattr(node.func, "attr", None))
            if helper in _PKG_CONFIG_HELPERS:
                first = node.args[0] if node.args else None
                modules = None
                if isinstance(first, ast.Name):
                    modules = loops.get(first.id)
                elif first is not None:
                    modules = names_in(first)
                    modules = modules if modules and len(modules) == 1 else None
                if modules is None:
                    unresolved.append(f"{where}:{node.lineno}: {helper}() with "
                                      "a module name this reading cannot resolve")
                else:
                    fatal = not suppressed
                    for keyword in node.keywords:
                        if (helper == "pkg_config" and keyword.arg == "fatal"
                                and isinstance(keyword.value, ast.Constant)
                                and keyword.value.value is False):
                            fatal = False
                    (required if fatal else optional).update(modules)
                    per_file.setdefault(where, set()).update(modules)
        for child in ast.iter_child_nodes(node):
            visit(child, where, loops, suppressed)

    for path in _fork_build_files(fork):
        where = str(path.relative_to(fork))
        tree = ast.parse(path.read_text(), where)
        for statement in tree.body:
            # The one place pkg-config is named on purpose: which command the
            # helpers run.
            if (isinstance(statement, ast.Assign)
                    and [getattr(t, "id", None) for t in statement.targets]
                    == ["PKGCONFIG"]):
                continue
            visit(statement, where, {}, False)
    return required, optional - required, unresolved, per_file


class VerifyChecksWhatTheForkBuildAsksForTests(_StubMachine):
    """`--verify` fails whenever the fork's build would stop for a missing
    pkg-config module.

    The modules are not named here: they are read from the pinned fork's own
    build files (``fork_pkg_config_requests``), so this fails the day the build
    asks for a module verify() does not check, as well as the day verify()
    stops checking one the build asks for. It is behavioural: each module is
    made missing on its own and verify() must say INCOMPLETE, so a name that is
    listed but never checked does not count. egl and libdrm were unchecked for
    as long as the list existed, covered only because the Media Player's sdl2
    package depended on both; when that requirement went, --verify said OK
    over a build that stopped at `Package egl was not found`.
    """

    def setUp(self):
        super().setUp()
        if not (FORK / "setup.py").is_file():
            self.fail(f"the fork is not checked out at {FORK}; this check "
                      "cannot run without it (git submodule update --init src)")
        self.required, self.optional, self.unresolved, self.per_file = \
            fork_pkg_config_requests()

    def test_every_request_the_build_makes_is_read(self):
        self.assertEqual(self.unresolved, [],
                         "requests this test cannot read; resolve them before "
                         "trusting the set it derives")
        # A build file that calls a helper and yielded no request would mean
        # the reading went blind there, not that the file asks for nothing.
        for path in _fork_build_files(FORK):
            where = str(path.relative_to(FORK))
            text = path.read_text()
            calls = any(f"{helper}(" in text for helper in _PKG_CONFIG_HELPERS)
            if calls:
                with self.subTest(file=where):
                    self.assertTrue(self.per_file.get(where),
                                    f"{where} calls a pkg-config helper and "
                                    "no request was read from it")
        self.assertTrue(self.required)

    def test_verify_fails_without_any_module_the_build_asks_for(self):
        self.interpreter("python3.13", "3.13.5", headers=True)
        # Control: nothing missing, so a failure below is the missing module.
        control = self.verify()
        self.assertEqual(control.returncode, 0,
                         control.stdout + control.stderr)
        for module in sorted(self.required):
            with self.subTest(module=module):
                self.missing_pc.write_text(module + "\n")
                result = self.verify()
                self.assertNotEqual(
                    result.returncode, 0,
                    f"the fork's build stops without {module}, and --verify "
                    f"passed without it:\n{result.stdout}")
                self.assertIn("==> INCOMPLETE", result.stdout)
                self.assertIn(f"   pkg-config {module}: MISSING", result.stdout)


if __name__ == "__main__":
    unittest.main()
