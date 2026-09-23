"""Real installed-package controls; supply KILIX_TEST_NATIVE_PREFIX explicitly.

These do not acquire a package or model. The existing small package-parser
fixtures remain separate from these actual installed-byte integration tests.
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env_support import sandbox_env  # noqa: E402


class InstalledBuildTests(unittest.TestCase):
    def setUp(self):
        prefix = os.environ.get('KILIX_TEST_NATIVE_PREFIX')
        if not prefix:
            self.skipTest('explicit source-bound native package is required')
        self.prefix = Path(prefix).resolve(strict=True)
        self.assertTrue((self.prefix/'share/doc/kilix-encodec/native-package.json').is_file())
        self.tmp = tempfile.TemporaryDirectory(prefix='kmx-input-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root/'source';self.source.mkdir();(self.source/'include').mkdir()
        (self.source/'include/kilix_mux.h').write_text('fixture\n')
        self.headers=self.root/'headers';self.headers.mkdir()
        self.header=self.headers/'guard.h';self.header.write_text('#define MARKER 3\n')
        self.trace=self.root/'trace';self.trace.touch()
        self.control=self.root/'control';self.control.mkdir()
        self.main='#include <guard.h>\nextern int kenc_installed_assets_open(void);\nint main(int n,char **v) { (void)v; if(n==99)return kenc_installed_assets_open(); return MARKER; }\n'
        (self.source/'main.c').write_text(self.main)
        self.recipe=''
        self.write_make()
        self.git('init','-q');self.git('config','user.name','itsmygithubacct')
        self.git('config','user.email','itsmygithubacct@users.noreply.github.com')
        self.commit()
        self.env=sandbox_env(KILIX_STORAGE_HOME=str(self.root/'storage'),
                      KILIX_MULTIPLEXER_HOME=str(self.source),KILIX_MULTIPLEXER_COMMIT=self.head,
                      PKG_CONFIG_PATH=str(self.prefix/'lib/pkgconfig'),CFLAGS='-I'+str(self.headers))
        ort=os.environ.get('KILIX_TEST_ORT_LIB','')
        self.env['LD_LIBRARY_PATH']=str(self.prefix/'lib')+(':'+ort if ort else '')
        self.env['LDFLAGS']='-Wl,-rpath-link,'+ort if ort else ''
        self.base=self.root/'storage/build/libraries/kilix-multiplexer'

    def git(self,*args):
        return subprocess.check_output(['git','-C',str(self.source),*args],stderr=subprocess.DEVNULL,text=True).strip()

    def commit(self):
        self.git('add','.');self.git('commit','-qm','local compiler fixture');self.head=self.git('rev-parse','HEAD')
        if hasattr(self,'env'):self.env['KILIX_MULTIPLEXER_COMMIT']=self.head

    def write_make(self):
        (self.source/'Makefile').write_text('all: $(BUILD_DIR)/kmx-serve $(BUILD_DIR)/kmx-attach\n'
            '$(BUILD_DIR)/kmx-%: main.c Makefile\n\ttest "$(ENCODEC)" = 1\n'+self.recipe+
            '\t$(CC) $(CPPFLAGS) $(CFLAGS) $(ENCODEC_CFLAGS) main.c -o $@ $(LDFLAGS) $(ENCODEC_LIBS) $(LDLIBS)\n'
            '\tprintf "compile\\n" >> "'+str(self.trace)+'"\n')

    def command(self):
        return [str(ROOT/'scripts/build-multiplexer.sh'),'--print-path','serve']

    def run_build(self,success=True):
        result=subprocess.run(self.command(),env=self.env,capture_output=True,text=True,timeout=20)
        if success:self.assertEqual(result.returncode,0,result.stderr)
        else:self.assertNotEqual(result.returncode,0,result.stdout)
        return result

    def record(self):return json.loads((self.base/'build-identity').read_text())
    def count(self):return len(self.trace.read_text().splitlines())
    def value(self):return subprocess.run([str(self.base/'kmx-serve')],env=self.env).returncode
    def published(self):
        return tuple(hashlib.sha256((self.base/name).read_bytes()).hexdigest() for name in ('kmx-serve','kmx-attach','build-identity'))

    def gate(self):
        self.recipe='\techo enter >> "'+str(self.control/'entered')+'"; while test ! -f "'+str(self.control/'release')+'"; do sleep .02; done\n'
        self.write_make();self.commit()

    def start(self,argv=None):
        child=subprocess.Popen(argv or self.command(),env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        def cleanup():
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=4)
                except subprocess.TimeoutExpired:child.kill();child.wait()
            child.stdout.close();child.stderr.close()
        self.addCleanup(cleanup)
        return child

    def wait(self,fn):
        deadline=time.monotonic()+6
        while time.monotonic()<deadline:
            if fn():return
            time.sleep(.01)
        self.fail('fixture boundary was not reached')

    def test_supported_flags_and_actual_header_bytes_invalidate(self):
        self.run_build();self.assertEqual(self.value(),3)
        first=self.count();self.run_build();self.assertEqual(self.count(),first)
        self.env['CFLAGS']+=' -O1';self.run_build();self.assertEqual(self.count(),first+2)
        self.header.write_text('#define MARKER 7\n')
        self.run_build();self.assertEqual(self.value(),7);self.assertEqual(self.count(),first+4)
        self.assertIn(str(self.header),self.record()['dependencies'])

    def test_ambient_search_and_override_variables_do_not_select_inputs(self):
        other=self.root/'other';other.mkdir();(other/'guard.h').write_text('#define MARKER 19\n')
        self.env.update(CPATH=str(other),C_INCLUDE_PATH=str(other),LIBRARY_PATH=str(other),
                        COMPILER_PATH=str(other),GCC_EXEC_PREFIX=str(other)+'/',MAKEFLAGS='-e',BASH_ENV='/nonexistent')
        self.run_build();self.assertEqual(self.value(),3)
        self.env.pop('CFLAGS')
        refused=self.run_build(False);self.assertIn('guard.h',refused.stderr)

    def test_hidden_and_ordinary_dirty_source_refuse(self):
        for flag in ('--skip-worktree','--assume-unchanged'):
            with self.subTest(flag=flag):
                self.git('update-index',flag,'main.c')
                (self.source/'main.c').write_text(self.main.replace('return MARKER','return 19'))
                self.assertEqual(self.git('status','--porcelain'),'')
                self.assertIn('raw committed',self.run_build(False).stderr)
                (self.source/'main.c').write_text(self.main)
                self.git('update-index',flag.replace('--','--no-',1),'main.c')
        (self.source/'main.c').write_text(self.main+'/* ordinary change */\n')
        self.assertIn('must be clean',self.run_build(False).stderr)
        self.assertEqual(self.count(),0)

    def test_source_mutation_during_compile_never_replaces_published_binary(self):
        self.run_build();before=self.published();self.gate()
        child=self.start();self.wait(lambda:(self.control/'entered').exists())
        generations=list(self.base.glob('generation-*'))
        self.assertTrue(any((g/'source/main.c').read_text()==self.main for g in generations))
        (self.source/'main.c').write_text(self.main.replace('return MARKER','return 19'))
        (self.control/'release').touch()
        _out,err=child.communicate(timeout=8)
        self.assertNotEqual(child.returncode,0);self.assertIn('must be clean',err)
        self.assertEqual(self.published(),before);self.assertEqual(self.value(),3)

    def test_external_header_mutation_during_compile_refuses(self):
        self.run_build();before=self.published();self.gate()
        child=self.start();self.wait(lambda:(self.control/'entered').exists())
        self.header.write_text('#define MARKER 11\n');(self.control/'release').touch()
        _out,err=child.communicate(timeout=8)
        self.assertNotEqual(child.returncode,0);self.assertIn('dependency changed',err)
        self.assertEqual(self.published(),before)

    def test_failed_compile_preserves_previous_generation(self):
        self.run_build();before=self.published()
        (self.source/'main.c').write_text('invalid C syntax;\n');self.commit()
        self.run_build(False);self.assertEqual(self.published(),before)
        self.assertEqual(len(list(self.base.glob('generation-*'))),1)

    def test_simultaneous_callers_hold_the_same_lock(self):
        self.gate();first=self.start()
        self.wait(lambda:(self.control/'entered').exists() and len((self.control/'entered').read_text().splitlines())==2)
        second=self.start();time.sleep(.15)
        self.assertIsNone(second.poll());self.assertEqual((self.control/'entered').read_text().splitlines(),['enter','enter'])
        (self.control/'release').touch()
        for child in (first,second):
            _out,err=child.communicate(timeout=10);self.assertEqual(child.returncode,0,err)
        self.assertEqual(self.value(),3)

    def test_actual_linker_input_change_invalidates(self):
        library=self.root/'extra.a';object_file=self.root/'extra.o';cfile=self.root/'extra.c'
        def prepare(value):
            cfile.write_text('int extra(void) { return '+str(value)+'; }\n')
            subprocess.run(['/usr/bin/cc','-c',str(cfile),'-o',str(object_file)],check=True,capture_output=True)
            subprocess.run(['/usr/bin/ar','rcs',str(library),str(object_file)],check=True,capture_output=True)
        (self.source/'main.c').write_text(self.main.replace('int main(', 'int extra(void);\nint main(').replace('return MARKER','return extra()'))
        self.commit();self.env['LDLIBS']=str(library)
        prepare(5);self.run_build();self.assertEqual(self.value(),5)
        self.assertIn(str(library),self.record()['dependencies'])
        prepare(9);self.run_build();self.assertEqual(self.value(),9)

    def test_unreported_compiler_and_linker_inputs_refuse(self):
        for key,value in [('CFLAGS','-fprofile-use=/unused'),('CFLAGS','@/unused'),
                          ('CFLAGS','-flto'),('LDFLAGS','-Wl,--version-script,/unused')]:
            with self.subTest(value=value):
                old=self.env.get(key);self.env[key]=value
                self.assertIn('unsupported',self.run_build(False).stderr)
                if old is None:self.env.pop(key)
                else:self.env[key]=old
        self.assertEqual(self.count(),0)

    def interrupted_case(self,number):
        self.run_build();before=self.published()
        pidfile=self.control/'escaped.pid'
        # The actual helper tree owns this grandchild; it changes its session
        # and ignores SIGTERM, so killing only the make group cannot pass.
        inner='import os,signal,time; os.setsid(); signal.signal(signal.SIGTERM,signal.SIG_IGN); open('+repr(str(pidfile))+',"a").write(str(os.getpid())+"\\n"); time.sleep(30)'
        outer='import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",'+repr(inner)+']); time.sleep(30)'
        import shlex
        self.recipe='\t/usr/bin/python3 -c '+shlex.quote(outer)+'\n';self.write_make();self.commit()
        argv=None if number is not None else ['/usr/bin/python3',str(ROOT/'config/multiplexer_build.py'),
              '--source',str(self.source),'--commit',self.head,'--build-dir',str(self.base),'--timeout','2']
        fd_before=len(os.listdir('/proc/self/fd'));started=time.monotonic()
        child=self.start(argv);self.wait(lambda:pidfile.exists() and len(pidfile.read_text().splitlines())==2)
        pids=[int(value) for value in pidfile.read_text().splitlines()]
        self.assertTrue(all(Path('/proc',str(pid)).exists() for pid in pids))
        if number is not None:child.send_signal(number)
        _out,err=child.communicate(timeout=6)
        self.assertNotEqual(child.returncode,0);self.assertIn('interrupted' if number is not None else 'deadline',err)
        self.assertTrue(all(not Path('/proc',str(pid)).exists() for pid in pids));self.assertEqual(self.published(),before)
        child.stdout.close();child.stderr.close();self.assertEqual(len(os.listdir('/proc/self/fd')),fd_before)
        if number is None:self.assertLess(time.monotonic()-started,3)

    def test_interrupt_reaps_escaped_child_and_preserves_previous_outputs(self):
        self.interrupted_case(signal.SIGINT)

    def test_termination_reaps_escaped_child_and_preserves_previous_outputs(self):
        self.interrupted_case(signal.SIGTERM)

    def test_original_deadline_reaps_escaped_child_and_preserves_previous_outputs(self):
        self.interrupted_case(None)


if __name__=='__main__':unittest.main()
