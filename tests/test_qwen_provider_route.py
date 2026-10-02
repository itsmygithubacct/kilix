"""Host preparation preserves consent, previous selection and private boundaries."""
import argparse
import json
import hashlib
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'config'))
from kilix_sdk import qwen_provider as provider


class QwenProviderRouteTests(unittest.TestCase):
    def previous(self, root):
        managed = root/'data/voice/qwen-provider'
        generation = managed/'generations'/('a'*40+'-'+'b'*32)
        generation.mkdir(mode=0o700,parents=True)
        managed.chmod(0o700)
        (generation/'sentinel').write_text('previous selection')
        (managed/'current').symlink_to(generation)
        return managed,generation

    def test_current_outside_managed_generations_is_refused(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            managed,_generation = self.previous(root)
            (managed/'current').unlink()
            (managed/'current').symlink_to(root)
            with self.assertRaises(ValueError):
                provider.current_generation(managed)

    def test_symlink_ancestor_is_refused(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root/'outside').mkdir(mode=0o700)
            (root/'link').symlink_to(root/'outside')
            with self.assertRaises(ValueError):
                provider.private_chain(root/'link/new',create=True)
            self.assertFalse((root/'outside/new').exists())

    def test_offline_missing_source_does_not_acquire(self):
        with tempfile.TemporaryDirectory() as scratch, patch.object(provider.subprocess,'run') as run:
            with self.assertRaisesRegex(ValueError,'offline source is missing'):
                provider.acquire_source(Path(scratch),'missing','unused','a'*40,True)
            run.assert_not_called()

    def preparation_failure(self, failed_step):
        selected = os.environ.get('KILIX_QWEN_PROVIDER_TEST_SOURCE')
        if not selected:
            self.skipTest('set KILIX_QWEN_PROVIDER_TEST_SOURCE to the exact provider checkout')
        builder = Path(selected)/'tools'
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            managed,previous = self.previous(root)
            calls = []
            def run(_provider,command,_environment,**kwargs):
                words = list(map(str,command))
                calls.append(words)
                if '--version' in words:
                    return b'uv 0.12.5\n'
                if 'find' in words:
                    return b'/private/python3.12\n'
                if 'rev-parse' in words:
                    return (provider.CONTENT_REF+'\n').encode()
                if 'status' in words:
                    return b''
                if 'InstalledModel' in ' '.join(words):
                    compile(words[words.index('-c')+1],'<receipt-preflight>','exec')
                    if failed_step == 'receipt':
                        raise subprocess.CalledProcessError(69,words)
                    return json.dumps({'receipt_root':str(root/'receipts'),
                                       'runtime_root':str(root/'xdg')}).encode()
                if any(word.endswith('build_environment.py') for word in words):
                    raise subprocess.CalledProcessError(1,words)
            def source(_parent,name,*_args):
                if name.startswith('.kilix-qwen-tts-'):
                    return Path(selected)
                return root/'engine'
            with patch.object(provider.paths,'data_dir',return_value=str(root/'data')), \
                 patch.object(provider.paths,'source_home',return_value=str(root/'sources')), \
                 patch.object(provider.paths,'kilix_home',return_value=str(ROOT)), \
                 patch.object(provider,'acquire_source',side_effect=source), \
                 patch.object(provider,'owned_run',side_effect=run):
                # Use the selected builder's real guarded-directory transaction.
                sys.path.insert(0,str(builder))
                try:
                    with self.assertRaises(subprocess.CalledProcessError):
                        provider.prepare(argparse.Namespace(uv=Path('/uv'),offline=True))
                finally:
                    sys.path.remove(str(builder))
            self.assertEqual((managed/'current').resolve(),previous)
            self.assertEqual((previous/'sentinel').read_text(),'previous selection')
            self.assertEqual(list((managed/'generations').iterdir()),[previous])
            build_calls = [c for c in calls if any(w.endswith('build_environment.py') for w in c)]
            self.assertEqual(len(build_calls),0 if failed_step == 'receipt' else 1)

    def test_missing_receipt_refuses_before_build_and_preserves_selection(self):
        self.preparation_failure('receipt')

    def test_offline_build_failure_rolls_back_and_preserves_selection(self):
        self.preparation_failure('build')

    def test_provider_help_does_not_install_voice(self):
        completed = subprocess.run([str(ROOT/'kilix'),'tts','provider','--help'],
                                   capture_output=True,text=True)
        self.assertEqual(completed.returncode,0,completed.stderr)
        self.assertIn('prepare',completed.stdout)
        self.assertIn('serve',completed.stdout)

    def launcher_fixture(self, root):
        selected = os.environ.get('KILIX_QWEN_PROVIDER_TEST_SOURCE')
        if not selected:
            self.skipTest('set KILIX_QWEN_PROVIDER_TEST_SOURCE to the exact provider checkout')
        generation = root/'generation'
        (generation/'bin').mkdir(mode=0o700,parents=True)
        lib = generation/'lib'
        shutil.copytree(Path(selected)/'src/kilix_qwen_tts',lib/'kilix_qwen_tts',
                        ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        sys.path.insert(0,str(lib))
        try:
            from kilix_qwen_tts.runtime import digest_file,tree_digest
        finally:
            sys.path.remove(str(lib))
        base = generation/'python-root'
        (base/'bin').mkdir(mode=0o700,parents=True)
        original = base/'bin/python'
        original.write_text('#!/bin/sh\nprintf "verified-marker\\n"\n')
        original.chmod(0o700)
        python = generation/'environment/bin/python'
        python.parent.mkdir(mode=0o700,parents=True)
        python.symlink_to(original)
        config = generation/'environment/pyvenv.cfg'
        config.write_text('include-system-site-packages = false\n')
        site = generation/'environment/site-packages'
        site.mkdir(mode=0o700)
        (site/'dependency.py').write_text('value = 1\n')
        manifest = {'environment':{'python':str(python),'python_sha256':digest_file(python,follow=True),
            'site_packages':str(site),'site_packages_sha256':tree_digest(site),
            'python_root':str(base),'python_root_sha256':tree_digest(base,allow_file_links=True)}}
        (generation/'runtime').mkdir(mode=0o700)
        runtime = generation/'runtime/runtime.json'
        runtime.write_text(json.dumps(manifest))
        binding = {'files':provider.package_files(lib),'content_root':str(root/'models'),
            'environment':{'KILIX_LICENSE_RECEIPTS':str(root/'receipts'),'XDG_RUNTIME_DIR':str(root/'xdg')},
            'runtime_sha256':hashlib.sha256(runtime.read_bytes()).hexdigest()}
        binding['venv_config_sha256'] = digest_file(config)
        payload = json.dumps(binding).encode()
        (generation/'binding.json').write_bytes(payload)
        (generation/'binding.json').chmod(0o600)
        launcher = generation/'bin/launch'
        launcher.write_text((ROOT/'scripts/kilix-qwen-provider-launch.py').read_text().replace(
            '@BINDING_SHA256@',hashlib.sha256(payload).hexdigest()))
        return launcher,original,site,runtime

    def test_verified_generation_launcher_and_tamper_refusals(self):
        for kind in ('unchanged','interpreter','dependency','manifest','library','configuration','bytecode'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as scratch:
                launcher,python,site,runtime = self.launcher_fixture(Path(scratch))
                if kind == 'interpreter':
                    python.write_text('#!/bin/sh\nprintf "changed-marker\\n"\n')
                elif kind == 'dependency':
                    (site/'dependency.py').write_text('value = 2\n')
                elif kind == 'manifest':
                    runtime.write_text('{}')
                elif kind == 'library':
                    (launcher.parent.parent/'lib/kilix_qwen_tts/extra.py').write_text('value = 1')
                elif kind == 'configuration':
                    (launcher.parent.parent/'environment/pyvenv.cfg').write_text('home = /changed\n')
                elif kind == 'bytecode':
                    (launcher.parent.parent/'lib/kilix_qwen_tts/extra.pyc').write_bytes(b'changed bytecode')
                result = subprocess.run(['/usr/bin/python3','-I','-B',str(launcher),'status'],
                                        capture_output=True,text=True)
                if kind == 'unchanged':
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertEqual(result.stdout,'verified-marker\n')
                else:
                    self.assertNotEqual(result.returncode,0)
                    self.assertEqual(result.stdout,'')

    def test_pinned_archive_excludes_checkout_changes_and_ignored_extras(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            source = root/'source'
            source.mkdir(mode=0o700)
            environment = provider.clean_environment()
            subprocess.run(['git','init','-q',str(source)],check=True,env=environment)
            package = source/'src/package'
            package.mkdir(parents=True)
            (package/'tracked.py').write_bytes(b'pinned bytes')
            subprocess.run(['git','-C',str(source),'add','src'],check=True,env=environment)
            subprocess.run(['git','-C',str(source),'-c','user.name=fixture',
                '-c','user.email=fixture@example.invalid','commit','-qm','fixture'],check=True,env=environment)
            revision = subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],
                                               text=True,env=environment).strip()
            (package/'tracked.py').write_bytes(b'changed checkout')
            (package/'extra.py').write_bytes(b'untracked extra')
            with patch.object(provider.paths,'kilix_home',return_value=str(ROOT)):
                provider.stage_package(source,revision,'src/package',root/'staged',environment)
            self.assertEqual(provider.package_files(root/'staged'),
                             {'tracked.py':hashlib.sha256(b'pinned bytes').hexdigest()})


if __name__ == '__main__':
    unittest.main()
