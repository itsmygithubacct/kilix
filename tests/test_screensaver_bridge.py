"""ScreenSaver ownership, bounds and recovery using independent real clients."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'config'))
from kilix_sdk.mpv_inhibit import PlayerInhibitor, prepare_player, playing

try:
    from gi.repository import Gio, GLib
except ImportError:
    Gio = None


class ScreenSaverTests(unittest.TestCase):
    @unittest.skipUnless(Gio and shutil.which('dbus-run-session'), 'GI and private D-Bus required')
    def test_real_two_bus_caller_and_service_lifecycle(self):
        result = subprocess.run(['dbus-run-session', '--', '/usr/bin/python3',
                                 str(ROOT / 'tests/fixtures/screensaver_bridge_fixture.py'),
                                 'host', str(ROOT / 'config/kilix_sdk/portal_bridge.py')],
                                capture_output=True, text=True, timeout=50)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertNotIn('CRITICAL', result.stderr)
        proof = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(proof, dict.fromkeys((
            'both_paths_caller_connections_private_cookies_and_pinned_owner',
            'cross_caller_invalid_signatures_and_explicit_lock_while_inhibited',
            'no_reply_inhibit_does_not_leak_and_uninhibit_is_acknowledged',
            'owner_restart_invalidates_cookies_and_unicasts_losses',
            'owner_loss_even_if_original_daemon_stays_connected_releases',
            'caller_disconnect_releases_physical_connection',
            'timeouts_late_reply_no_reply_bad_reply_and_cookie_collision_fail_closed',
            'caller_death_during_pending_inhibit_releases',
            'independent_caller_sigkill_before_and_after_inhibit_reply_releases',
            'queued_call_limit_timeout_cleanup_and_no_retained_messages',
            'client_cookie_total_limits_recover_and_allow_lock',
            'cookie_exhaustion_never_wraps_or_reuses_authority',
            'graceful_shutdown_hard_relay_death_and_restart_do_not_inherit_cookies',
            'production_launcher_primary_sigkill_kills_relay_and_releases',
            'physical_bus_death_and_same_address_restart_erase_old_authority'), True))

    def test_private_mpv_keeps_user_scripts_config_arguments_and_separator(self):
        env = {'KILIX_PRIVATE_XAPP': '1', 'KILIX_PORTAL_HOST_BUS': 'unix:path=/owned'}
        argv = ['/usr/bin/mpv', '--no-config', '--script=/user.lua', '--stop-screensaver=no',
                '--', '--script=filename.mp4']
        adapted = prepare_player(argv, env)
        self.assertEqual(adapted[:4], argv[:4])
        self.assertEqual(adapted[5:], argv[4:])
        self.assertEqual(adapted[4], '--script=' + str(ROOT / 'config/kilix_sdk/mpv_screensaver.run'))
        self.assertEqual(argv[4], '--')
        for command, environment in ((argv, {}), (argv, {'KILIX_PRIVATE_XAPP': '1'}),
                                      (['smplayer', 'movie.mp4'], env)):
            self.assertEqual(prepare_player(command, environment), command)

    def test_installed_native_script_executes_without_touching_non_private_players(self):
        import os
        env = {name: value for name, value in os.environ.items()
               if name not in ('KILIX_PRIVATE_XAPP', 'KILIX_PORTAL_HOST_BUS')}
        result = subprocess.run([str(ROOT / 'config/kilix_sdk/mpv_screensaver.run')],
                                env=env, capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')

    @unittest.skipUnless(Gio, 'GI required')
    def test_mpv_loss_before_inhibit_callback_cannot_commit_stale_authority(self):
        helper = PlayerInhibitor.__new__(PlayerInhibitor)
        helper.GLib = GLib
        helper.bus = object()
        helper.cookie = helper.early_loss = 0
        helper.pending = True
        results = []
        helper.failed = lambda: results.append('lost')
        helper.reconcile = lambda: results.append('committed')
        helper.lost(helper.bus, None, None, None, None, GLib.Variant('(u)', (17,)))
        helper.inhibited(SimpleNamespace(call_finish=lambda _result: GLib.Variant('(u)', (17,))),
                         None, helper.bus)
        self.assertEqual(results, ['lost'])
        self.assertEqual(helper.cookie, 0)
        results.clear()
        helper.cookie, helper.pending = 19, False
        helper.lost(object(), None, None, None, None, GLib.Variant('(u)', (19,)))
        helper.lost(helper.bus, None, None, None, None, GLib.Variant('(u)', (20,)))
        self.assertEqual(results, [])
        helper.lost(helper.bus, None, None, None, None, GLib.Variant('(u)', (19,)))
        self.assertEqual(results, ['lost'])

    def test_only_active_video_with_enabled_screensaver_policy_inhibits(self):
        active = {'pause': False, 'paused-for-cache': False, 'idle-active': False,
                  'eof-reached': False, 'current-vo': 'xv', 'video-out-params': {'w': 640},
                  'options/stop-screensaver': True}
        self.assertTrue(playing(active))
        for name, value in (('pause', True), ('paused-for-cache', True), ('idle-active', True),
                            ('eof-reached', True), ('video-out-params', None), ('current-vo', 'null'),
                            ('options/stop-screensaver', False)):
            with self.subTest(name=name):
                self.assertFalse(playing({**active, name: value}))
        for name in active:
            self.assertFalse(playing({key: value for key, value in active.items() if key != name}))


if __name__ == '__main__':
    unittest.main()
