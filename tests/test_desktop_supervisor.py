"""The desktop provider is restarted after an abnormal exit, never after a clean one."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = (ROOT / "kilix").read_text()
SUPERVISOR = LAUNCHER.split("# BEGIN DESKTOP SUPERVISOR\n", 1)[1].split(
    "# END DESKTOP SUPERVISOR\n", 1)[0]

PROVIDER = """import json, os, sys
from pathlib import Path
base = Path(sys.argv[1])
statuses = json.loads((base / 'statuses').read_text())
runs = base / 'runs'
n = len(runs.read_text().splitlines()) if runs.exists() else 0
with runs.open('a') as fh:
    fh.write(json.dumps({'restarted': os.environ.get('KILIX_DESKTOP_RESTARTED'),
                         'last': os.environ.get('KILIX_DESKTOP_LAST_STATUS'),
                         'home': os.environ.get('KILIX_HOME')}) + '\\n')
sys.exit(statuses[min(n, len(statuses) - 1)])
"""


class DesktopSupervisorTests(unittest.TestCase):
    def supervise(self, statuses, **env):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "statuses").write_text(json.dumps(statuses))
            (base / "provider.py").write_text(PROVIDER)
            script = ("_kilix_restore_pane_tty() { :; }\n" + SUPERVISOR
                      + 'KILIX_HOME=/fixture/home _kilix_desktop_supervise '
                        'python3 "$1/provider.py" "$1"\n')
            result = subprocess.run(
                ["bash", "-c", script, "fixture", str(base)],
                env={"PATH": os.defpath, "KILIX_DESKTOP_RESTART_DELAY": "0", **env},
                capture_output=True, text=True, timeout=60)
            runs = [json.loads(line) for line in
                    (base / "runs").read_text().splitlines()]
        return result, runs

    def test_a_crash_is_restarted_and_told_why(self):
        result, runs = self.supervise([1, 137, 0])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(runs), 3)
        self.assertEqual(runs[0]["restarted"], None)
        self.assertEqual([r["restarted"] for r in runs[1:]], ["1", "1"])
        self.assertEqual([r["last"] for r in runs[1:]], ["1", "137"])
        self.assertIn("stopped unexpectedly (status 1)", result.stderr)

    def test_the_callers_environment_still_reaches_the_provider(self):
        _result, runs = self.supervise([1, 0])
        self.assertEqual([r["home"] for r in runs], ["/fixture/home"] * 2)

    def test_clean_and_signalled_exits_end_the_session(self):
        for status in (0, 129, 130, 143):
            with self.subTest(status=status):
                result, runs = self.supervise([status, 0])
                self.assertEqual(result.returncode, status)
                self.assertEqual(len(runs), 1)

    def test_a_crash_storm_stops_after_five_restarts(self):
        result, runs = self.supervise([1])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(runs), 6)
        self.assertIn("not restarting it again", result.stderr)

    def test_restarting_can_be_turned_off(self):
        result, runs = self.supervise([1, 0], KILIX_DESKTOP_RESTART="off")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(runs), 1)

    def test_the_python_provider_runs_under_the_supervisor(self):
        self.assertIn('_kilix_desktop_supervise python3 "$_KILIX_DESKTOP_MAIN" "$@" || rc=$? ;;',
                      LAUNCHER)


if __name__ == "__main__":
    unittest.main()
