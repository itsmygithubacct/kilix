"""A pinned component installer must deliver its pin to existing machines too.

The component installers had two paths that disagreed. A first-use download
always landed on the resolved ref; an existing checkout was reinstalled from
whatever it happened to hold, announced as `kilix tui: using existing checkout
at 372559f`. So a moved pin reached every freshly provisioned machine and no
updated one — the update path silently reinstalled the stale tree.

Both paths now resolve the same ref, under the same immutable-SHA validation.
Keeping a checkout as it is stays possible and has to say so in as many words,
because a silent decision is what caused this.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

# Every installer with the "existing checkout, sibling repository" shape.
COMPONENTS = (
    ("install-kilix-tui-utils.sh", "KILIX_TUI_UTILS"),
    ("install-kilix-cap.sh", "KILIX_CAP"),
    ("install-kilix-land-desktop.sh", "KILIX_LAND_DESKTOP"),
    ("install-kilix-chawan.sh", "KILIX_CHAWAN"),
)

GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "kilix tests",
    "GIT_AUTHOR_EMAIL": "tests@example.invalid",
    "GIT_COMMITTER_NAME": "kilix tests",
    "GIT_COMMITTER_EMAIL": "tests@example.invalid",
}


def git(*argv, cwd=None):
    result = subprocess.run(
        ["git", *argv], cwd=cwd, check=True, text=True, capture_output=True,
        env=dict(os.environ, **GIT_IDENTITY))
    return result.stdout.strip()


def stage_installer(name, prefix, ref, destination):
    """The real installer with only its pinned default replaced."""
    body = (SCRIPTS / name).read_text()
    body, count = re.subn(
        r"(?m)^%s_DEFAULT_REF=[0-9a-fA-F]{40}$" % prefix,
        "%s_DEFAULT_REF=%s" % (prefix, ref), body)
    assert count == 1, "no %s_DEFAULT_REF line in %s" % (prefix, name)
    destination.write_text(body)
    destination.chmod(0o755)
    return destination


class ExistingCheckoutTests(unittest.TestCase):
    """Behaviour, against real Git checkouts and real installer scripts."""

    maxDiff = None

    def _origin(self, tmp, payload, ignore):
        """A two-commit component repository: 'old' then 'new'."""
        origin = tmp / "origin"
        origin.mkdir()
        git("init", "-q", "-b", "main", str(origin))
        for name, content in payload.items():
            (origin / name).write_text(content)
        # Real component repositories ignore what they build, which is what
        # keeps a built checkout eligible to be moved.
        (origin / ".gitignore").write_text(ignore)
        (origin / "marker").write_text("#!/bin/sh\n# old\n")
        git("add", "-A", cwd=str(origin))
        git("commit", "-qm", "old", cwd=str(origin))
        old = git("rev-parse", "HEAD", cwd=str(origin))
        (origin / "marker").write_text("#!/bin/sh\n# new\n")
        git("commit", "-qam", "new", cwd=str(origin))
        new = git("rev-parse", "HEAD", cwd=str(origin))
        return origin, old, new

    def _checkout(self, tmp, origin, ref, name):
        checkout = tmp / "sources" / "kilix-desktops" / name
        checkout.parent.mkdir(parents=True, exist_ok=True)
        git("clone", "-q", str(origin), str(checkout))
        git("checkout", "-q", "--detach", ref, cwd=str(checkout))
        return checkout

    def _run(self, installer, tmp, checkout, origin, prefix, **extra):
        env = dict(os.environ)
        for key in list(env):
            if key.startswith(("KILIX", "GPU_TERMINAL")):
                env.pop(key)
        env.update({
            "HOME": str(tmp),
            "KILIX_HOME": str(ROOT),
            "GPU_TERMINAL_SOURCE_HOME": str(tmp / "sources"),
            "GPU_TERMINAL_HOME": str(tmp / "gpu_terminal"),
            "%s_DIR" % prefix: str(checkout),
            "%s_REPO" % prefix: str(origin),
            "%s_PREFIX" % prefix: str(tmp / "prefix"),
        })
        env.update(extra)
        return subprocess.run(
            [str(installer)], capture_output=True, text=True, env=env,
            timeout=300)

    def _case(self, name, prefix, payload, artifact, ignore, component):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td).resolve()
            origin, old, new = self._origin(tmp, payload, ignore)
            checkout = self._checkout(tmp, origin, old, component)
            installer = stage_installer(name, prefix, new, tmp / name)

            forward = self._run(installer, tmp, checkout, origin, prefix)
            self.assertEqual(forward.returncode, 0, forward.stderr)
            self.assertEqual(git("rev-parse", "HEAD", cwd=str(checkout)), new)
            self.assertIn("existing checkout advanced", forward.stderr)
            self.assertNotIn("using existing checkout", forward.stderr)
            built = tmp / artifact if artifact.startswith("prefix") else checkout / artifact
            self.assertIn("# new", built.read_text())

            # Second run, nothing left to do: no fetch, no noise about a move.
            again = self._run(installer, tmp, checkout, origin, prefix)
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertIn("already at the resolved ref", again.stderr)

            # A pin that is older than the machine is legitimate, and loud.
            rewinding = stage_installer(name, prefix, old, tmp / ("back-" + name))
            back = self._run(rewinding, tmp, checkout, origin, prefix)
            self.assertEqual(back.returncode, 0, back.stderr)
            self.assertEqual(git("rev-parse", "HEAD", cwd=str(checkout)), old)
            self.assertIn("REWOUND", back.stderr)

            # And the deliberate opt-out says exactly what it did not do.
            kept = self._run(installer, tmp, checkout, origin, prefix,
                             **{"%s_KEEP_EXISTING_CHECKOUT" % prefix: "1"})
            self.assertEqual(kept.returncode, 0, kept.stderr)
            self.assertEqual(git("rev-parse", "HEAD", cwd=str(checkout)), old)
            self.assertIn("keeping the existing checkout", kept.stderr)
            self.assertIn("NOT installed", kept.stderr)

            # A tree being worked in is kept too — and one uncommitted file
            # must not turn a stack update into a failure.
            (checkout / "work-in-progress").write_text("mine\n")
            dirty = self._run(installer, tmp, checkout, origin, prefix)
            self.assertEqual(dirty.returncode, 0, dirty.stderr)
            self.assertEqual(git("rev-parse", "HEAD", cwd=str(checkout)), old)
            self.assertIn("it has local modifications", dirty.stderr)
            self.assertIn("NOT installed", dirty.stderr)

    def test_kilix_tui_utils_advances_an_existing_checkout(self):
        install_sh = (
            "#!/bin/sh\n"
            "set -eu\n"
            'mkdir -p "$KILIX_TUI_UTILS_PREFIX/bin"\n'
            'cp "$(dirname "$0")/marker" "$KILIX_TUI_UTILS_PREFIX/bin/kilix-tui"\n'
            'chmod +x "$KILIX_TUI_UTILS_PREFIX/bin/kilix-tui"\n'
        )
        self._case("install-kilix-tui-utils.sh", "KILIX_TUI_UTILS",
                   {"install.sh": install_sh}, "prefix/bin/kilix-tui",
                   "", "kilix-tui-utils")

    def test_kilix_cap_advances_an_existing_checkout(self):
        makefile = (
            "all:\n"
            "\tmkdir -p bin\n"
            "\tcp marker bin/kilix-cap\n"
            "\tchmod +x bin/kilix-cap\n"
        )
        self._case("install-kilix-cap.sh", "KILIX_CAP",
                   {"Makefile": makefile}, "bin/kilix-cap",
                   "/bin/\n", "kilix-cap")

    def test_kilix_land_advances_an_existing_checkout(self):
        makefile = (
            "all:\n"
            "\tcp marker kilix-land-desktop\n"
            "\tchmod +x kilix-land-desktop\n"
        )
        self._case("install-kilix-land-desktop.sh", "KILIX_LAND_DESKTOP",
                   {"Makefile": makefile}, "kilix-land-desktop",
                   "/kilix-land-desktop\n", "kilix-land-desktop")


class InstallerShapeTests(unittest.TestCase):
    """Every installer of this shape, including the ones too heavy to build."""

    def test_no_installer_reinstalls_from_an_unresolved_checkout(self):
        for name, prefix in COMPONENTS:
            with self.subTest(installer=name):
                body = (SCRIPTS / name).read_text()
                # The blind-reuse log line, not the history of it in comments.
                self.assertNotIn('log "using existing checkout at', body)
                self.assertIn("advance_existing_checkout() {", body)
                self.assertIn('advance_existing_checkout "', body)
                self.assertIn(
                    'checkout_ref "$directory" "$install_ref"', body)
                self.assertIn("%s_KEEP_EXISTING_CHECKOUT" % prefix, body)
                # The opt-out is only acceptable because it is explicit.
                self.assertIn("NOT installed", body)

    def test_the_resolved_ref_still_passes_the_immutable_sha_gate(self):
        for name, prefix in COMPONENTS:
            with self.subTest(installer=name):
                body = (SCRIPTS / name).read_text()
                self.assertRegex(
                    body, r"(?m)^%s_DEFAULT_REF=[0-9a-f]{40}$" % prefix)
                self.assertIn(
                    'if ! [[ "$install_ref" =~ ^[0-9a-fA-F]{40}$ ]]', body)
                self.assertIn("%s_ALLOW_MUTABLE_REF" % prefix, body)


if __name__ == "__main__":
    unittest.main()
