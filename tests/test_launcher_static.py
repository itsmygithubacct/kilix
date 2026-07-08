import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class KilixLauncherTests(unittest.TestCase):
    def test_launcher_parses(self):
        subprocess.run(["bash", "-n", "kilix"], cwd=ROOT, check=True)

    def test_desktop_provider_knobs_are_wired(self):
        text = (ROOT / "kilix").read_text()
        for provider in ["auto)", "builtin)", "external)", "command|custom)", "none|off|disabled)"]:
            self.assertIn(provider, text)
        self.assertIn("KILIX_DESKTOP_COMMAND", text)
        self.assertIn("KILIX_DESKTOP_NAME", text)
        self.assertIn('sh -c "$KILIX_DESKTOP_COMMAND"', text)
        self.assertIn('--tab-title "${KILIX_DESKTOP_NAME:-desktop}"', text)

    def test_update_supports_kilix_ref(self):
        text = (ROOT / "kilix").read_text()
        self.assertIn('if [ -n "${KILIX_REF:-}" ]; then', text)
        self.assertIn('git -C "$KILIX_HOME" checkout --detach "$KILIX_REF"', text)

    def test_ls_lists_live_tabs_via_kitty_remote_control(self):
        launcher = (ROOT / "kilix").read_text()
        remote = (ROOT / "config" / "remote.py").read_text()
        self.assertIn("ls|focus|watch)", launcher)
        self.assertIn('KILIX_KITTEN="$KITTEN" exec python3 "$KILIX_HOME/config/remote.py"', launcher)
        self.assertIn('run_kitten(["ls"])', remote)
        self.assertIn('"--panes"', remote)
        self.assertIn('"TAB_ID"', remote)
        self.assertIn('"PANE_ID"', remote)
        self.assertIn('"foreground_processes"', remote)

    def test_focus_watch_and_mux_commands_are_wired(self):
        launcher = (ROOT / "kilix").read_text()
        remote = (ROOT / "config" / "remote.py").read_text()
        self.assertIn('if [ "${1:-}" = "mux" ]; then', launcher)
        self.assertIn('"$KITTEN" @ launch --type=tab', launcher)
        self.assertIn('a|attach)', launcher)
        self.assertIn('-- "$_self" serve "$_mux_name"', launcher)
        self.assertIn('exec "$_self" serve "$_mux_name"', launcher)
        self.assertIn('new-session -A -s "$_session"', launcher)
        self.assertIn('"focus-tab"', remote)
        self.assertIn('"focus-window"', remote)
        self.assertIn('"get-text"', remote)
        self.assertIn('"--interval"', remote)
        self.assertIn("refusing to watch the current pane", remote)

    def test_external_kilix95_clone_uses_array(self):
        text = (ROOT / "kilix").read_text()
        self.assertIn("_clone_args=()", text)
        self.assertIn('git clone "${_clone_args[@]}" "$_k95_repo" "$_k95_dir"', text)
        self.assertNotIn("${KILIX95_BRANCH:+", text)

    def test_prebuilt_bootstrap_can_pin_and_verify(self):
        text = (ROOT / "bootstrap.sh").read_text()
        self.assertIn("KILIX_PREBUILT_VERSION", text)
        self.assertIn("KILIX_PREBUILT_SHA256", text)
        self.assertIn(".kitty.txz.sha256", text)
        self.assertIn("sha256sum -c --status", text)


if __name__ == "__main__":
    unittest.main()
