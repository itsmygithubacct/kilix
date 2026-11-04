"""Public installer rollback controls with fake tools and model bytes."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tests'))
from _env_support import sandbox_env


class GenerationRollback(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='yolox-generation-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.base = self.root/'runtime'
        self.source = self.root/'source with space'
        self.host = self.root/'host'
        self.assets = self.root/'apps'
        self.config = self.root/'config'
        (self.host/'scripts').mkdir(parents=True)
        (self.host/'config').mkdir()
        for name in ('yolox-generation.py','detector-carrier-supervisor.py'):
            shutil.copyfile(ROOT/'scripts'/name,self.host/'scripts'/name)
        shutil.copyfile(ROOT/'config/yolox_runtime.py',self.host/'config/yolox_runtime.py')
        self.executable(self.host/'kilix', '''import json,os,pathlib,sys
root=pathlib.Path(os.environ['PRIVATE_ASSETS'])
command,model=sys.argv[2:4]
if command=='show': print(json.dumps({'root':str(root)}))
elif command=='verify': sys.exit(0 if (root/'verified').exists() else 1)
else: raise SystemExit('fixture must never install content')
''')
        (self.source/'tools').mkdir(parents=True)
        (self.source/'models').mkdir()
        self.executable(self.source/'tools/kilix-yolox-detect', '''import json,os,sys
print(json.dumps({'argv':sys.argv,'runtime':os.environ.get('KILIX_YOLOX_DIR')}))
''')
        self.executable(self.source/'tools/kilix-yolox-cut', '''import os,pathlib,sys
p=pathlib.Path(sys.argv[1]); out=p.with_name(p.stem+'_'+sys.argv[3]+'.onnx')
if os.environ.get('PRIVATE_FAIL_STAGE')=='cut':
 out.write_bytes(b'partial');raise SystemExit(1)
if os.environ.get('PRIVATE_FAIL_STAGE')=='concurrent':
 pathlib.Path(os.environ['PRIVATE_CONFIG_FILE']).write_text('KILIX_OBJECT_DETECTOR=concurrent-custom\\n')
out.write_bytes(b'cut:'+p.read_bytes())
''')
        (self.source/'models/SHA256SUMS').write_text(''.join(
            hashlib.sha256(name.encode()).hexdigest()+'  '+name+'.onnx\n'
            for name in ('yolox_s','yolox_nano','yolox_tiny')))
        for model in ('yolox_s','yolox_nano','yolox_tiny'):
            directory=self.assets/'assets'/model
            (directory/'notices').mkdir(parents=True)
            (directory/(model+'.onnx')).write_bytes(model.encode())
            (directory/'notices/LICENSE-apache-2.0.txt').write_bytes(b'fixture notice')
        (self.assets/'verified').touch()
        self.template=self.root/'fake-python'
        self.executable(self.template, '''import json,os,pathlib,sys
args=sys.argv[1:]
while args and args[0] in ('-I','-B'):args=args[1:]
if args[0]=='-c':raise SystemExit(0)
if args[0]=='-m':
 pathlib.Path(__file__).parent.parent.joinpath('pip.log').write_text(json.dumps({'args':args,'find_links':os.environ.get('PIP_FIND_LINKS'),'config':os.environ.get('PIP_CONFIG_FILE')}))
 raise SystemExit(0)
os.execv(sys.executable,[sys.executable,*sys.argv[1:]])
''')
        self.python_command=self.root/'fake-python-command'
        self.executable(self.python_command, '''import os,pathlib,shutil,sys
if sys.argv[1:3]==['-m','venv']:
 p=pathlib.Path(sys.argv[3])/'bin/python';p.parent.mkdir(parents=True)
 shutil.copyfile(os.environ['PRIVATE_PYTHON_TEMPLATE'],p);p.chmod(0o700);raise SystemExit(0)
os.execv(sys.executable,[sys.executable,*sys.argv[1:]])
''')
        self.uv=self.root/'fake-uv'
        self.executable(self.uv, '''import os,pathlib,shutil,sys,time
if sys.argv[1]=='venv':
 dest=pathlib.Path(sys.argv[-1])/'bin/python';dest.parent.mkdir(parents=True)
 shutil.copyfile(os.environ['PRIVATE_PYTHON_TEMPLATE'],dest);dest.chmod(0o700)
elif sys.argv[1]=='pip':
 dest=pathlib.Path(sys.argv[sys.argv.index('--python')+1]).parent.parent/'partial-package'
 dest.write_text('changed before package failure')
 if os.environ.get('PRIVATE_FAIL_STAGE')=='block':
  pathlib.Path(os.environ['PRIVATE_READY']).write_text('ready');time.sleep(30)
 if os.environ.get('PRIVATE_FAIL_STAGE')=='package':raise SystemExit(1)
elif sys.argv[1]=='--version':print('fixture uv')
else:raise SystemExit('unexpected fake uv command')
''')
        self.env=sandbox_env(HOME=str(self.root/'home'),GPU_TERMINAL_DATA_HOME=str(self.root/'data'),
            GPU_TERMINAL_SOURCE_HOME=str(self.root/'sources'),KILIX_HOME=str(self.host),
            KILIX_YOLOX_DIR=str(self.base),KILIX_YOLOX_SRC=str(self.source),
            KILIX_USER_CONFIG_DIRECTORY=str(self.config),KILIX_YOLOX_TRUST_EXISTING_CHECKOUT='1',
            KILIX_PYTHON=sys.executable,KILIX_UV=str(self.uv),PRIVATE_ASSETS=str(self.assets),
            PRIVATE_PYTHON_TEMPLATE=str(self.template),PRIVATE_CONFIG_FILE=str(self.config/'kilix.env'),
            PRIVATE_READY=str(self.root/'ready'),PRIVATE_PIP_LOG=str(self.root/'pip.log'))
        self.legacy()

    def executable(self,path,body):
        path.write_text('#!'+sys.executable+'\n'+body)
        path.chmod(0o700)

    def legacy(self):
        (self.base/'venv/bin').mkdir(parents=True)
        shutil.copyfile(self.template,self.base/'venv/bin/python')
        (self.base/'venv/bin/python').chmod(0o700)
        (self.base/'models').mkdir()
        (self.base/'models/yolox_nano.onnx').write_bytes(b'yolox_nano')
        (self.base/'models/yolox_nano_320.onnx').write_bytes(b'cut:yolox_nano')
        (self.base/'bin').mkdir()
        wrapper=self.base/'bin/kilix-yolox-detect'
        self.executable(wrapper, """import os,subprocess,sys
cmd=[sys.executable,os.environ['KILIX_HOME']+'/config/yolox_runtime.py','check',os.environ['KILIX_YOLOX_DIR'],os.environ['KILIX_YOLOX_SRC'],'yolox_nano','320']
raise SystemExit(subprocess.run(cmd).returncode)
""")
        subprocess.run([sys.executable,str(self.host/'config/yolox_runtime.py'),'record',
                        str(self.base),str(self.source),'yolox_nano','320'],check=True)
        self.config.mkdir()
        (self.config/'kilix.env').write_text('KILIX_OBJECT_DETECTOR='+str(wrapper)+'\nKILIX_NVR_DETECT=keep\nOTHER=unchanged\n')
        (self.root/'receipt').write_text('unchanged fixture receipt')

    def run_installer(self,action='--upgrade',**extra):
        return subprocess.run([str(ROOT/'scripts/install-yolox.sh'),action,'--yes'],
            env=dict(self.env,**extra),stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=30)

    def legacy_state(self):
        paths=[self.base/'runtime.json',self.base/'bin/kilix-yolox-detect',
               self.base/'venv/bin/python',self.base/'models/yolox_nano.onnx',
               self.base/'models/yolox_nano_320.onnx',self.source/'tools/kilix-yolox-cut',self.root/'receipt']
        return {str(p):p.read_bytes() for p in paths}

    def test_package_and_cut_failure_preserve_selected_runtime(self):
        for stage in ('package','cut'):
            with self.subTest(stage=stage):
                before=self.legacy_state();config=(self.config/'kilix.env').read_bytes()
                result=self.run_installer(PRIVATE_FAIL_STAGE=stage)
                self.assertNotEqual(result.returncode,0,result.stderr)
                self.assertEqual(self.legacy_state(),before)
                self.assertEqual((self.config/'kilix.env').read_bytes(),config)
                self.assertFalse((self.base/'.current').exists())
                self.assertEqual(list((self.base/'.generations').glob('gen-*')),[])
                self.assertEqual(subprocess.run([str(self.base/'bin/kilix-yolox-detect')],env=self.env).returncode,0)

    def test_success_keeps_legacy_generation_and_selects_same_model(self):
        before=self.legacy_state()
        result=self.run_installer()
        self.assertEqual(result.returncode,0,result.stderr)
        generation=(self.base/'.current').resolve(strict=True)
        self.assertEqual(self.legacy_state(),before)
        binding=json.loads((generation/'runtime.json').read_text())
        self.assertEqual(binding['model'],'yolox_nano')
        self.assertEqual(binding['source'],str(generation/'source'))
        self.assertIn('KILIX_OBJECT_DETECTOR='+str(generation/'bin/kilix-yolox-detect'),(self.config/'kilix.env').read_text())
        self.assertIn('KILIX_NVR_DETECT=keep',(self.config/'kilix.env').read_text())
        self.assertEqual(self.run_installer('--check').returncode,0)
        run=subprocess.run([str(ROOT/'scripts/install-yolox.sh'),'--run','--geometry','320x320'],
                           env=self.env,capture_output=True,text=True,timeout=20)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertEqual(json.loads(run.stdout)['runtime'],str(generation))

    def test_fresh_install_selects_new_generation(self):
        shutil.rmtree(self.base)
        shutil.rmtree(self.config)
        result=self.run_installer('--install')
        self.assertEqual(result.returncode,0,result.stderr)
        generation=(self.base/'.current').resolve(strict=True)
        self.assertEqual(json.loads((generation/'runtime.json').read_text())['model'],'yolox_s')
        self.assertIn('KILIX_OBJECT_DETECTOR='+str(generation/'bin/kilix-yolox-detect'),
                      (self.config/'kilix.env').read_text())
        self.assertEqual(self.run_installer('--check').returncode,0)

    def test_custom_detector_is_not_replaced(self):
        conf=self.config/'kilix.env';conf.write_text('KILIX_OBJECT_DETECTOR=/custom/detector\nKILIX_NVR_DETECT=keep\n')
        before=conf.read_bytes();result=self.run_installer()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(conf.read_bytes(),before)
        self.assertTrue((self.base/'.current').is_symlink())

    def test_custom_detector_environment_is_not_replaced(self):
        conf=self.config/'kilix.env';before=conf.read_bytes()
        result=self.run_installer(KILIX_OBJECT_DETECTOR='/custom/explicit-detector')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(conf.read_bytes(),before)
        self.assertTrue((self.base/'.current').is_symlink())

    def test_failed_later_upgrade_preserves_selected_generation(self):
        first=self.run_installer()
        self.assertEqual(first.returncode,0,first.stderr)
        selected=(self.base/'.current').resolve(strict=True)
        previous_config=(self.config/'kilix.env').read_bytes()
        previous_binding=(selected/'runtime.json').read_bytes()
        previous_wrapper=(selected/'bin/kilix-yolox-detect').read_bytes()
        previous_source=(selected/'source/tools/kilix-yolox-cut').read_bytes()
        for stage in ('package','cut'):
            with self.subTest(stage=stage):
                result=self.run_installer(PRIVATE_FAIL_STAGE=stage)
                self.assertNotEqual(result.returncode,0,result.stderr)
                self.assertEqual((self.base/'.current').resolve(strict=True),selected)
                self.assertEqual((self.config/'kilix.env').read_bytes(),previous_config)
                self.assertEqual((selected/'runtime.json').read_bytes(),previous_binding)
                self.assertEqual((selected/'bin/kilix-yolox-detect').read_bytes(),previous_wrapper)
                self.assertEqual((selected/'source/tools/kilix-yolox-cut').read_bytes(),previous_source)
                self.assertEqual(list((self.base/'.generations').glob('gen-*')),[selected])
                self.assertEqual(self.run_installer('--check').returncode,0)

    def test_requested_model_and_size_persist_across_later_upgrade(self):
        first=self.run_installer('--install',KILIX_YOLOX_MODEL='yolox_s',KILIX_YOLOX_SIZE='640')
        self.assertEqual(first.returncode,0,first.stderr)
        first_generation=(self.base/'.current').resolve(strict=True)
        self.assertEqual(json.loads((first_generation/'runtime.json').read_text())['size'],640)
        second=self.run_installer()
        self.assertEqual(second.returncode,0,second.stderr)
        selected=(self.base/'.current').resolve(strict=True)
        binding=json.loads((selected/'runtime.json').read_text())
        self.assertEqual((binding['model'],binding['size']),('yolox_s',640))
        self.assertNotEqual(selected,first_generation)
        self.assertTrue((first_generation/'runtime.json').is_file())

    def test_interrupted_upgrade_keeps_previous_selection(self):
        for sig in (signal.SIGTERM,signal.SIGINT):
            with self.subTest(signal=sig):
                before=self.legacy_state();config=(self.config/'kilix.env').read_bytes()
                ready=self.root/'ready';ready.unlink(missing_ok=True)
                process=subprocess.Popen([str(ROOT/'scripts/install-yolox.sh'),'--upgrade','--yes'],
                    env=dict(self.env,PRIVATE_FAIL_STAGE='block'),stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
                try:
                    deadline=time.monotonic()+10
                    while not ready.exists() and time.monotonic()<deadline:
                        time.sleep(.02)
                    self.assertTrue(ready.exists(),'fake package installer did not start')
                    os.kill(process.pid,sig)
                    process.communicate(timeout=12)
                    self.assertEqual(process.returncode,128+sig)
                    self.assertEqual(self.legacy_state(),before)
                    self.assertEqual((self.config/'kilix.env').read_bytes(),config)
                    self.assertFalse((self.base/'.current').exists())
                    self.assertEqual(list((self.base/'.generations').glob('gen-*')),[])
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid,signal.SIGKILL)
                        process.communicate(timeout=5)

    def test_concurrent_selection_change_refuses_activation(self):
        before=self.legacy_state();result=self.run_installer(PRIVATE_FAIL_STAGE='concurrent')
        self.assertNotEqual(result.returncode,0,result.stderr)
        self.assertIn('selection changed',result.stderr)
        self.assertEqual(self.legacy_state(),before)
        self.assertFalse((self.base/'.current').exists())
        self.assertEqual((self.config/'kilix.env').read_text(),'KILIX_OBJECT_DETECTOR=concurrent-custom\n')

    def test_activation_failure_restores_previous_pointer_and_config(self):
        first=self.run_installer()
        self.assertEqual(first.returncode,0,first.stderr)
        selected=(self.base/'.current').resolve(strict=True)
        config=(self.config/'kilix.env').read_bytes()
        binding=(selected/'runtime.json').read_bytes()
        spec=importlib.util.spec_from_file_location('yolox_generation_fixture',ROOT/'scripts/yolox-generation.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        argv=[str(ROOT/'scripts/yolox-generation.py'),str(ROOT/'scripts/install-yolox.sh'),
              str(self.host),str(self.base),str(self.source),str(self.config/'kilix.env'),'yolox_nano','320']
        with mock.patch.dict(os.environ,self.env,clear=True), mock.patch.object(sys,'argv',argv), \
             mock.patch.object(module,'rename_flags',side_effect=OSError('injected activation failure')):
            with self.assertRaisesRegex(OSError,'injected activation failure'):
                module.main()
        self.assertEqual((self.base/'.current').resolve(strict=True),selected)
        self.assertEqual((self.config/'kilix.env').read_bytes(),config)
        self.assertEqual((selected/'runtime.json').read_bytes(),binding)
        self.assertEqual(self.run_installer('--check').returncode,0)
        # Once a candidate pointer was briefly visible, conservatively keep it
        # in case an already-started reader still holds that generation path.
        self.assertEqual(len(list((self.base/'.generations').glob('gen-*'))),2)

    def test_config_change_at_exchange_is_restored(self):
        first=self.run_installer()
        self.assertEqual(first.returncode,0,first.stderr)
        selected=(self.base/'.current').resolve(strict=True)
        binding=(selected/'runtime.json').read_bytes()
        spec=importlib.util.spec_from_file_location('yolox_generation_race',ROOT/'scripts/yolox-generation.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        original_rename=module.rename_flags
        conf=self.config/'kilix.env';changed=b'KILIX_OBJECT_DETECTOR=/concurrent/custom\n'
        calls=[]
        def exchange(source,destination,flags):
            if flags==2 and not calls:
                calls.append(True)
                conf.write_bytes(changed)
            return original_rename(source,destination,flags)
        argv=[str(ROOT/'scripts/yolox-generation.py'),str(ROOT/'scripts/install-yolox.sh'),
              str(self.host),str(self.base),str(self.source),str(conf),'yolox_nano','320']
        with mock.patch.dict(os.environ,self.env,clear=True), mock.patch.object(sys,'argv',argv), \
             mock.patch.object(module,'rename_flags',side_effect=exchange):
            with self.assertRaisesRegex(ValueError,'changed during activation'):
                module.main()
        self.assertTrue(calls)
        self.assertEqual(conf.read_bytes(),changed)
        self.assertEqual((self.base/'.current').resolve(strict=True),selected)
        self.assertEqual((selected/'runtime.json').read_bytes(),binding)

    def test_concurrent_pointer_change_is_not_overwritten_on_failure(self):
        first=self.run_installer()
        self.assertEqual(first.returncode,0,first.stderr)
        old=(self.base/'.current').resolve(strict=True)
        config=(self.config/'kilix.env').read_bytes()
        external=self.base/'.generations/gen-external';external.mkdir()
        spec=importlib.util.spec_from_file_location('yolox_generation_pointer',ROOT/'scripts/yolox-generation.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        def fail_after_external_selection(_source,_destination,_flags):
            module.make_link(self.base,'.generations/gen-external')
            raise OSError('injected activation failure')
        argv=[str(ROOT/'scripts/yolox-generation.py'),str(ROOT/'scripts/install-yolox.sh'),
              str(self.host),str(self.base),str(self.source),str(self.config/'kilix.env'),'yolox_nano','320']
        with mock.patch.dict(os.environ,self.env,clear=True), mock.patch.object(sys,'argv',argv), \
             mock.patch.object(module,'rename_flags',side_effect=fail_after_external_selection):
            with self.assertRaisesRegex(OSError,'injected activation failure'):
                module.main()
        self.assertEqual((self.base/'.current').resolve(strict=True),external)
        self.assertEqual((self.config/'kilix.env').read_bytes(),config)
        self.assertTrue((old/'runtime.json').is_file())

    def test_print_ref_does_not_depend_on_runtime_pointer(self):
        (self.base/'.current').symlink_to('../invalid-selection')
        result=self.run_installer('--print-ref')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertRegex(result.stdout.strip(),r'^[0-9a-f]{40}$')
        self.assertEqual(os.readlink(self.base/'.current'),'../invalid-selection')

    def test_unsafe_source_overlap_and_alternates_refuse(self):
        before=self.legacy_state();config=(self.config/'kilix.env').read_bytes()
        result=self.run_installer('--install',KILIX_YOLOX_SRC=str(self.base))
        self.assertNotEqual(result.returncode,0)
        self.assertIn('source overlaps',result.stderr)
        self.assertEqual(self.legacy_state(),before)
        self.assertEqual((self.config/'kilix.env').read_bytes(),config)
        info=self.source/'.git/objects/info';info.mkdir(parents=True)
        (info/'alternates').write_text('/nonexistent\n')
        result=self.run_installer('--install')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Git alternates',result.stderr)
        self.assertEqual((self.config/'kilix.env').read_bytes(),config)
        shutil.rmtree(self.source/'.git')
        (self.source/'tools/outside').symlink_to(self.root, target_is_directory=True)
        result=self.run_installer('--install')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('escaping symlink',result.stderr)
        self.assertEqual((self.config/'kilix.env').read_bytes(),config)

    def test_source_snapshot_checkout_does_not_run_user_git_hook(self):
        def git(*args):
            return subprocess.check_output(['git','-C',str(self.source),'-c','user.name=fixture',
                '-c','user.email=fixture@example.invalid',*args],text=True).strip()
        git('init','-q')
        git('add','-A');git('commit','-qm','first')
        old=git('rev-parse','HEAD')
        (self.source/'marker').write_text('second')
        git('add','marker');git('commit','-qm','second')
        new=git('rev-parse','HEAD')
        git('checkout','--quiet','--detach',old)
        git('remote','add','origin',str(self.source))
        marker=self.root/'unexpected-hook'
        hook=self.source/'.git/hooks/post-checkout'
        hook.write_text('#!/bin/sh\nprintf ran > '+str(marker)+'\n');hook.chmod(0o700)
        result=self.run_installer('--install',KILIX_YOLOX_REPO=str(self.source),KILIX_YOLOX_REF=new,
                                  KILIX_YOLOX_TRUST_EXISTING_CHECKOUT='0')
        self.assertEqual(result.returncode,0,result.stderr)
        selected=(self.base/'.current').resolve(strict=True)
        self.assertEqual(git('rev-parse','HEAD'),old)
        self.assertEqual(json.loads((selected/'runtime.json').read_text())['source_revision'],new)
        self.assertFalse(marker.exists())

    def test_busy_generation_lock_preserves_previous_selection(self):
        before=self.legacy_state();config=(self.config/'kilix.env').read_bytes()
        with (self.base/'.generation.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            result=self.run_installer()
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(self.legacy_state(),before)
        self.assertEqual((self.config/'kilix.env').read_bytes(),config)
        self.assertFalse((self.base/'.current').exists())

    def test_internal_preparation_cannot_mutate_selected_generation(self):
        result=self.run_installer()
        self.assertEqual(result.returncode,0,result.stderr)
        selected=(self.base/'.current').resolve(strict=True)
        binding=(selected/'runtime.json').read_bytes()
        result=self.run_installer('--install',KILIX_YOLOX_INTERNAL='1',KILIX_YOLOX_PARENT=str(self.base),
                                  KILIX_YOLOX_DIR=str(selected),KILIX_YOLOX_SRC=str(selected/'source'))
        self.assertNotEqual(result.returncode,0)
        self.assertIn('selected generation in place',result.stderr)
        self.assertEqual((selected/'runtime.json').read_bytes(),binding)

    def test_offline_wheel_inputs_require_pinned_hashes_and_no_index(self):
        wheelhouse=self.root/'wheelhouse';wheelhouse.mkdir()
        requirements=self.root/'requirements.txt'
        requirements.write_text('fixture==1.0 --hash=sha256:'+'0'*64+'\n')
        extra={'KILIX_PYTHON':str(self.python_command),'KILIX_YOLOX_WHEELHOUSE':str(wheelhouse),
               'PIP_FIND_LINKS':'https://example.invalid/hostile',
               'KILIX_YOLOX_REQUIREMENTS':str(requirements)}
        result=self.run_installer(**extra)
        self.assertEqual(result.returncode,0,result.stderr)
        selected=(self.base/'.current').resolve(strict=True)
        call=json.loads((selected/'venv/pip.log').read_text())
        self.assertIn('--no-index',call['args'])
        self.assertIn('--require-hashes',call['args'])
        self.assertIn(str(selected/'.requirements.lock'),call['args'])
        self.assertEqual((selected/'.requirements.lock').read_bytes(),requirements.read_bytes())
        self.assertIn(str(wheelhouse),call['args'])
        self.assertIsNone(call['find_links'])
        self.assertEqual(call['config'],'/dev/null')
        config=(self.config/'kilix.env').read_bytes()
        (selected/'venv/pip.log').unlink()
        requirements.write_text('--extra-index-url https://example.invalid/simple\n')
        result=self.run_installer(**extra)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('only pinned, hashed packages',result.stderr)
        self.assertFalse((selected/'venv/pip.log').exists())
        self.assertEqual((self.base/'.current').resolve(strict=True),selected)
        self.assertEqual((self.config/'kilix.env').read_bytes(),config)


if __name__ == '__main__':
    unittest.main()
