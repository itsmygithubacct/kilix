"""Real immutable shared-source staging and isolated import/population closure."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from kilix_sdk import qwen_provider as staging, shared_voice

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT.parent

class SharedVoiceTests(unittest.TestCase):
    def sources(self):
        voice = Path(os.environ.get('KILIX_VOICE_TEST_SOURCE', str(SOURCES/'kilix-voice')))
        lease = Path(os.environ.get('KILIX_DEVICE_LEASE_TEST_SOURCE', str(SOURCES/'kilix-system-monitor')))
        def head(path):
            return subprocess.check_output(['git','-C',str(path),'rev-parse','HEAD'],text=True).strip()
        scratch = tempfile.TemporaryDirectory(prefix='shared-voice-sources-')
        self.addCleanup(scratch.cleanup)
        values = {}
        for name, source in (('voice', voice), ('lease', lease)):
            revision = head(source)
            destination = Path(scratch.name) / name
            # Managed generations require a private chain. A caller's fixture
            # checkout can live beneath a shared research/work directory.
            subprocess.run(['git','clone','--quiet','--shared',str(source),str(destination)],
                           check=True, capture_output=True)
            subprocess.run(['git','-C',str(destination),'checkout','--quiet','--detach',revision],
                           check=True, capture_output=True)
            destination.chmod(0o700)
            values[name + '_source'], values[name + '_ref'] = destination, revision
        return argparse.Namespace(**values)

    def test_partial_closure_refuses_and_default_does_not_invent_a_pin(self):
        self.assertIsNone(shared_voice.selected(argparse.Namespace(),staging))
        with self.assertRaisesRegex(ValueError,'complete explicit'):
            shared_voice.selected(argparse.Namespace(voice_source=Path('/unused')),staging)

    def test_wrong_source_ref_refuses_before_staging(self):
        args = self.sources()
        args.voice_ref = '0'*40
        with self.assertRaises(ValueError):
            shared_voice.selected(args,staging)

    def test_actual_staged_voice_and_canonical_lease_population_and_imports(self):
        args = self.sources()
        selection = shared_voice.selected(args,staging)
        with tempfile.TemporaryDirectory() as scratch:
            root=Path(scratch)
            (root/'lib').mkdir(mode=0o700)
            with patch.object(staging.paths,'kilix_home',return_value=str(ROOT)):
                result=shared_voice.stage(selection,root,staging,staging.clean_environment(),Path('/usr/bin/python3'))
            for source,revision,prefix,target in ((args.voice_source,args.voice_ref,'voicelib',root/'lib/voicelib'),
                (args.lease_source,args.lease_ref,'components/kilix-device-lease/src/kilix_device_lease',root/'shared/kilix_device_lease')):
                names=subprocess.check_output(['git','-C',str(source),'ls-tree','-r','--name-only',revision,prefix],text=True).splitlines()
                self.assertEqual(sorted(str(p.relative_to(target)) for p in target.rglob('*') if p.is_file()),
                                 sorted(str(Path(name).relative_to(prefix)) for name in names))
                for name in names:
                    self.assertEqual((target/Path(name).relative_to(prefix)).read_bytes(),
                        subprocess.check_output(['git','-C',str(source),'show',revision+':'+name]))
            self.assertEqual(result['sources'],{'voice':args.voice_ref,'lease':args.lease_ref})
            self.assertEqual(result['files'],staging.package_files(root/'shared'))

    def test_bound_transcribe_commands_select_existing_lease_policy_only_for_new_closure(self):
        spec=importlib.util.spec_from_file_location('_transcribe_closure',ROOT/'scripts/kilix-transcribe-launch.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        value={'model':'fixture','content_root':'/fixture','budget':100}
        self.assertNotIn('--lease-device',module.bound_command(value,Path('/owned'),['serve']))
        value['shared_voice']={'sources':{}}
        self.assertIn('--lease-device',module.bound_command(value,Path('/owned'),['serve']))
        self.assertEqual(module.bound_command(value,Path('/owned'),['record','--seconds','1']),['record','--seconds','1'])

    def test_actual_bound_launchers_refuse_changed_shared_populations(self):
        from tests.test_transcribe_provider_route import TranscribeRouteTests
        from tests.test_qwen_provider_route import QwenProviderRouteTests
        for provider in ('transcribe','qwen'):
            for damage in ('none','bytes','extra','link'):
                with self.subTest(provider=provider,damage=damage), tempfile.TemporaryDirectory() as scratch:
                    if provider=='transcribe':
                        generation,_,executable=TranscribeRouteTests().fixture(Path(scratch))
                        template=ROOT/'scripts/kilix-transcribe-launch.py'
                    else:
                        executable,*_=QwenProviderRouteTests().launcher_fixture(Path(scratch))
                        generation=executable.parent.parent
                        template=ROOT/'scripts/kilix-qwen-provider-launch.py'
                    shared=generation/'shared'
                    shared.mkdir(mode=0o700)
                    target=shared/'canonical.py'
                    target.write_bytes(b'# synthetic population binding\n')
                    value=json.loads((generation/'binding.json').read_bytes())
                    value['shared_voice']={'sources':{'fixture':'inert'},'files':staging.package_files(shared)}
                    payload=json.dumps(value).encode()
                    (generation/'binding.json').write_bytes(payload)
                    executable.write_text(template.read_text().replace('@BINDING_SHA256@',hashlib.sha256(payload).hexdigest()))
                    if damage=='bytes':target.write_bytes(b'X'*target.stat().st_size)
                    elif damage=='extra':(shared/'undeclared.py').write_text('# altered\n')
                    elif damage=='link':target.unlink();target.symlink_to('/etc/hostname')
                    result=subprocess.run(['/usr/bin/python3','-I','-B',str(executable),'status'],capture_output=True,text=True)
                    if damage=='none':self.assertEqual(result.returncode,0,result.stderr)
                    else:self.assertNotEqual(result.returncode,0);self.assertEqual(result.stdout,'')
