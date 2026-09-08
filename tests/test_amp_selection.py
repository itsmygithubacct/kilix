"""Actual catalog readiness/setup shares one caller-selected application root."""
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('amp_setup_selection', ROOT/'scripts/install-kilix-amp.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base/'source'
        self.source.mkdir()
        (self.source/'Makefile').write_text('all:\n\ttest "$(ENCODEC)" = 1\n\tprintf "#!/bin/sh\\nexit 0\\n" > kilix-amp\n\tchmod 755 kilix-amp\n')
        for args in (('init','-q'),('config','user.name','Kilix Test'),
                     ('config','user.email','test@example.invalid'),('add','.'),('commit','-qm','fixture')):
            subprocess.run(['git','-C',str(self.source),*args],check=True,capture_output=True)
        self.ref = subprocess.check_output(['git','-C',str(self.source),'rev-parse','HEAD'],text=True).strip()
        self.item = replace(setup.kilix_content.default_catalog().require('kilix-amp'),
            repository=str(self.source), ref=self.ref, build=('make','ENCODEC=1','all'))
        self.catalog = mock.Mock()
        self.catalog.require.return_value = self.item
        self.patch = mock.patch.object(setup.kilix_content,'default_catalog',return_value=self.catalog)
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.env = mock.patch.dict(os.environ, {'HOME':str(self.base/'home'),
            'KILIX_DATA_HOME':str(self.base/'host-data'),'KILIX_CONTENT_ROOT':str(self.base/'stale'),
            'PATH':os.defpath,'GIT_ALLOW_PROTOCOL':'file'}, clear=True)
        self.env.start();self.addCleanup(self.env.stop)

    def call(self,*args):
        stdout,stderr=io.StringIO(),io.StringIO()
        with redirect_stdout(stdout),redirect_stderr(stderr):
            code=setup.main(list(args))
        return code,stdout.getvalue(),stderr.getvalue()

    def test_absent_root_query_is_read_only_and_ignores_stale_environment(self):
        code,out,error=self.call('--resolve')
        self.assertEqual(code,0,error)
        value=json.loads(out)
        self.assertEqual(value,{'id':'kilix-amp','root':str(self.base/'host-data/desktop-apps'),
            'executable':None,'ref':self.ref,'build':['make','ENCODEC=1','all']})
        self.assertFalse((self.base/'host-data').exists())
        self.assertFalse((self.base/'stale').exists())

    def test_explicit_root_install_and_query_delegate_actual_full_spec(self):
        root=self.base/"95 apps 'quoted'"
        requested=str(root/'unused/..')
        code,out,error=self.call('--json','--content-root',requested)
        self.assertEqual(code,0,error)
        self.assertEqual(json.loads(out)['root'],str(root))
        expected=str(root/'kilix-amp/kilix-amp')
        self.assertEqual(json.loads(out)['executable'],expected)
        code,out,error=self.call('--resolve','--content-root',requested)
        self.assertEqual((code,json.loads(out)['executable']),(0,expected),error)
        self.assertFalse((self.base/'host-data').exists())
        self.assertFalse((self.base/'stale').exists())
        # A distinct actual root never borrows the first root's executable.
        code,out,error=self.call('--resolve')
        self.assertEqual(code,0,error)
        self.assertIsNone(json.loads(out)['executable'])

    def test_query_creates_no_root_even_if_a_parent_disappears(self):
        root=self.base/'existing';root.mkdir()
        with mock.patch.object(setup.os,'makedirs',side_effect=AssertionError('query mkdir')):
            self.assertEqual(self.call('--resolve','--content-root',str(root))[0],0)
            root.rmdir()
            self.assertEqual(self.call('--resolve','--content-root',str(root))[0],0)
        self.assertFalse(root.exists())

    def test_relative_file_and_symlink_roots_refuse(self):
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            setup.main(['--resolve','--content-root','relative'])
        file=self.base/'file';file.write_text('unchanged')
        link=self.base/'link';link.symlink_to(self.base)
        for root in (file,link):
            with self.subTest(root=root):
                self.assertEqual(self.call('--resolve','--content-root',str(root))[0],1)
        self.assertEqual(file.read_text(),'unchanged')

    def test_wrong_ref_origin_and_missing_binary_are_not_ready(self):
        root=self.base/'selected'
        self.assertEqual(self.call('--json','--content-root',str(root))[0],0)
        self.catalog.require.return_value=replace(self.item,ref='f'*40)
        self.assertIsNone(json.loads(self.call('--resolve','--content-root',str(root))[1])['executable'])
        self.catalog.require.return_value=self.item
        checkout=root/'kilix-amp'
        subprocess.run(['git','-C',str(checkout),'remote','set-url','origin',str(self.base/'wrong')],check=True)
        self.assertIsNone(json.loads(self.call('--resolve','--content-root',str(root))[1])['executable'])
        subprocess.run(['git','-C',str(checkout),'remote','set-url','origin',str(self.source)],check=True)
        (checkout/'kilix-amp').unlink()
        self.assertIsNone(json.loads(self.call('--resolve','--content-root',str(root))[1])['executable'])

    def test_auto_install_refusal_uses_explicit_root(self):
        root=self.base/'explicit'
        with mock.patch.dict(os.environ,{'KILIX_AMP_AUTO_INSTALL':'0'}):
            code,out,error=self.call('--json','--content-root',str(root))
        self.assertEqual(code,1)
        self.assertIn(str(root),error)
        self.assertEqual(out,'')
        self.assertFalse((root/'kilix-amp').exists())


if __name__=='__main__':unittest.main()
