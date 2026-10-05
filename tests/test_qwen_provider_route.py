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
    def test_selected_host_content_passes_provider_authority_check(self):
        # Exercise the real checkout guard: a mocked ref derived from the
        # provider constant cannot detect drift from the host's gitlink.
        with patch.object(provider.paths, 'kilix_home', return_value=str(ROOT)):
            provider.verify_content(ROOT/'third_party/kilix-content',
                                    provider.clean_environment())

    def previous(self, root):
        managed = root/'data/voice/qwen-provider'
        generation = managed/'generations'/('a'*40+'-'+'b'*32)
        generation.mkdir(mode=0o700,parents=True)
        managed.chmod(0o700)
        (managed/'generations').chmod(0o700)
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
                                       'runtime_root':str(root/'xdg'),
                                       'ready':['qwen3-tts-0.6b-customvoice']}).encode()
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

    def launcher_fixture(self, root, script='#!/bin/sh\nprintf "verified-marker\\n"\n'):
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
        original.write_text(script)
        original.chmod(0o700)
        python = generation/'environment/bin/python'
        python.parent.mkdir(mode=0o700,parents=True)
        python.symlink_to(original)
        config = generation/'environment/pyvenv.cfg'
        config.write_text('include-system-site-packages = false\n')
        site = generation/'environment/site-packages'
        site.mkdir(mode=0o700)
        (site/'dependency.py').write_text('value = 1\n')
        environment = {'python':str(python),'python_sha256':digest_file(python,follow=True),
            'site_packages':str(site),'site_packages_sha256':tree_digest(site),
            'python_root':str(base),'python_root_sha256':tree_digest(base,allow_file_links=True)}
        models = {'qwen3-tts-0.6b-customvoice':3*1024**3,'qwen3-tts-1.7b-voicedesign':5*1024**3}
        digests = {}
        for name in models:
            (generation/'runtimes'/name).mkdir(mode=0o700,parents=True)
            manifest = generation/'runtimes'/name/'runtime.json'
            manifest.write_text(json.dumps({'device':'cuda','model':{'id':name},'environment':environment}))
            digests[name] = hashlib.sha256(manifest.read_bytes()).hexdigest()
        runtime = generation/'runtimes/qwen3-tts-1.7b-voicedesign/runtime.json'
        index = generation/'runtime-index.json'
        index.write_text(json.dumps({'schema':'kilix.qwen-tts.runtime-set/v1','runtimes':[
            {'root':str(generation/'runtimes'/name),'asset_id':name,'snapshot_bytes':budget}
            for name,budget in models.items()]}))
        binding = {'files':provider.package_files(lib),'content_root':str(root/'models'),
            'environment':{'KILIX_LICENSE_RECEIPTS':str(root/'receipts'),'XDG_RUNTIME_DIR':str(root/'xdg')},
            'models':models,'device':'cuda','runtimes':digests,
            'index_sha256':hashlib.sha256(index.read_bytes()).hexdigest()}
        binding['venv_config_sha256'] = digest_file(config)
        payload = json.dumps(binding).encode()
        (generation/'binding.json').write_bytes(payload)
        (generation/'binding.json').chmod(0o600)
        launcher = generation/'bin/launch'
        launcher.write_text((ROOT/'scripts/kilix-qwen-provider-launch.py').read_text().replace(
            '@BINDING_SHA256@',hashlib.sha256(payload).hexdigest()))
        return launcher,original,site,runtime

    def test_verified_generation_launcher_and_tamper_refusals(self):
        for kind in ('unchanged','interpreter','dependency','manifest','index','library','configuration','bytecode'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as scratch:
                launcher,python,site,runtime = self.launcher_fixture(Path(scratch))
                if kind == 'interpreter':
                    python.write_text('#!/bin/sh\nprintf "changed-marker\\n"\n')
                elif kind == 'dependency':
                    (site/'dependency.py').write_text('value = 2\n')
                elif kind == 'manifest':
                    runtime.write_text('{}')
                elif kind == 'index':
                    (launcher.parent.parent/'runtime-index.json').write_text('{"runtimes":[]}')
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

    def test_serve_selects_the_bound_runtime_index_and_every_model(self):
        with tempfile.TemporaryDirectory() as scratch:
            launcher,_python,_site,_runtime = self.launcher_fixture(
                Path(scratch),'#!/bin/sh\nprintf "%s\\n" "$@"\n')
            result = subprocess.run(['/usr/bin/python3','-I','-B',str(launcher),'serve'],
                                    capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            words = result.stdout.splitlines()
            generation = launcher.parent.parent
            self.assertEqual(words[-4:],['--runtime-index',str(generation/'runtime-index.json'),
                                         '--content-root',str(Path(scratch)/'models')])
            consent = json.loads(words[words.index(str(generation/'lib'))+1])
            self.assertEqual(list(consent['models']),
                             ['qwen3-tts-0.6b-customvoice','qwen3-tts-1.7b-voicedesign'])
            self.assertIn("for name,budget in value['models'].items():",result.stdout)

    def test_runtime_index_or_manifest_disagreeing_with_binding_refuses(self):
        for kind in ('order','device','environment','model'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as scratch:
                launcher,_python,_site,_runtime = self.launcher_fixture(Path(scratch))
                generation = launcher.parent.parent
                binding = json.loads((generation/'binding.json').read_text())
                if kind == 'order':
                    index = generation/'runtime-index.json'
                    value = json.loads(index.read_text())
                    value['runtimes'].reverse()
                    index.write_text(json.dumps(value))
                    binding['index_sha256'] = hashlib.sha256(index.read_bytes()).hexdigest()
                else:
                    name = 'qwen3-tts-1.7b-voicedesign'
                    manifest = generation/'runtimes'/name/'runtime.json'
                    value = json.loads(manifest.read_text())
                    if kind == 'device':
                        value['device'] = 'cpu'
                    elif kind == 'environment':
                        value['environment']['site_packages'] = '/elsewhere'
                    else:
                        value['model']['id'] = 'qwen3-tts-0.6b-base'
                    manifest.write_text(json.dumps(value))
                    binding['runtimes'][name] = hashlib.sha256(manifest.read_bytes()).hexdigest()
                payload = json.dumps(binding).encode()
                (generation/'binding.json').write_bytes(payload)
                launcher.write_text((ROOT/'scripts/kilix-qwen-provider-launch.py').read_text().replace(
                    '@BINDING_SHA256@',hashlib.sha256(payload).hexdigest()))
                result = subprocess.run(['/usr/bin/python3','-I','-B',str(launcher),'status'],
                                        capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(result.stdout,'')

    def test_auto_device_requires_every_node_and_capability(self):
        self.assertEqual(provider.selected_device('cpu'),'cpu')
        self.assertEqual(provider.selected_device('cuda'),'cuda')
        with self.assertRaises(ValueError):
            provider.selected_device('rocm')
        with patch.object(provider,'cuda_capable',return_value=True):
            self.assertEqual(provider.selected_device('auto'),'cuda')
        with patch.object(provider,'cuda_capable',return_value=False):
            self.assertEqual(provider.selected_device('auto'),'cpu')
        def smi(capability):
            return subprocess.CompletedProcess([],0,stdout=(capability+'\n').encode(),stderr=b'')
        nodes = ('/dev/null','/dev/zero','/dev/full')
        with patch.object(provider,'GPU_NODES',nodes), \
             patch.object(provider.shutil,'which',return_value='/usr/bin/nvidia-smi'):
            for capability,expected in (('8.6',True),('7.0',True),('6.1',False),('',False)):
                with self.subTest(capability=capability), \
                     patch.object(provider.subprocess,'run',return_value=smi(capability)):
                    self.assertEqual(provider.cuda_capable(),expected)
            with patch.object(provider.subprocess,'run',side_effect=OSError('no driver')):
                self.assertFalse(provider.cuda_capable())
        with tempfile.TemporaryDirectory() as scratch, \
             patch.object(provider,'GPU_NODES',('/dev/null','/dev/zero',str(Path(scratch)/'nvidia0'))), \
             patch.object(provider.subprocess,'run',side_effect=AssertionError('probed without nodes')):
            self.assertFalse(provider.cuda_capable())

    def staged_preparation(self, ready, requested_device, capable=False):
        selected = os.environ.get('KILIX_QWEN_PROVIDER_TEST_SOURCE')
        if not selected:
            self.skipTest('set KILIX_QWEN_PROVIDER_TEST_SOURCE to the exact provider checkout')
        builder = Path(selected)/'tools'
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        managed,previous = self.previous(root)
        calls = []
        def run(source,command,_environment,**kwargs):
            words = list(map(str,command))
            calls.append(words)
            if '--version' in words:
                return b'uv 0.12.5\n'
            if 'find' in words:
                return b'/private/python3.12\n'
            if 'rev-parse' in words:
                return ((provider.CONTENT_REF if 'kilix-content' in words[2] else provider.PROVIDER_REF)
                        +'\n').encode()
            if 'status' in words:
                return b''
            if 'InstalledModel' in ' '.join(words):
                compile(words[words.index('-c')+1],'<receipt-preflight>','exec')
                return json.dumps({'receipt_root':str(root/'receipts') if ready else None,
                                   'runtime_root':str(root/'xdg'),'ready':ready}).encode()
            if any(word.endswith('build_environment.py') for word in words):
                destination = Path(words[words.index('--destination')+1])
                destination.mkdir(mode=0o700)
                (destination/'pyvenv.cfg').write_text('home = /private\n')
                return None
            if 'archive' in words:
                import io,tarfile
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream,mode='w') as archive:
                    item = tarfile.TarInfo(words[-1]+'/module.py')
                    item.size = 1
                    archive.addfile(item,io.BytesIO(b'x'))
                return stream.getvalue()
            if '--installed-asset' in words:
                destination = Path(words[words.index('--destination')+1])
                destination.mkdir(mode=0o700)
                (destination/'runtime.json').write_text(json.dumps({
                    'device':words[words.index('--device')+1],
                    'model':{'id':words[words.index('--installed-asset')+1]}}))
                return None
            return None
        def source(_parent,name,*_args):
            return Path(selected) if name.startswith('.kilix-qwen-tts-') else root/'engine'
        with patch.object(provider.paths,'data_dir',return_value=str(root/'data')), \
             patch.object(provider.paths,'source_home',return_value=str(root/'sources')), \
             patch.object(provider.paths,'kilix_home',return_value=str(ROOT)), \
             patch.object(provider,'acquire_source',side_effect=source), \
             patch.object(provider,'cuda_capable',return_value=capable), \
             patch.object(provider,'owned_run',side_effect=run):
            sys.path.insert(0,str(builder))
            try:
                provider.prepare(argparse.Namespace(uv=Path('/uv'),offline=True,device=requested_device))
            finally:
                sys.path.remove(str(builder))
        return managed,previous,calls

    def test_preparation_stages_every_licensed_model_on_the_selected_device(self):
        ready = ['qwen3-tts-1.7b-voicedesign','qwen3-tts-0.6b-customvoice']
        for requested,capable,device in (('cuda',False,'cuda'),('auto',True,'cuda'),
                                         ('auto',False,'cpu'),('cpu',True,'cpu')):
            with self.subTest(requested=requested,capable=capable):
                managed,previous,calls = self.staged_preparation(ready,requested,capable)
                generation = (managed/'current').resolve()
                self.assertNotEqual(generation,previous)
                builds = [c for c in calls if any(w.endswith('build_environment.py') for w in c)]
                self.assertEqual(len(builds),1)
                self.assertEqual(builds[0][builds[0].index('--device')+1],device)
                stages = [(c[c.index('--installed-asset')+1],c[c.index('--device')+1],
                           c[c.index('--model-snapshot-bytes')+1]) for c in calls if '--installed-asset' in c]
                expected = ['qwen3-tts-0.6b-customvoice','qwen3-tts-1.7b-voicedesign']
                self.assertEqual(stages,[(name,device,str(provider.MODELS[name])) for name in expected])
                index = json.loads((generation/'runtime-index.json').read_text())
                self.assertEqual(index,{'schema':'kilix.qwen-tts.runtime-set/v1','runtimes':[
                    {'root':str(generation/'runtimes'/name),'asset_id':name,
                     'snapshot_bytes':provider.MODELS[name]} for name in expected]})
                self.assertEqual(oct((generation/'runtime-index.json').stat().st_mode & 0o777),'0o600')
                binding = json.loads((generation/'binding.json').read_text())
                self.assertEqual(binding['models'],{name:provider.MODELS[name] for name in expected})
                self.assertEqual(list(binding['models']),expected)
                self.assertEqual(binding['device'],device)
                self.assertEqual(binding['index_sha256'],hashlib.sha256(
                    (generation/'runtime-index.json').read_bytes()).hexdigest())
                self.assertEqual(set(binding['runtimes']),set(expected))
                self.assertNotIn('qwen3-tts-0.6b-base',binding['models'])

    def test_no_licensed_model_refuses_before_build_and_keeps_selection(self):
        with self.assertRaisesRegex(ValueError,'licence receipt'):
            self.staged_preparation([], 'cpu')

    def test_model_budgets_cover_the_catalogued_installations(self):
        sys.path.insert(0,str(ROOT/'third_party/kilix-content/src'))
        sys.path.insert(0,str(ROOT/'third_party/kilix-content/third_party/kilix-license/src'))
        try:
            import kilix_content
            catalog = kilix_content.verified_packaged_catalog()
        finally:
            sys.path.remove(str(ROOT/'third_party/kilix-content/src'))
            sys.path.remove(str(ROOT/'third_party/kilix-content/third_party/kilix-license/src'))
        self.assertEqual(next(iter(provider.MODELS)),provider.MODEL)
        for name,budget in provider.MODELS.items():
            spec = catalog.require_asset(name)
            self.assertEqual((spec.provider,spec.stream,spec.consumer_schema),
                             ('kilix-qwen-tts','F104','kilix.qwen-tts.runtime'))
            self.assertLessEqual(spec.installed_bytes,budget)
            self.assertLessEqual(budget,8*1024**3)

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
