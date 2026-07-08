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

    def test_screen_size_command_is_wired(self):
        launcher = (ROOT / "kilix").read_text()
        self.assertIn("screen-size|font-size)", launcher)
        self.assertIn("_kilix_screen_size_cmd", launcher)
        self.assertIn("font_size", launcher)
        self.assertIn("KILIX_CONFIG_DIRECTORY", launcher)
        self.assertIn("set-font-size --all", launcher)
        self.assertIn("load_config_file", launcher)

    def test_clickable_chrome_battery_indicator_is_wired(self):
        battery = (ROOT / "src" / "kitty" / "kilix_battery.py").read_text()
        tabbar = (ROOT / "src" / "kitty" / "tab_bar.py").read_text()
        tabs = (ROOT / "src" / "kitty" / "tabs.py").read_text()
        conf = (ROOT / "config" / "kitty.conf").read_text()
        readme = (ROOT / "README.md").read_text()

        self.assertIn("KILIX_CHROME_BATTERY", battery)
        self.assertIn("KILIX_BATTERY_SUPPLY_DIR", battery)
        self.assertIn("s.lower() == 'discharging'", battery)
        self.assertIn("BATTERY_TOGGLE_ACTION", battery)
        self.assertIn("kilix_toggle_battery_percent", battery)
        self.assertIn("battery_segment", tabbar)
        self.assertIn("action_at", tabbar)
        self.assertIn("toggle_battery_percent", tabs)
        self.assertIn("U+F0079", conf)
        self.assertIn("U+F0083", conf)
        self.assertIn("Battery-in-chrome", readme)

    def test_title_bar_screen_tracks_font_resize(self):
        window = (ROOT / "src" / "kitty" / "window.py").read_text()
        titlebar = (ROOT / "src" / "kitty" / "window_title_bar.py").read_text()
        self.assertIn("self.cell_height = cell_height", titlebar)
        self.assertIn("self._title_bar_screen.cell_width != cell_width", window)
        self.assertIn("self._title_bar_screen.cell_height != cell_height", window)

    def test_clickable_chrome_font_size_buttons_are_local(self):
        titlebar = (ROOT / "src" / "kitty" / "window_title_bar.py").read_text()
        readme = (ROOT / "README.md").read_text()

        self.assertIn("' + ', 'change_font_size current +2.0'", titlebar)
        self.assertIn("' - ', 'change_font_size current -2.0'", titlebar)
        self.assertNotIn("' + ', 'change_font_size all +2.0'", titlebar)
        self.assertNotIn("' - ', 'change_font_size all -2.0'", titlebar)
        self.assertIn("`+` | increase font size for this Kilix window", readme)
        self.assertIn("`-` | decrease font size for this Kilix window", readme)

    def test_bell_is_quiet_by_default_but_visible_in_chrome(self):
        conf = (ROOT / "config" / "kitty.conf").read_text()
        settings = (ROOT / "desktop" / "apps" / "settings.py").read_text()

        self.assertIn("enable_audio_bell              no", conf)
        self.assertIn("window_alert_on_bell           no", conf)
        self.assertIn('bell_on_tab                    "● "', conf)
        self.assertIn("{fmt.fg.red}{bell_symbol}", conf)
        self.assertIn("{fmt.fg.yellow}{activity_symbol}", conf)
        self.assertIn('("enable_audio_bell", "Audio bell", "bool", "no")', settings)

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
