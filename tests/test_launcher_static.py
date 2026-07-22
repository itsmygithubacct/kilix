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
        self.assertIn("'FETCH_HEAD^{commit}'", text)
        self.assertIn('git -C "$KILIX_HOME" checkout --detach "$_target"', text)
        self.assertIn("status --porcelain --untracked-files=normal", text)
        self.assertIn("KILIX_TRUST_EXISTING_CHECKOUT", text)
        self.assertIn('merge --ff-only "origin/$_branch"', text)
        self.assertIn("fork rebuild failed", text)

    def test_ls_lists_live_tabs_via_kitty_remote_control(self):
        launcher = (ROOT / "kilix").read_text()
        remote = (ROOT / "config" / "remote.py").read_text()
        self.assertIn("ls|focus|watch|fullscreen)", launcher)
        self.assertIn('KILIX_KITTEN="$KITTEN" exec python3 "$KILIX_HOME/config/remote.py"', launcher)
        self.assertIn('run_kitten(["ls"], authenticated=True)', remote)
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

    def test_runtime_config_is_outside_the_checkout(self):
        launcher = (ROOT / "kilix").read_text()
        settings = (ROOT / "desktop" / "apps" / "settings.py").read_text()
        self.assertIn('KILIX_USER_CONFIG_DIRECTORY="$KILIX_CONFIG_HOME"', launcher)
        self.assertIn("include .kilix-defaults.conf", launcher)
        self.assertIn("_kilix_refresh_managed_link", launcher)
        self.assertNotIn("_kilix_finish_config_migration", launcher)
        self.assertNotIn("_kilix_migrate_directory", launcher)
        self.assertIn('export KILIX_ENV_CONFIG=', launcher)
        self.assertIn('from kilix_sdk.paths import config_dir', settings)
        self.assertIn('KILIX_CACHE_HOME="${KILIX_CACHE_HOME:-$KILIX_STORAGE_HOME/cache}"', launcher)
        self.assertIn('FORK="$KILIX_BUILD_DIRECTORY/current/src/kitty/launcher/kitty"', launcher)

    def test_source_and_provider_storage_are_separate(self):
        launcher = (ROOT / "kilix").read_text()
        settings = (ROOT / "desktop" / "apps" / "settings.py").read_text()
        self.assertIn(
            'GPU_TERMINAL_SOURCE_HOME="${GPU_TERMINAL_SOURCE_HOME:-$HOME/gpu_terminal}"',
            launcher)
        self.assertIn(
            'KILIX95_DIR="${KILIX95_DIR:-$GPU_TERMINAL_SOURCE_HOME/kilix-95}"',
            launcher)
        self.assertIn(
            'KILIX95_STORAGE_HOME="${KILIX95_STORAGE_HOME:-$GPU_TERMINAL_HOME/kilix-95}"',
            launcher)
        self.assertIn('--env "KILIX95_DATA_HOME=$KILIX95_DATA_HOME"', launcher)
        self.assertIn('default="~/gpu_terminal/kilix-95"', settings)

    def test_runtime_roots_are_not_loaded_from_persisted_config(self):
        launcher = (ROOT / "kilix").read_text()
        loader = launcher.split("_kilix_load_env_file()", 1)[1].split(
            "_kilix_load_env_file \"$KILIX_DEFAULT_CONFIG_DIRECTORY", 1)[0]
        self.assertNotIn("KILIX_STORAGE_HOME|", loader)
        self.assertNotIn("KILIX95_STORAGE_HOME|", loader)
        self.assertIn("_KILIX_ENV_EXPLICIT", loader)
        self.assertIn("[[ ! -v $_key ]]", loader)

    def test_authoritative_provider_contract_is_enforced(self):
        launcher = (ROOT / "kilix").read_text()
        self.assertIn("check-desktop-provider.py", launcher)
        self.assertIn("external authoritative provider", launcher)
        self.assertIn("KILIX95_ALLOW_MUTABLE_REF", launcher)
        self.assertIn("KILIX95_ALLOW_UNPINNED_INSTALL", launcher)
        self.assertIn('fetch --force origin "$KILIX95_REF"', launcher)
        self.assertIn("'FETCH_HEAD^{commit}'", launcher)

    def test_external_provider_gets_pinned_state_library(self):
        launcher = (ROOT / "kilix").read_text()
        helper = (ROOT / "scripts" / "build-state-library.sh").read_text()
        self.assertIn('"$KILIX_HOME/scripts/build-state-library.sh"', launcher)
        self.assertIn('export KILIX_STATE_LIBRARY', launcher)
        self.assertIn('--env "KILIX_STATE_LIBRARY=${KILIX_STATE_LIBRARY:-}"',
                      launcher)
        self.assertIn('third_party/kilix-state', helper)
        self.assertIn('BUILD_DIR="$STATE_BUILD"', helper)

    def test_clickable_chrome_status_items_are_wired(self):
        battery = (ROOT / "src" / "kitty" / "kilix_battery.py").read_text()
        tabbar = (ROOT / "src" / "kitty" / "tab_bar.py").read_text()
        tabs = (ROOT / "src" / "kitty" / "tabs.py").read_text()
        conf = (ROOT / "config" / "kitty.conf").read_text()
        readme = (ROOT / "README.md").read_text()

        self.assertIn("KILIX_CHROME_CLOCK", battery)
        self.assertIn("KILIX_CHROME_VOLUME", battery)
        self.assertIn("KILIX_CHROME_NETWORK", battery)
        self.assertIn("KILIX_CHROME_CALENDAR", battery)
        self.assertIn("KILIX_CHROME_CLOCK_FORMAT", battery)
        self.assertIn("clock_segment", battery)
        self.assertIn("clock_segments", battery)
        self.assertIn("CALENDAR_WIDGET_ACTION", battery)
        self.assertIn("DATE_WIDGET_ACTION", battery)
        self.assertIn("VOLUME_WIDGET_ACTION", battery)
        self.assertIn("NETWORK_WIDGET_ACTION", battery)
        self.assertIn("volume_segment", battery)
        self.assertIn("network_segment", battery)
        self.assertIn("ensure_chrome_timers", battery)
        self.assertIn("KILIX_CHROME_BATTERY", battery)
        self.assertIn("KILIX_BATTERY_SUPPLY_DIR", battery)
        self.assertIn("s.lower() == 'discharging'", battery)
        self.assertIn("BATTERY_TOGGLE_ACTION", battery)
        self.assertIn("kilix_toggle_battery_percent", battery)
        self.assertIn("_BATTERY_SHOW_PERCENT = True", battery)
        self.assertIn("f' {info.percent:3d}% {glyph} '", battery)
        self.assertIn("clock_segment", tabbar)
        self.assertIn("volume_segment", tabbar)
        self.assertIn("network_segment", tabbar)
        self.assertIn("battery_segment", tabbar)
        self.assertIn("right_status_start", tabbar)
        self.assertIn("right_status_width", tabbar)
        self.assertIn("action_at", tabbar)
        self.assertIn("get_options().foreground", tabbar)
        self.assertIn("run_kitten_with_metadata('kilix_clock'", tabs)
        self.assertIn("which('pulsemixer') or which('alsamixer')", tabs)
        self.assertIn("Volume Control", tabs)
        self.assertIn("which('nmtui')", tabs)
        self.assertIn("Network Connections", tabs)
        self.assertTrue((ROOT / "src" / "kittens" / "kilix_clock" / "main.py").is_file())
        self.assertIn("toggle_battery_percent", tabs)
        self.assertIn("U+F073", conf)
        self.assertIn("U+F028", conf)
        self.assertIn("U+F1EB", conf)
        self.assertIn("U+F0079", conf)
        self.assertIn("U+F0083", conf)
        self.assertIn("Battery-in-chrome", readme)
        self.assertIn("Date/time-in-chrome", readme)

    def test_title_bar_screen_tracks_font_resize(self):
        window = (ROOT / "src" / "kitty" / "window.py").read_text()
        titlebar = (ROOT / "src" / "kitty" / "window_title_bar.py").read_text()
        self.assertIn("self.cell_height = cell_height", titlebar)
        self.assertIn("self._title_bar_screen.cell_width != cell_width", window)
        self.assertIn("self._title_bar_screen.cell_height != cell_height", window)

    def test_software_mouse_cursor_stays_visible_in_inactive_pane(self):
        shader = (ROOT / "src" / "kitty" / "cell_vertex.glsl").read_text()

        # Kitty makes an inactive pane's text cursor transparent. The software
        # mouse pointer shares the cursor drawing path, but must not share that
        # focus-dependent opacity because Kilix hides the native pointer.
        self.assertIn("float has_cursor, has_block_cursor, has_mouse_cursor;", shader)
        self.assertIn(
            "float cell_cursor_opacity = max(cursor_opacity, cell_data.has_mouse_cursor);",
            shader,
        )
        self.assertIn(
            "cell_data.cursor.bg * cell_cursor_opacity, cell_cursor_opacity",
            shader,
        )
        self.assertIn(
            "mix(bg, cell_data.cursor.bg, cell_cursor_opacity)",
            shader,
        )

    def test_fullscreen_is_content_only_and_restores_chrome_on_exit(self):
        state = (ROOT / "src" / "kitty" / "state.c").read_text()
        monitor = (ROOT / "src" / "kitty" / "child-monitor.c").read_text()
        window = (ROOT / "src" / "kitty" / "window.py").read_text()
        tabbar = (ROOT / "src" / "kitty" / "tab_bar.py").read_text()
        pyi = (ROOT / "src" / "kitty" / "fast_data_types.pyi").read_text()
        fork_glfw_tests = (ROOT / "src" / "kitty_tests" / "glfw.py").read_text()
        fork_window_tests = (ROOT / "src" / "kitty_tests" / "window.py").read_text()

        self.assertIn("!is_os_window_fullscreen(os_window) && !OPT(tab_bar_hidden)", state)
        self.assertIn("is_os_window_fullscreen(os_window) || os_window->num_tabs == 0", state)
        self.assertIn("MW(is_os_window_fullscreen, METH_VARARGS)", state)
        self.assertIn("const bool content_only_fullscreen = is_os_window_fullscreen(os_window)", monitor)
        self.assertIn("not is_os_window_fullscreen(self.os_window_id)", window)
        self.assertIn("def is_os_window_fullscreen(os_window_id: int) -> bool", pyi)
        self.assertIn("set_tab_bar_render_data(self.os_window_id, self.screen, 0, 0, 0, 0)", tabbar)
        self.assertIn("test_fullscreen_state_change_relayouts_without_resize", fork_glfw_tests)
        self.assertIn("test_remote_fullscreen_exit_is_not_an_error", fork_glfw_tests)
        self.assertIn("test_title_bar_visibility_restores_after_fullscreen", fork_window_tests)

    def test_clickable_chrome_font_size_buttons_are_local(self):
        titlebar = (ROOT / "src" / "kitty" / "window_title_bar.py").read_text()
        readme = (ROOT / "README.md").read_text()

        self.assertIn("' + ', 'change_font_size current +2.0'", titlebar)
        self.assertIn("' - ', 'change_font_size current -2.0'", titlebar)
        self.assertNotIn("' + ', 'change_font_size all +2.0'", titlebar)
        self.assertNotIn("' - ', 'change_font_size all -2.0'", titlebar)
        self.assertIn("`+` | increase font size for this Kilix window", readme)
        self.assertIn("`-` | decrease font size for this Kilix window", readme)

    def test_page_strip_wraps_after_thirty_visible_items(self):
        tabbar = (ROOT / "src" / "kitty" / "tab_bar.py").read_text()
        tabs = (ROOT / "src" / "kitty" / "tabs.py").read_text()
        boss = (ROOT / "src" / "kitty" / "boss.py").read_text()
        state = (ROOT / "src" / "kitty" / "state.c").read_text()
        state_h = (ROOT / "src" / "kitty" / "state.h").read_text()
        optdef = (ROOT / "src" / "kitty" / "options" / "definition.py").read_text()
        toc = (ROOT / "src" / "kitty" / "options" / "to-c-generated.h").read_text()

        self.assertRegex(tabbar, r"MAX_TABS_PER_ROW\s*=\s*30\b")
        self.assertIn("visible_items_in_row >= MAX_TABS_PER_ROW", tabbar)
        self.assertIn("visible_items_in_row += 1", tabbar)
        self.assertIn("rows = split_tab_bar_rows(data)[:s.lines]", tabbar)
        self.assertIn("def tab_id_at(self, x: int, y: int)", tabbar)
        self.assertIn("def action_at(self, x: int, y: int)", tabbar)
        self.assertNotIn("TAB_CLOSE_ACTION", tabbar)
        self.assertNotIn("kilix_close_active_app_tab", tabbar)
        self.assertNotIn("GLFW_MOUSE_BUTTON_MIDDLE", tabs)
        self.assertIn("#define KILIX_TAB_BAR_TABS_PER_ROW 30", state)
        self.assertIn("tab_bar_item_count = os_window->num_tabs + (OPT(tab_bar_show_new_tab_button) ? 1u : 0u)", state)
        self.assertIn("tab_bar_rows_for_count(tab_bar_item_count)", state)
        self.assertIn("tab_bar_content_height", state)
        self.assertIn("bool tab_bar_show_new_tab_button", state_h)
        self.assertIn("opt('tab_bar_show_new_tab_button', 'no', option_type='to_bool', ctype='bool'", optdef)
        self.assertIn("convert_from_opts_tab_bar_show_new_tab_button", toc)
        self.assertIn("self.tab_bar.tab_id_at(int(x), int(y))", tabs)
        self.assertIn("tm.tab_bar.tab_id_at(x, y)", boss)

    def test_bell_is_quiet_by_default_but_visible_in_chrome(self):
        conf = (ROOT / "config" / "kitty.conf").read_text()
        settings = (ROOT / "desktop" / "apps" / "settings.py").read_text()

        self.assertIn("enable_audio_bell              no", conf)
        self.assertIn("window_alert_on_bell           no", conf)
        self.assertIn('bell_on_tab                    "● "', conf)
        self.assertIn("{fmt.fg.red}{bell_symbol}", conf)
        self.assertIn("{fmt.fg.yellow}{activity_symbol}", conf)
        self.assertIn('K("enable_audio_bell", "Audio bell", "bool", "no")', settings)
        self.assertIn('K("window_alert_on_bell", "Urgency on bell", "bool", "no")', settings)

    def test_runtime_and_shared_settings_are_gui_backed(self):
        launcher = (ROOT / "kilix").read_text()
        settings = (ROOT / "desktop" / "apps" / "settings.py").read_text()
        env_conf = (ROOT / "config" / "kilix.env").read_text()
        shared = (ROOT / "config" / "kilix_sdk" / "settings.py").read_text()
        tui = (ROOT / "kilix-settings").read_text()

        self.assertIn("KILIX_ENV_CONFIG", launcher)
        self.assertIn("kilix.env", launcher)
        self.assertIn("GPU_TERMINAL_SETTINGS_FILE", launcher)
        self.assertIn('python3 "$KILIX_HOME/kilix-settings" --ensure', launcher)
        self.assertIn("KILIX_CHROME_CLOCK|KILIX_CHROME_CLOCK_FORMAT", launcher)
        self.assertIn("KILIX_DESKTOP_PROVIDER|KILIX_DESKTOP_COMMAND", launcher)
        self.assertIn("KILIX_DESKTOP_FLAVOR", launcher)
        self.assertIn("KILIX_RUN_AUTO_FIT|KILIX_NO_PANE", launcher)
        self.assertIn("KILIX_BROWSE_BACKEND", launcher)
        self.assertIn("KILIX_SHELL_INTEGRATION", launcher)
        self.assertIn("KILIX_NO_SOUND|KILIX_XPANE_WM", launcher)
        self.assertIn("SETTING_PAGES", settings)
        for key in (
            "KILIX_CHROME_VOLUME", "KILIX_CHROME_NETWORK",
            "KILIX_CHROME_CALENDAR",
            "KILIX_CHROME_CLOCK", "KILIX_CHROME_BATTERY",
            "KILIX_CHROME_BUTTON_FONT_INCREASE",
            "KILIX_CHROME_BUTTON_SPLIT_LEFT",
            "KILIX_CHROME_BUTTON_CLOSE",
            "KILIX_DESKTOP_PROVIDER", "KILIX_DESKTOP_FLAVOR", "KILIX95_AUTO_INSTALL",
            "KILIX_RUN_AUTO_FIT", "KILIX_BROWSE_BACKEND", "KILIX_HW", "KILIX_DESKTOP_DIR",
            "KILIX_NO_SOUND", "KILIX_SHELL_INTEGRATION", "KILIX_REF",
        ):
            self.assertIn(key, settings)
        self.assertIn("get_env_key", settings)
        self.assertIn("set_env_key", settings)
        self.assertIn("shared_settings", settings)
        self.assertIn("settings.conf", env_conf)
        self.assertIn("TOP_BAR_TOGGLES", shared)
        self.assertIn("PANE_BUTTON_TOGGLES", shared)
        self.assertIn("curses.wrapper", tui)
        self.assertIn("KILIX_DESKTOP_FLAVOR=xp", env_conf)
        self.assertIn("KILIX_BROWSE_BACKEND=presenter", env_conf)

    def test_browser_defaults_to_shared_frame_presenter(self):
        launcher = (ROOT / "kilix").read_text()
        self.assertIn('_browse_backend=${KILIX_BROWSE_BACKEND:-presenter}', launcher)
        self.assertIn('presenter|python)', launcher)
        self.assertIn('python3 "$KILIX_HOME/config/browse.py"', launcher)
        self.assertIn('KILIX_BROWSE_BACKEND=go requires the built-in browse kitten', launcher)

    def test_focus_watch_and_mux_commands_are_wired(self):
        launcher = (ROOT / "kilix").read_text()
        remote = (ROOT / "config" / "remote.py").read_text()
        shell = (ROOT / "desktop" / "shell.py").read_text()
        self.assertIn('if [ "${1:-}" = "mux" ]; then', launcher)
        self.assertIn('@ --password-file "$KILIX_RC_PASSWORD_FILE"', launcher)
        self.assertIn('launch --type=tab --cwd=current --self', launcher)
        self.assertIn('a|attach)', launcher)
        self.assertIn('-- "$_self" serve "$_mux_name"', launcher)
        self.assertIn('exec "$_self" serve "$_mux_name"', launcher)
        self.assertIn('new-session -A -s "$_session"', launcher)
        self.assertIn('"focus-tab"', remote)
        self.assertIn('"focus-window"', remote)
        self.assertIn('"get-text"', remote)
        self.assertIn('def cmd_fullscreen', remote)
        self.assertIn('"resize-os-window", "--self", "--action", "toggle-fullscreen"', remote)
        self.assertIn('"--interval"', remote)
        self.assertIn("refusing to watch the current pane", remote)
        self.assertIn('"Mux Terminal"', shell)
        self.assertIn('def open_mux_terminal', shell)

    def test_remote_control_is_policy_restricted(self):
        conf = (ROOT / "config" / "kitty.conf").read_text()
        policy = (ROOT / "config" / "kilix_rc_auth.py").read_text()
        remote = (ROOT / "config" / "remote.py").read_text()
        settings = (ROOT / "desktop" / "apps" / "settings.py").read_text()
        self.assertIn("allow_remote_control           password", conf)
        self.assertIn('remote_control_password        "" kilix_rc_auth.py', conf)
        self.assertIn('payload.get("self") is True', policy)
        self.assertIn('payload.get("action") == "toggle-fullscreen"', policy)
        self.assertIn("not from_socket", policy)
        self.assertIn("via_tty=True", remote)
        self.assertIn("_kilix_init_rc_password", (ROOT / "kilix").read_text())
        self.assertIn('"%s" launch ls focus-window focus-tab get-text',
                      (ROOT / "kilix").read_text())
        self.assertNotIn('command == "launch"', policy)
        self.assertNotIn('command == "get-text"', policy)
        self.assertIn('["password", "no", "yes"]', settings)

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

    def test_fork_build_has_no_mutable_default_bundle(self):
        text = (ROOT / "build.sh").read_text()
        self.assertIn("KILIX_BUILD_MODE", text)
        self.assertIn("refusing mutable kitty CI dependency URL", text)
        self.assertNotIn(
            "deps_url=\"${KILIX_KITTY_DEPS_URL:-https://download.calibre-ebook.com",
            text,
        )
        self.assertIn("_sysconfigdata_", text)
        self.assertIn("libfontconfig.so", text)

    def test_build_dependency_installer_covers_pinned_kitty_headers(self):
        text = (ROOT / "scripts" / "install-build-deps.sh").read_text()
        for package in ("libsimde-dev", "libwayland-dev", "wayland-protocols"):
            self.assertIn(package, text)
        self.assertIn("wayland-client wayland-cursor wayland-egl wayland-protocols",
                      text)
        self.assertIn("#include <simde/x86/avx2.h>", text)
        self.assertIn("KILIX_PYTHON", text)
        self.assertIn("need >= 3.12", text)
        self.assertIn("return 1", text)


if __name__ == "__main__":
    unittest.main()
