"""Release mode on Plebian OS does not install Kilix build packages through system apt.

The release closure was already installed. A package still missing is named and
left uninstalled, so a third-party source cannot supply a higher version.
Development, and release mode on a machine that is not Plebian OS, keep today's
``sudo apt-get`` command.
"""
import os
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
THIRD = "third-party-libxxhash-dev-2.0"


class ReleaseBuildDepsAptTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.record = self.base / "apt-record"
        self.marker = self.base / "build-info.env"
        self.marker.write_text("PLEBIAN_OS_VERSION=0.2.3\n")
        self.stubs = self.base / "bin"
        self.stubs.mkdir()
        self._stub("sudo", "#!/bin/sh\nexec \"$@\"\n")
        self._stub("apt-get", textwrap.dedent(f"""\
            #!/bin/sh
            printf '%s\\n' "apt-get $*" >> {self.record!s}
            case "$*" in
              *install*) printf '%s\\n' {THIRD!r} >> {self.record!s} ;;
            esac
            exit 0
            """))
        self._stub("dpkg-query", textwrap.dedent("""\
            #!/bin/sh
            pkg=
            for arg in "$@"; do pkg=$arg; done
            case "$pkg" in
              libxxhash-dev) exit 1 ;;
            esac
            case "$*" in
              *db:Status-Status*) printf 'installed\\n' ;;
              *) printf 'install ok installed' ;;
            esac
            """))

    def tearDown(self):
        self._tmp.cleanup()

    def _stub(self, name, body):
        path = self.stubs / name
        path.write_text(body)
        path.chmod(0o755)

    def run_debian(self, **env):
        child = sandbox_env(
            HOME=str(self.base / "home"),
            PATH=f"{self.stubs}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
            KILIX_STORAGE_HOME=str(self.base / "kilix-data"),
            KILIX_CONFIG_HOME=str(self.base / "kilix-data" / "config"),
            PLEBIAN_OS_MACHINE_MARKER=str(self.marker),
            LC_ALL="C",
        )
        child.update(env)
        state = self.base / "var-lib-kilix"
        conf = self.base / "systemd-user"
        state.mkdir(exist_ok=True)
        conf.mkdir(exist_ok=True)
        return subprocess.run(
            ["bash", "-c",
             'source "$1"; KILIX_SYSTEM_STATE="$2"; SYSTEMD_USER_CONF="$3"; '
             'debian_install; printf "rc=%s\\n" "$?"',
             "_", str(SCRIPT), str(state), str(conf)],
            env=child, text=True, capture_output=True)

    def test_a_missing_package_is_refused_and_the_third_party_version_is_not_used(self):
        result = self.run_debian(PLEBIAN_OS_RELEASE_MODE="1")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "release package closure is incomplete; missing", result.stderr)
        self.assertIn("libxxhash-dev", result.stderr)
        self.assertIn("the system apt configuration was not used", result.stderr)
        self.assertIn(
            "run 'plebian-os-update --revalidate-current' to reinstall this release's packages; if it is still missing, report it",
            result.stderr)
        self.assertNotIn("pleb update", result.stderr)
        self.assertNotIn("--recover-interrupted", result.stderr)
        self.assertFalse(self.record.exists(), result.stderr)
        self.assertNotIn(THIRD, result.stdout + result.stderr)

    def test_installed_packages_are_not_upgraded_to_a_third_party_version(self):
        self._stub("dpkg-query", textwrap.dedent("""\
            #!/bin/sh
            case "$*" in
              *db:Status-Status*) printf 'installed\\n' ;;
              *) printf 'install ok installed' ;;
            esac
            """))
        result = self.run_debian(PLEBIAN_OS_RELEASE_MODE="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "release package closure already includes these build packages",
            result.stderr)
        self.assertIn("the system apt configuration was not used", result.stderr)
        self.assertFalse(self.record.exists(), result.stdout + result.stderr)
        self.assertNotIn(THIRD, result.stdout + result.stderr)

    def test_a_held_package_and_any_installed_architecture_count(self):
        self._stub("dpkg-query", textwrap.dedent("""\
            #!/bin/sh
            pkg=
            for arg in "$@"; do pkg=$arg; done
            case "$*" in
              *db:Status-Status*)
                case "$pkg" in
                  libssl-dev) printf 'not-installed\\ninstalled\\n' ;;
                  *) printf 'installed\\n' ;;
                esac
                ;;
              *) printf 'hold ok installedhold ok installed' ;;
            esac
            """))
        result = self.run_debian(PLEBIAN_OS_RELEASE_MODE="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "release package closure already includes these build packages",
            result.stderr)
        self.assertNotIn("libssl-dev", result.stderr)

    def test_libssl_dev_installed_for_amd64_and_i386_counts(self):
        """libssl-dev:amd64 and libssl-dev:i386 are one installed package.

        dpkg-query emits one status line per architecture when the format
        contains a newline. Both lines are installed. A format without that
        newline would glue them together, and the package would look missing.
        """
        self._stub("dpkg-query", textwrap.dedent(r"""\
            #!/bin/sh
            newline=0
            pkg=
            for arg in "$@"; do
              pkg=$arg
              case "$arg" in
                *'\n'*) newline=1 ;;
              esac
            done
            case "$pkg" in
              libssl-dev)
                if [ "$newline" = 1 ]; then
                  printf 'installed\ninstalled\n'
                else
                  printf 'installedinstalled'
                fi
                ;;
              libssl-dev:amd64|libssl-dev:i386)
                printf 'installed\n'
                ;;
              *)
                printf 'installed\n'
                ;;
            esac
            """))
        result = self.run_debian(PLEBIAN_OS_RELEASE_MODE="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "release package closure already includes these build packages",
            result.stderr)
        self.assertNotIn("libssl-dev", result.stderr)

    def test_development_mode_still_uses_system_apt(self):
        result = self.run_debian(PLEBIAN_OS_RELEASE_MODE="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        recorded = self.record.read_text()
        self.assertIn("apt-get update", recorded)
        self.assertIn("apt-get install -y", recorded)
        self.assertIn(THIRD, recorded)
        self.assertNotIn("APT_CONFIG", recorded)
        self.assertNotIn("--no-remove", recorded)

    def test_release_mode_off_a_plebian_machine_keeps_system_apt(self):
        result = self.run_debian(
            PLEBIAN_OS_RELEASE_MODE="1",
            PLEBIAN_OS_MACHINE_MARKER=str(self.base / "absent-marker"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("apt-get install -y", self.record.read_text())
        self.assertIn(THIRD, self.record.read_text())

    def test_a_symlink_marker_does_not_count_as_the_installed_machine(self):
        link = self.base / "marker-link"
        link.symlink_to(self.marker)
        result = self.run_debian(
            PLEBIAN_OS_RELEASE_MODE="1",
            PLEBIAN_OS_MACHINE_MARKER=str(link),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("apt-get install -y", self.record.read_text())


if __name__ == "__main__":
    unittest.main()
