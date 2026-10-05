import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
from kilix_sdk import capture_registry as registry


class CapturePublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.runtime=self.root/'runtime';self.runtime.mkdir(mode=0o700)
        self.auth=self.root/'authority';self.auth.write_bytes(b'cookie must never enter registry');self.auth.chmod(0o600)
        self.proc=self.root/'proc';self.proc.mkdir()
        owner=os.getpid()
        for pid,parent,tick in ((owner,1,10),(501,owner,11),(502,owner,12)):
            path=self.proc/str(pid);path.mkdir()
            (path/'stat').write_text(str(pid)+' (process) '+' '.join(['S',str(parent)]+['0']*17+[str(tick)]))
        self.session=SimpleNamespace(display=':91',xauthority=str(self.auth),server=SimpleNamespace(pid=501),app=SimpleNamespace(pid=502))
        root=SimpleNamespace(id=10,get_geometry=lambda:SimpleNamespace(width=640,height=480))
        self.session.connect=lambda:SimpleNamespace(screen=lambda:SimpleNamespace(root=root))
        for patch in (mock.patch.object(registry,'_PROC',self.proc),
                      mock.patch.dict(os.environ,{'XDG_RUNTIME_DIR':str(self.runtime),'DISPLAY':':0',
                                                  'PLEB_DESKTOP_DISPLAY':':0','KITTY_PUBLIC_KEY':'private-route-marker'})):
            patch.start();self.addCleanup(patch.stop)

    def test_publication_contains_only_source_identity_and_is_private(self):
        publication=registry.publish(self.session,'Test\n  application')
        self.assertIsNotNone(publication)
        value=json.loads(publication.path.read_text())
        self.assertEqual((value['label'],value['desktop_display'],value['display']),('Test application',':0',':91'))
        self.assertEqual(value['owner_pid'],os.getpid())
        self.assertEqual((value['owner_start'],value['server_start'],value['app_start']),('10','11','12'))
        self.assertEqual(publication.path.stat().st_mode&0o777,0o600)
        self.assertEqual(publication.path.parent.stat().st_mode&0o777,0o700)
        self.assertNotIn(self.auth.read_text(),publication.path.read_text())
        self.assertNotIn('private-route-marker',publication.path.read_text())
        publication.close();publication.close();self.assertFalse(publication.path.exists())

    def test_unavailable_runtime_is_optional_and_does_not_create_files(self):
        with mock.patch.dict(os.environ,{'XDG_RUNTIME_DIR':''}):self.assertIsNone(registry.publish(self.session,'Test'))
        self.assertFalse((self.runtime/'kilix-capture-sources').exists())

    def test_shared_or_symlink_runtime_and_authority_are_refused(self):
        self.runtime.chmod(0o755);self.assertIsNone(registry.publish(self.session,'Test'));self.runtime.chmod(0o700)
        alias=self.root/'alias';alias.symlink_to(self.runtime)
        with mock.patch.dict(os.environ,{'XDG_RUNTIME_DIR':str(alias)}):self.assertIsNone(registry.publish(self.session,'Test'))
        auth_alias=self.root/'auth-alias';auth_alias.symlink_to(self.auth)
        self.session.xauthority=str(auth_alias);self.assertIsNone(registry.publish(self.session,'Test'))
        self.assertFalse((self.runtime/'kilix-capture-sources').exists())

    def test_unowned_or_dead_children_cannot_publish(self):
        path=self.proc/'501/stat'
        path.write_text('501 (server) '+' '.join(['S','1']+['0']*17+['11']))
        self.assertIsNone(registry.publish(self.session,'Test'))
        self.session.server.pid=999
        self.assertIsNone(registry.publish(self.session,'Test'))
        self.assertFalse((self.runtime/'kilix-capture-sources').exists())

    def test_an_app_that_is_not_the_owners_child_cannot_publish(self):
        # The X server is ours but the application process is someone else's
        # child: the record would name a pane this provider does not own.
        (self.proc/'502/stat').write_text('502 (app) '+' '.join(['S','1']+['0']*17+['12']))
        self.assertIsNone(registry.publish(self.session,'Test'))
        self.assertFalse((self.runtime/'kilix-capture-sources').exists())

    def test_physical_or_remote_display_cannot_be_published_as_a_private_pane(self):
        for display in (':0','localhost:10.0',':91; command'):
            self.session.display=display
            self.assertIsNone(registry.publish(self.session,'Test'))

    def test_close_does_not_unlink_a_substituted_file(self):
        publication=registry.publish(self.session,'Test')
        replacement=self.root/'replacement';replacement.write_text('other record');replacement.replace(publication.path)
        publication.close();self.assertEqual(publication.path.read_text(),'other record')

    def test_hard_owner_deaths_are_pruned_without_removing_live_or_unknown_files(self):
        original=registry.publish(self.session,'Original')
        directory=original.path.parent
        value=json.loads(original.path.read_text());value.update(id='b'*32,owner_pid=999,owner_start='1')
        dead=directory/('b'*32+'.json');dead.write_text(json.dumps(value));dead.chmod(0o600)
        unknown=directory/('c'*32+'.json');unknown.write_text('unrecognized file');unknown.chmod(0o600)
        incomplete=directory/('d'*32+'.json')
        incomplete.write_text(json.dumps({'version':1,'id':'d'*32,'owner_pid':999,'owner_start':'1'}))
        incomplete.chmod(0o600)
        future=directory/('e'*32+'.json')
        future.write_text(json.dumps(dict(value,id='e'*32,version=2)));future.chmod(0o600)
        latest=registry.publish(self.session,'Latest')
        self.assertIsNotNone(latest)
        self.assertFalse(dead.exists());self.assertTrue(original.path.exists());self.assertTrue(unknown.exists())
        self.assertTrue(incomplete.exists());self.assertTrue(future.exists())
        self.assertEqual(list(directory.glob('*.pending')),[])
        self.assertEqual(latest.path.stat().st_nlink,1)


    def test_staging_files_left_by_a_killed_provider_are_pruned_only_once_stale(self):
        directory=registry.publish(self.session,'Original').path.parent
        old=os.stat(directory).st_mtime-3600
        abandoned=directory/('a'*32+'.pending');abandoned.write_text('{"version": 1, "id"');abandoned.chmod(0o600)
        os.utime(abandoned,(old,old))
        in_progress=directory/('b'*32+'.pending');in_progress.write_text('{}');in_progress.chmod(0o600)
        unknown=directory/'notes.pending';unknown.write_text('kept');os.utime(unknown,(old,old))
        outside=self.root/'outside';outside.write_text('kept')
        linked=directory/('c'*32+'.pending');linked.symlink_to(outside)
        os.utime(linked,(old,old),follow_symlinks=False)
        self.assertIsNotNone(registry.publish(self.session,'Latest'))
        self.assertFalse(abandoned.exists())
        self.assertTrue(in_progress.exists());self.assertTrue(unknown.exists())
        self.assertTrue(linked.is_symlink());self.assertEqual(outside.read_text(),'kept')


if __name__=='__main__':unittest.main()
