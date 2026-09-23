"""The X test sandbox itself, without an X server.

Two properties the X modules rely on and cannot see for themselves:

* the sandboxed child really is cut off from the invoking session -- no
  inherited ``DISPLAY`` or ``XAUTHORITY``, a network namespace of its own
  with no X11 listener in it, and a private ``/tmp/.X11-unix``;
* a sandbox that cannot be built is reported as an error naming
  ``X11 SANDBOX UNAVAILABLE``, not as a quiet skip, unless the caller opts
  out explicitly with ``KILIX_X11_SANDBOX_OPTIONAL=1``.

No X server is started and no connection is opened: ``DISPLAY`` below is only
a string the child must not receive.
"""

import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import x11_sandbox  # noqa: E402
from _env_support import sandbox_env  # noqa: E402

SELFCHECK = "x11_sandbox_selfcheck"


@unittest.skipUnless(shutil.which("Xvfb"),
                     "the X tests that need the sandbox are skipped too")
class SandboxedChildTests(unittest.TestCase):
    def test_the_child_is_cut_off_from_the_invoking_session(self):
        planted = {"DISPLAY": ":47", "XAUTHORITY": "/nonexistent/cookie",
                   "KILIX_DATA_HOME": "/nonexistent/live/kilix/data",
                   "GPU_TERMINAL_HOME": "/nonexistent/live"}
        with mock.patch.dict(os.environ, planted):
            results = x11_sandbox._run_module_in_child(SELFCHECK)
        prefix = f"{SELFCHECK}.InsideTheSandbox."
        names = ("test_no_display_or_cookie_was_inherited",
                 "test_the_network_namespace_is_private_and_holds_no_x11_listener",
                 "test_the_harness_precondition_holds")
        self.assertEqual(sorted(results), sorted(prefix + n for n in names))
        for name in names:
            with self.subTest(name=name):
                record = results[prefix + name]
                self.assertEqual(record["outcome"], "success", record["detail"])


class UnavailableSandboxTests(unittest.TestCase):
    def _run_proxy(self, extra_env):
        # Defined here, not at module level, so discovery never collects it.
        class Stand(unittest.TestCase):
            """A stand-in for an X test; the sandbox never gets to run it."""

            def test_one(self):
                raise AssertionError("the real test must not run in the parent")

        module = f"{__name__}.planted-unavailable"
        loader = unittest.TestLoader()
        suite = x11_sandbox.sandbox_load_tests(
            loader, loader.loadTestsFromTestCase(Stand), module)
        result = unittest.TestResult()
        planted = x11_sandbox.SandboxUnavailable("planted: no namespaces")
        env = sandbox_env()
        env.update(extra_env)
        with mock.patch.object(x11_sandbox, "_run_module_in_child",
                               side_effect=planted), \
                mock.patch.dict(x11_sandbox._RESULTS, clear=True), \
                mock.patch.dict(os.environ, env, clear=True):
            suite.run(result)
        return result

    def test_an_unbuildable_sandbox_is_an_error_not_a_skip(self):
        result = self._run_proxy({})
        self.assertEqual((result.testsRun, len(result.errors),
                          len(result.failures), len(result.skipped)),
                         (1, 1, 0, 0))
        detail = result.errors[0][1]
        self.assertIn("SandboxUnavailable", detail)
        self.assertIn("X11 SANDBOX UNAVAILABLE", detail)
        self.assertIn("planted: no namespaces", detail)

    def test_the_explicit_opt_out_records_a_named_skip(self):
        result = self._run_proxy({x11_sandbox.OPTIONAL_ENV: "1"})
        self.assertEqual((result.testsRun, len(result.errors),
                          len(result.failures), len(result.skipped)),
                         (1, 0, 0, 1))
        reason = result.skipped[0][1]
        self.assertTrue(reason.startswith("X11 SANDBOX UNAVAILABLE"), reason)
        self.assertIn(x11_sandbox.OPTIONAL_ENV, reason)


if __name__ == "__main__":
    unittest.main()
