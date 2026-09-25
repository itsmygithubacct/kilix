"""Real make/freshness refusal controls with a tiny non-codec source fixture."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env_support import sandbox_env  # noqa: E402

ROOT=Path(__file__).resolve().parents[1]
FIXTURE_PARENT=ROOT/'.test-tmp'


class MultiplexerBuildTests(unittest.TestCase):
    def setUp(self):
        # The build identity binds every parent directory of its inputs, and
        # /tmp changes whenever any process on the host adds or removes an
        # entry there, which forces a correct rebuild and hides freshness.
        # The checkout's own parents are quiet; keep the fixture below them.
        FIXTURE_PARENT.mkdir(mode=0o700,exist_ok=True)
        self.temporary=tempfile.TemporaryDirectory(dir=FIXTURE_PARENT)
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.trace=self.root/'trace';self.trace.touch()
        self.source=self.root/'source';self.source.mkdir()
        (self.source/'include').mkdir()
        (self.source/'include/kilix_mux.h').write_text('fixture header\n')
        (self.source/'main.c').write_text('extern int kenc_installed_assets_open(void);\nint main(void) { return kenc_installed_assets_open(); }\n')
        (self.source/'Makefile').write_text('all: $(BUILD_DIR)/kmx-serve $(BUILD_DIR)/kmx-attach\n'
            '$(BUILD_DIR)/kmx-%: Makefile\n'
            '\ttest "$(ENCODEC)" = 1\n'
            '\t$(CC) $(CFLAGS) $(ENCODEC_CFLAGS) main.c -o $@ $(ENCODEC_LIBS)\n'
            '\tprintf "enabled\\n" >> "'+str(self.trace)+'"\n')
        for args in (('init','-q'),('config','user.name','Kilix Test'),
                     ('config','user.email','test@example.invalid'),('add','.'),('commit','-qm','fixture')):
            self.git(*args)
        self.commit=self.git('rev-parse','HEAD').strip()
        self.prefix=self.root/'package/usr';self.prefix.mkdir(parents=True)
        self.content='a'*40
        stub=self.root/'library.c';stub.write_text('int kenc_installed_assets_open(void) { return 0; }\n')
        library=self.root/'library.so'
        subprocess.run(['cc','-shared','-fPIC','-Wl,-soname,libkilix-encodec.so.0','-o',str(library),str(stub)],check=True,capture_output=True)
        self.members={
            'lib/libkilix-encodec.so.0':library.read_bytes(),
            'lib/pkgconfig/kilix-encodec.pc':b'prefix=/usr\nlibdir=${prefix}/lib\nincludedir=${prefix}/include\nName: kilix-encodec\nDescription: fixture\nVersion: 1\nLibs: -L${libdir} -lkilix-encodec\nCflags: -I${includedir}\n',
            'include/kilix_encodec.h':b'header', 'include/kilix_encodec_content.h':b'admission',
            'include/kilix_encodec_file.h':b'file header',
            'share/doc/kilix-encodec/LICENSE':b'fixture license',
            'share/doc/kilix-encodec/CONTENT-LICENSE.txt':b'fixture content license',
            'share/doc/kilix-encodec/source.tar.gz':b'fixture source bytes',
            'share/doc/kilix-encodec/README.md':b'fixture readme',
        }
        self.record_path=self.prefix/'share/doc/kilix-encodec/native-package.json'
        self.write_package()
        (self.prefix/'lib/libkilix-encodec.so').symlink_to('libkilix-encodec.so.0')
        self.trace=self.root/'trace'
        self.env={k:v for k,v in sandbox_env().items() if not k.startswith('GIT_')}
        self.env.update(HOME=str(self.root),KILIX_STORAGE_HOME=str(self.root/'storage'),
                        KILIX_MULTIPLEXER_HOME=str(self.source),KILIX_MULTIPLEXER_COMMIT=self.commit,
                        PKG_CONFIG_PATH=str(self.prefix/'lib/pkgconfig'),TRACE=str(self.trace),
                        LD_LIBRARY_PATH=str(self.prefix/'lib'))

    def git(self,*args):
        return subprocess.check_output(['git','-C',str(self.source),*args],stderr=subprocess.DEVNULL,text=True)

    def write_package(self):
        self.members['share/doc/kilix-encodec/content_bundle.receipt.json']=json.dumps(
            {'content_commit':self.content,'bundle_sha256':'b'*64}).encode()
        record={'schema':'kilix.encodec.native-package/v1','source_commit':'c'*40,
                'content_commit':self.content,'content_bundle_sha256':'b'*64,
                'build':{'ONNX':1,'CONTENT':1,'PREFIX':'/usr'},'files':{}}
        for name,value in self.members.items():
            path=self.prefix/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(value)
            record['files']['usr/'+name]={'bytes':len(value),'sha256':hashlib.sha256(value).hexdigest(),'mode':0o644}
        self.record_path.write_text(json.dumps(record))

    def run_build(self,success=True):
        result=subprocess.run([str(ROOT/'scripts/build-multiplexer.sh'),'--print-path','serve'],
                              env=self.env,text=True,capture_output=True,timeout=20)
        if success:self.assertEqual(result.returncode,0,result.stderr)
        else:self.assertNotEqual(result.returncode,0,result.stdout)
        return result

    def count(self):return len(self.trace.read_text().splitlines()) if self.trace.exists() else 0

    def parents(self):
        return [(str(path),path.lstat().st_ctime_ns) for path in (self.root,*self.root.parents)]

    def build_expecting(self,rebuild):
        """Run the build and assert whether it rebuilt.

        The build identity binds every parent directory of its inputs, so when
        another process adds or removes an entry in any fixture parent the
        next build rightly rebuilds. That is an input change, not
        a freshness failure: a step that must not rebuild is repeated until it
        runs with every parent unchanged, and a step that must rebuild accepts
        any extra rebuild caused by such a change.
        """
        for _ in range(10):
            before=self.count();settled=self._settled
            result=self.run_build();built=self.count()-before
            self._settled=self.parents()
            if rebuild:
                self.assertGreaterEqual(built,2)
                if built!=2:self.assertNotEqual(self._settled,settled)
                return result
            if built==0:return result
            self.assertNotEqual(self._settled,settled,'rebuilt with no input change')
        self.fail('fixture parent directories kept changing between builds')

    def test_source_flags_content_and_output_bytes_control_freshness(self):
        self._settled=self.parents()
        first=self.build_expecting(True)
        self.build_expecting(False)
        self.env['CFLAGS']='-O1'
        self.build_expecting(True)
        self.content='d'*40;self.write_package()
        self.build_expecting(True)
        binary=Path(first.stdout.strip());binary.write_text('#!/bin/sh\nexit 7\n')
        self.build_expecting(True)
        self.assertEqual(subprocess.run([str(binary)],env=self.env).returncode,0)
        self.git('commit','--allow-empty','-qm','new source identity')
        self.env['KILIX_MULTIPLEXER_COMMIT']=self.git('rev-parse','HEAD').strip()
        self.build_expecting(True)

    def test_missing_or_mutated_package_refuses_without_disabled_build(self):
        self.run_build();before=self.count()
        self.record_path.unlink()
        self.assertIn('explicit system setup',self.run_build(False).stderr)
        self.assertEqual(self.count(),before)
        self.write_package()
        (self.prefix/'lib/libkilix-encodec.so.0').write_bytes(b'replaced')
        self.assertIn('member differs',self.run_build(False).stderr)
        self.assertEqual(self.count(),before)

    def test_modified_source_and_unpinned_override_refuse(self):
        self.env['KILIX_MULTIPLEXER_COMMIT']=''
        self.run_build(False);self.assertEqual(self.count(),0)
        self.env['KILIX_MULTIPLEXER_COMMIT']=self.commit
        (self.source/'Makefile').write_text('all:\n\tfalse\n')
        self.assertIn('must be clean',self.run_build(False).stderr)
        self.assertEqual(self.count(),0)

    def test_symlink_stamp_and_fifo_refuse_without_following(self):
        self.run_build()
        stamp=self.root/'storage/build/libraries/kilix-multiplexer/build-identity'
        stamp.unlink();target=self.root/'preserved';target.write_text('sentinel')
        stamp.symlink_to(target)
        self.run_build(False);self.assertEqual(target.read_text(),'sentinel')
        stamp.unlink();os.mkfifo(stamp)
        self.run_build(False)

    def test_legacy_source_cannot_publish_disabled_backend_identity(self):
        (self.source/'main.c').write_text('int main(void) { return 0; }\n')
        self.git('add','main.c');self.git('commit','-qm','legacy fixture')
        self.env['KILIX_MULTIPLEXER_COMMIT']=self.git('rev-parse','HEAD').strip()
        result=self.run_build(False)
        self.assertIn('did not link installed EnCodec admission',result.stderr)
        self.assertFalse((self.root/'storage/build/libraries/kilix-multiplexer/build-identity').exists())


if __name__=='__main__':unittest.main()
