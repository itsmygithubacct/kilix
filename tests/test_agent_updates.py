import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'config'))
import agent_updates
import install


class AgentUpdatesTests(unittest.TestCase):
    def test_grok_uses_resolved_client_and_verifies_version(self):
        with patch.object(install, '_resolve_agent_command', return_value='/opt/tools/grok'), \
                patch.object(agent_updates.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            self.assertEqual(install.update('grok'), 0)
        self.assertEqual([c.args[0] for c in run.call_args_list],
                         [['/opt/tools/grok','update'],['/opt/tools/grok','--version']])

    def test_native_codex_uses_its_own_updater(self):
        with patch.object(agent_updates.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            self.assertEqual(agent_updates.update(install._agent('codex'), '/opt/codex'), 0)
        self.assertEqual(run.call_args_list[0].args[0], ['/opt/codex','update'])

    def test_npm_codex_preserves_prefix_umask_and_permissions_without_network(self):
        with tempfile.TemporaryDirectory(prefix='agent update ') as tmp:
            root=Path(tmp); prefix=root/'npm prefix'
            package=prefix/'lib/node_modules/@openai/codex';package.mkdir(parents=True)
            (package/'package.json').write_text(json.dumps({'name':'@openai/codex'}))
            binary=package/'codex';binary.write_text('#!/bin/sh\necho test-version\n');binary.chmod(0o700)
            launcher=root/'codex';launcher.symlink_to(binary)
            npm=root/'npm'; npm.write_text('#!/usr/bin/env python3\nimport os,sys\n'
                'assert os.umask(0o077)==0o022\n'
                'assert sys.argv[1:]==["install","--global","--prefix",os.environ["TEST_PREFIX"],"@openai/codex@latest"]\n')
            npm.chmod(0o700)
            with patch.dict(os.environ, {'TEST_PREFIX':str(prefix)}), \
                    patch.object(agent_updates.shutil, 'which', return_value=str(npm)):
                self.assertEqual(agent_updates.update(install._agent('codex'), str(launcher)), 0)
            self.assertEqual(binary.stat().st_mode & 0o777, 0o755)

    def test_unwritable_npm_prefix_uses_sudo_and_failure_stops_repair(self):
        with patch.object(agent_updates, 'codex_npm_package', return_value=(Path('/prefix/lib/node_modules/@openai/codex'),Path('/prefix'))), \
                patch.object(agent_updates.shutil, 'which', return_value='/bin/npm'), \
                patch.object(agent_updates.os, 'geteuid', return_value=1000), \
                patch.object(agent_updates.os, 'access', return_value=False), \
                patch.object(agent_updates.subprocess, 'run', return_value=Mock(returncode=19)) as run:
            self.assertEqual(agent_updates.update(install._agent('codex'), '/bin/codex'), 19)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][:2], ['sudo','--'])

    def test_batch_validates_before_running_and_reports_partial_failure(self):
        rows=[dict(id=k) for k in ('codex','grok')]
        with patch.object(install, 'rows', return_value=rows), patch.object(install, 'update') as update:
            self.assertEqual(install.main(['--update','codex','unknown']), 2)
            update.assert_not_called()
        with patch.object(install, 'rows', return_value=rows), \
                patch.object(install, 'update', side_effect=[7,0]) as update, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(install.main(['--update','codex','grok','codex']), 1)
        self.assertEqual([c.args[0] for c in update.call_args_list], ['codex','grok'])
        self.assertIn('codex: failed',out.getvalue());self.assertIn('grok: updated',out.getvalue())

    def test_interrupt_stops_remaining_updates(self):
        with patch.object(install, 'rows', return_value=[dict(id=k) for k in ('codex','grok')]), \
                patch.object(install, 'update', side_effect=KeyboardInterrupt) as update:
            self.assertEqual(install.main(['--update','codex','grok']), 130)
        update.assert_called_once_with('codex')

    def test_shared_yolo_cli_preserves_other_settings_and_launch_reads_it(self):
        from kilix_sdk import settings
        loader=importlib.machinery.SourceFileLoader('agent_settings_test',str(ROOT/'kilix-settings'))
        spec=importlib.util.spec_from_loader(loader.name,loader)
        cli=importlib.util.module_from_spec(spec);loader.exec_module(cli)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'settings.conf';path.write_text('OTHER_SETTING=keep\nKILIX_CODING_YOLO=off\n')
            with patch.dict(os.environ, {'GPU_TERMINAL_SETTINGS_FILE':str(path)}), \
                    patch.object(cli, '_notify_running_kilix'), contextlib.redirect_stdout(io.StringIO()) as out:
                for value in ('on','off'):
                    self.assertEqual(cli.main(['--set','coding_yolo='+value,'--print']),0)
                    self.assertEqual(settings.coding_yolo(),value=='on')
                    self.assertIn('OTHER_SETTING=keep',path.read_text())
                self.assertIn('KILIX_CODING_YOLO=on',out.getvalue())


if __name__ == '__main__': unittest.main()
