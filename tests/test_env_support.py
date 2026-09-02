"""Checks on the environment a test hands to a child process.

These exist because Kilix's suite reported 13 failures when run inside a
running Kilix session and none when run outside one. The tests were reading the
machine. A regression here invalidates results elsewhere rather than merely
failing on its own.
"""

import os
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _env_support import STACK_PREFIXES, sandbox_env  # noqa: E402

TESTS_DIR = pathlib.Path(__file__).resolve().parent

# Modules still building a child environment from a raw copy of os.environ.
# This number may go DOWN. It must never go up: every one of them is a test
# that can be decided by whoever happens to be running it.
UNSANITISED_MODULE_BUDGET = 10


def _without_exempt(text: str, exempt: str) -> str:
    """*text* with every line containing *exempt* removed."""
    return "\n".join(line for line in text.splitlines() if exempt not in line)


class SandboxEnvTests(unittest.TestCase):
    def test_a_live_session_cannot_reach_the_child(self):
        # Paths under /nonexistent, not a plausible /home/<name>: the
        # publication hygiene gate treats any /home/<someone> string as a
        # personal path leak, and a fixture is not worth an allowlist entry.
        live = {
            "KILIX_SESSION_HOME": "/nonexistent/live/kilix/session",
            "KILIX95_STORAGE_HOME": "/nonexistent/live",
            "GPU_TERMINAL_HOME": "/nonexistent/live",
            "KITTY_WINDOW_ID": "7",
        }
        # patch.dict rather than a hand-rolled save/restore: the obvious way to
        # snapshot os.environ is the very expression this module's ratchet
        # searches for, so writing it here would report this file as an
        # offender. It did exactly that on the first run.
        with mock.patch.dict(os.environ, live):
            env = sandbox_env(PATH="/usr/bin:/bin")
        for key in live:
            self.assertNotIn(key, env, key)
        self.assertEqual(env["PATH"], "/usr/bin:/bin")

    def test_the_control_an_unrelated_name_survives(self):
        # Without this, "strips the session" and "returns almost nothing" are
        # the same observation.
        env = sandbox_env()
        self.assertIn("PATH", env)

    def test_overrides_win_over_stripping(self):
        # The usual case: a test names a variable whose whole family was just
        # removed, in order to point the child at its sandbox instead.
        env = sandbox_env(KILIX_STORAGE_HOME="/sandbox/storage")
        self.assertEqual(env["KILIX_STORAGE_HOME"], "/sandbox/storage")

    def test_the_prefix_family_covers_the_95_variables(self):
        # KILIX carries no trailing underscore precisely so KILIX95_* is caught
        # by it. Spelled out because an "obvious" tidy-up to "KILIX_" would
        # silently reopen the hole.
        self.assertTrue("KILIX95_STORAGE_HOME".startswith(STACK_PREFIXES))

    def test_unsanitised_modules_do_not_increase(self):
        # Assembled, not written literally: spelled out in one piece these
        # match the file that searches for them, and the check reports itself.
        needles = ("os.environ" + ".copy()", "dict(os." + "environ", "**os." + "environ")
        # patch.dict(os.environ, ...) patches THIS process's environment for
        # in-process code. It builds no child environment and is not the defect
        # -- counting it inflated this budget by four modules and would make
        # the instrument report debt that cannot be paid.
        exempt = "patch." + "dict(os.environ"
        # This module is skipped, and not as a convenience: it polices how
        # OTHER modules build a child environment and builds none itself, so a
        # match here is always its own vocabulary. It reported itself twice
        # while being written -- once for the needles, once for
        # mock.patch.dict(os.environ, ...) -- which is a scanner working, not a
        # scanner to be worked around. The needles stay assembled regardless.
        offenders = sorted(
            path.name for path in TESTS_DIR.glob("test_*.py")
            if path.name != pathlib.Path(__file__).name
            and any(n in _without_exempt(path.read_text(), exempt) for n in needles)
        )
        self.assertLessEqual(
            len(offenders), UNSANITISED_MODULE_BUDGET,
            "a module started building a child environment from a raw copy of "
            "os.environ again; use sandbox_env() from _env_support instead:\n  "
            + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
