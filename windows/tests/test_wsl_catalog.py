"""Catalog transactions and real child execution without user windows/network."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import shutil
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'wsl'
sys.path.insert(0, str(SOURCE))
import catalog
import catalog_build
import catalog_native
import catalog_sources
import catalog_store
import catalog_chooser
import catalog_runtime
import games
import companion


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="catalog ' café ")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        environment = patch.dict(os.environ, GPU_TERMINAL_SETTINGS_FILE=str(self.home / 'settings.conf'))
        environment.start()
        self.addCleanup(environment.stop)
        self.root = self.home / 'state'
        self.manager = catalog_store.Catalog(root=self.root)
        self.spec = dict(name='fixture', aliases=['short'], title='Fixture', category='app',
                         runtime=['wsl', 'native'], recipe='python', source='apps/fixture',
                         executable='fixture.py', probe=['--help'])
        self.pins = {'apps/fixture': 'a' * 40}
        self.bundle = self.home / 'sources.tar.gz'
        with tarfile.open(self.bundle, 'w:gz') as archive:
            contents = {
                'source-revisions.json': json.dumps(self.pins),
                'apps/fixture/fixture.py': 'import json,sys\nprint(json.dumps(sys.argv[1:]))\n',
                'apps/fixture/LICENSE': 'Fixture license notice\n',
                'content/model': 'must not import this model',
                'receipts/acceptance.json': 'must not import this receipt',
            }
            for name, value in contents.items():
                data = value.encode()
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        self.patcher = patch.object(catalog_store, 'revisions', return_value=self.pins)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def install(self):
        return self.manager.install_item(self.spec, source_bundle=self.bundle,
                                         sha256=catalog_sources.digest(self.bundle))

    def test_shared_game_settings_are_authoritative_and_aliases_match(self):
        import catalog_settings
        settings = catalog_settings.shared_settings()
        for spec in catalog_store.entries('games'):
            key = catalog_settings.game_key(settings, spec)
            self.assertIsNotNone(key, spec['name'])
            settings.update({key: '0'})
            self.assertFalse(self.manager.enabled(spec))
            self.manager.set_enabled(spec, True)
            self.assertTrue(settings.enabled(key))
            self.assertTrue(self.manager.enabled(spec))
            self.manager.set_enabled(spec, False)
            self.assertFalse(settings.enabled(key))
            # Changing the same toggle through `kilix settings` takes effect.
            settings.update({key: '1'})
            self.assertTrue(self.manager.enabled(spec))
        self.assertEqual(catalog_settings.game_key(settings, catalog_store.find('lander')),
                         settings.GAME_KEY_BY_ID['terminal-lander'])

    def test_legacy_game_disables_survive_until_shared_choice_and_native_is_separate(self):
        import catalog_settings
        spec = catalog_store.find('pong')
        self.root.mkdir()
        catalog_store.write_state(self.root, dict(schema=1, disabled=[spec['name']], installed={}))
        self.assertFalse(self.manager.enabled(spec))
        settings = catalog_settings.shared_settings()
        settings.update({settings.GAME_KEY_BY_ID['kilix-pong']: '1'})
        self.assertTrue(self.manager.enabled(spec))
        native = catalog_store.Catalog(root=self.root, runtime='native')
        self.assertFalse(native.enabled(spec))
        native.set_enabled(spec, True)
        self.manager.set_enabled(spec, False)
        self.assertTrue(native.enabled(spec))

    def test_source_install_launch_and_binary_relocation_keep_literal_argv(self):
        first = self.install()
        argv = ['', 'space here', "quote'", '日本語', '$HOME;$(false)', 'line\nbreak']
        result = subprocess.check_output(self.manager.argv(self.spec) + argv, text=True, cwd='/')
        self.assertEqual(json.loads(result), argv)
        binary = self.home / 'binary.tar.gz'
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager.export(self.spec, binary)
        other = catalog_store.Catalog(root=self.home / 'different root')
        with patch.object(catalog_build, 'build', side_effect=AssertionError('Binary import must not compile')):
            second = other.install_item(self.spec, binary=binary, sha256=catalog_sources.digest(binary))
        self.assertNotEqual(first, second)
        self.assertEqual(json.loads(subprocess.check_output(other.argv(self.spec) + argv, text=True)), argv)
        self.assertEqual((second / 'apps/fixture/LICENSE').read_text(), 'Fixture license notice\n')

    def test_failed_update_retains_active_version_model_and_disabled_state(self):
        old = self.install()
        (self.root / 'content').mkdir()
        (self.root / 'content/model').write_bytes(b'owner model')
        self.manager.set_enabled(self.spec, False)
        before = (self.root / 'catalog-state.json').read_bytes()
        with patch.object(catalog_build, 'smoke', side_effect=ValueError('bad executable')):
            with self.assertRaisesRegex(ValueError, 'bad executable'):
                self.install()
        self.assertEqual((self.root / 'catalog-state.json').read_bytes(), before)
        self.assertEqual((self.root / 'content/model').read_bytes(), b'owner model')
        self.assertEqual(list((self.root / 'catalog').iterdir()), [old])
        new = self.install()
        self.assertNotEqual(old, new)
        self.assertTrue(old.is_dir())
        self.assertFalse(self.manager.enabled(self.spec))
        with self.assertRaisesRegex(ValueError, 'enable to launch'):
            self.manager.argv(self.spec)

    def test_source_install_never_imports_model_or_receipt_members(self):
        generation = self.install()
        for name in ('content', 'receipts'):
            self.assertFalse((self.root / name).exists())
            self.assertFalse((generation / name).exists())

    def test_bad_digest_or_pin_cannot_activate_or_execute(self):
        with patch.object(catalog_build, 'build') as build:
            with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
                self.manager.install_item(self.spec, source_bundle=self.bundle, sha256='0' * 64)
            with patch.object(catalog_store, 'revisions', return_value={'apps/fixture': 'b' * 40}):
                with self.assertRaisesRegex(ValueError, 'reviewed revisions'):
                    self.install()
            build.assert_not_called()
        self.assertFalse((self.root / 'catalog-state.json').exists())

    def test_archive_traversal_does_not_escape_staging(self):
        with tarfile.open(self.bundle, 'w:gz') as archive:
            member = tarfile.TarInfo('apps/fixture/../../../escape')
            member.size = 1
            archive.addfile(member, io.BytesIO(b'x'))
        with self.assertRaises(tarfile.TarError):
            self.install()
        self.assertFalse((self.root / 'catalog/escape').exists())

    def test_binary_platform_and_revision_must_match(self):
        generation = self.install()
        record = json.loads((generation / 'catalog-install.json').read_text())
        record['runtime'] = 'native'
        (generation / 'catalog-install.json').write_text(json.dumps(record))
        output = self.home / 'binary.tar.gz'
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager.export(self.spec, output)
        with self.assertRaisesRegex(ValueError, 'does not match'):
            self.manager.install_item(self.spec, binary=output, sha256=catalog_sources.digest(output))

    def test_readiness_is_offline_and_detects_removed_program(self):
        generation = self.install()
        with patch.object(catalog_sources, 'download', side_effect=AssertionError('No listing downloads')):
            self.assertTrue(self.manager.status(self.spec)['ready'])
            (generation / 'apps/fixture/fixture.py').unlink()
            self.assertFalse(self.manager.status(self.spec)['ready'])

    def test_native_games_are_unavailable_and_graphs_are_portable(self):
        native = catalog_store.Catalog(root=self.root, runtime='native')
        for game in catalog_store.entries('games'):
            status = native.status(game)
            self.assertFalse(status['supported'])
            self.assertFalse(status['ready'])
            self.assertIn('WSL', status['reason'])
        self.assertTrue(native.status(catalog_store.find('graphs'))['supported'])

    def test_disabled_game_cannot_fall_through_to_path_or_needle(self):
        install = self.root / 'current'
        (install / 'bin').mkdir(parents=True)
        binary = install / 'bin/kilix-pong'
        binary.write_text('#!/bin/sh\nexit 0\n')
        binary.chmod(0o755)
        manager = catalog_store.Catalog(root=self.root, install=install)
        spec = catalog_store.find('pong')
        manager.set_enabled(spec, False)
        with patch.object(catalog_store, 'default_root', return_value=self.root), \
                patch.object(companion, 'INSTALL', install), \
                patch.object(companion.shutil, 'which', return_value=str(binary)), \
                patch.object(companion, 'remote') as remote, \
                patch.object(games, 'installation', return_value=install):
            self.assertFalse(games.game_enabled('pong'))
            self.assertFalse(games.game_ready('pong'))
            with self.assertRaisesRegex(ValueError, 'enable to launch'):
                companion.main(['--launch', 'pong'])
            remote.assert_not_called()
            manager.set_enabled(spec, True)
            self.assertTrue(games.game_ready('pong'))

    def test_catalog_has_unique_aliases_and_explains_every_unavailable_entry(self):
        known = set()
        for spec in catalog_store.entries():
            for name in [spec['name'], *spec['aliases']]:
                self.assertNotIn(name, known)
                known.add(name)
            if not spec['runtime']:
                self.assertTrue(spec['unsupported'])
            self.assertTrue(spec['repository'].startswith('https://github.com/itsmygithubacct/'))
        self.assertEqual(len(catalog_store.entries('games')), 15)

    def test_chooser_dispatches_selected_entry_and_stops(self):
        with patch.object(catalog_chooser, 'entries', return_value=[self.spec]), \
                patch.object(sys.stdin, 'isatty', return_value=True), \
                patch('builtins.input', side_effect=['d 1', 'e 1', '? 1', 'q']), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(catalog_chooser.terminal(self.manager, 'apps'), 0)
        self.assertTrue(self.manager.enabled(self.spec))

    def test_native_speech_never_interpolates_text_into_powershell(self):
        text = 'hello; $(exit 7) "世界"'
        with patch.object(catalog_native.os, 'name', 'nt'), \
                patch.object(catalog_native.subprocess, 'call', return_value=0) as call:
            self.assertEqual(catalog_native.main(['windows-voice', text]), 0)
        argv = call.call_args.args[0]
        self.assertNotIn(text, argv)
        self.assertEqual(call.call_args.kwargs['env']['KILIX_CATALOG_SPEECH'], text)

    def test_managed_interpreter_is_used_for_app_argv(self):
        install = self.root / 'current'
        (install / 'bin').mkdir(parents=True)
        (install / 'bin/python3').symlink_to(Path(sys.executable).resolve())
        manager = catalog_store.Catalog(root=self.root, install=install)
        self.install()
        self.assertEqual(manager.argv(self.spec)[0], str(Path(sys.executable).resolve()))

    def test_embedded_python_launch_sets_script_argv_without_shell_parsing(self):
        script = self.home / 'literal script.py'
        script.write_text('import json,sys\nprint(json.dumps(sys.argv))\n')
        args = ['space here', "quote'", '$HOME', '世界']
        command = catalog_runtime.python_argv(script, args, python='/app/kitty-console.exe')
        self.assertEqual(command[:2], ['/app/kitty-console.exe', '+runpy'])
        # +runpy starts its code with the same argv layout as python -c.
        result = subprocess.check_output([sys.executable, '-c', *command[2:]], text=True)
        self.assertEqual(json.loads(result), [str(script), *args])

    def test_old_system_python_delegates_mutation_to_managed_interpreter(self):
        install = self.root / 'current'
        (install / 'bin').mkdir(parents=True)
        (install / 'bin/python3').write_text('managed interpreter fixture')
        with patch.object(catalog.sys, 'version_info', (3, 10)), \
                patch.object(catalog.subprocess, 'call', return_value=0) as call:
            self.assertEqual(catalog.main(['install', 'graphs'], root=self.root, install=install), 0)
        argv = call.call_args.args[0]
        self.assertEqual(argv[0], str(install / 'bin/python3'))
        self.assertEqual(argv[-3:], ['apps', 'install', 'graphs'])

    def test_missing_source_dependencies_fail_before_download(self):
        with patch.object(catalog_build, 'missing_build_dependencies', return_value=['libavcodec >= 60.31.102']), \
                patch.object(catalog_store, 'materialize') as fetch:
            with self.assertRaisesRegex(ValueError, 'Missing or too old'):
                self.manager.install_item(self.spec)
            fetch.assert_not_called()

    def test_public_source_closure_has_immutable_pins_and_checksums(self):
        pins = json.loads((SOURCE / 'source-revisions.json').read_text())
        hashes = json.loads((SOURCE / 'source-archives.json').read_text())
        for prefix, pin in pins.items():
            self.assertRegex(pin, r'^[0-9a-f]{40}$')
            key = catalog_sources.repository(prefix) + '/' + pin
            self.assertRegex(hashes[key], r'^[0-9a-f]{64}$')

    def test_dependency_revision_change_requires_update_and_rejects_old_binary(self):
        self.install()
        binary = self.home / 'binary.tar.gz'
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager.export(self.spec, binary)
        with patch.object(catalog_build, 'dependency_pins', return_value={'library': 'b' * 64}):
            self.assertTrue(self.manager.status(self.spec)['update_available'])
            with self.assertRaisesRegex(ValueError, 'source revision'):
                self.manager.install_item(self.spec, binary=binary, sha256=catalog_sources.digest(binary))
        self.assertTrue(self.manager.status(self.spec)['ready'])

    def test_media_dependencies_are_pinned_and_dont_require_system_media_versions(self):
        import catalog_mpv
        spec = catalog_store.find('kilix-mpv')
        self.assertFalse(any('libav' in module or 'libplacebo' in module
                             for module in spec['build_modules']))
        self.assertNotIn('meson', spec['build_commands'])
        for dependency in catalog_mpv.dependencies().values():
            self.assertTrue(dependency['url'].startswith('https://'))
            self.assertRegex(dependency['sha256'], r'^[0-9a-f]{64}$')
        self.assertEqual(catalog_mpv.dependencies()['ffmpeg']['version'], '6.1.4')

    def test_needle_readiness_shim_works_in_packaged_isolated_python(self):
        # Needle imports games from wsl-bin/config with python -I, rather
        # than inheriting the companion process's Python import path.
        package = self.home / 'package'
        (package / 'wsl-bin/config').mkdir(parents=True)
        (package / 'wsl').mkdir()
        for pattern in ('*.py', '*.json'):
            for item in SOURCE.glob(pattern):
                shutil.copy2(item, package / 'wsl' / item.name)
        shutil.copy2(SOURCE / 'games.py', package / 'wsl-bin/config/games.py')
        shutil.copytree(SOURCE.parents[1] / 'config/kilix_sdk', package / 'wsl-bin/config/kilix_sdk',
                        ignore=shutil.ignore_patterns('__pycache__'))
        installed = self.home / '.local/share/kilix-wsl'
        (installed / 'bin').mkdir(parents=True)
        (installed / 'bin/kilix-pong').write_text('#!/bin/sh\nexit 0\n')
        (installed / 'bin/kilix-pong').chmod(0o755)
        code = 'import sys;sys.path.insert(0,sys.argv[1]);import games;print(games.game_enabled("pong") and games.game_ready("pong"))'
        argv = [sys.executable, '-I', '-c', code, str(package / 'wsl-bin/config')]
        env = dict(os.environ, HOME=str(self.home))
        self.assertEqual(subprocess.check_output(argv, env=env, text=True).strip(), 'True')
        catalog_store.Catalog(root=installed).set_enabled(catalog_store.find('pong'), False)
        self.assertEqual(subprocess.check_output(argv, env=env, text=True).strip(), 'False')


if __name__ == '__main__':
    unittest.main()
