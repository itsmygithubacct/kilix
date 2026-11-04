"""Pure close-dialog controls; no native engine or owner pane is launched."""
import ast
import importlib.util
import json
import os
import tempfile
from pathlib import Path
import sys
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'src/kitty/coding_close.py'
spec = importlib.util.spec_from_file_location('kitty.coding_close_fixture', SOURCE)
ui = importlib.util.module_from_spec(spec); spec.loader.exec_module(ui)

class CloseUITests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'KILIX_HOME': str(SOURCE.parents[2])})
        environment.start()
        self.addCleanup(environment.stop)


    def window(self, argv):
        child = SimpleNamespace(is_pty_brokered=True, foreground_processes=[{'pid': 123, 'cmdline': argv, 'cwd': '/fixture'}])
        row = {'id': 1, 'foreground_processes': child.foreground_processes,
               'env': {'KITTY_PTY_BROKER_SESSION': 'fixture'}}
        return SimpleNamespace(id=1, child=child, as_dict=lambda: row)

    def test_detects_four_clients_and_interpreter_paths(self):
        for argv in (['codex'], ['claude'], ['grok'], ['omp'],
                     ['/usr/local/bin/codex'], ['/home/pleb/.local/bin/claude'],
                     ['/home/pleb/.local/bin/grok'], ['/home/pleb/.local/bin/omp'],
                     ['node', '/usr/local/lib/node_modules/@openai/codex/bin/codex.js'],
                     ['node', '/opt/node_modules/@anthropic-ai/claude-code/cli.js']):
            self.assertTrue(ui.is_coding(self.window(argv)), argv)
        for argv in (['bash', '-c', 'codex'], ['python3', 'script.py', 'claude'], ['vim', 'grok'],
                     ['/home/claude/bin/vim'], ['/opt/codex/tools/editor'],
                     ['/opt/grok/bin/bash'], ['/opt/omp/bin/editor'],
                     ['node', '/opt/grok/dist/cli.js'], ['node', '/opt/codex/bin/editor.js'],
                     ['node', '/opt/node_modules/@openai/codex/bin/editor.js'],
                     ['node', '-e', '/opt/node_modules/@openai/codex/bin/codex.js'],
                     ['python3', '/opt/node_modules/@openai/codex/bin/codex.js'],
                     ['node', '/opt/node_modules/@openai/codex/bin/../bin/codex.js']):
            self.assertFalse(ui.is_coding(self.window(argv)), argv)

    def test_registry_is_shared_with_managed_launch(self):
        sys.path.insert(0, str(SOURCE.parents[2] / 'config'))
        self.addCleanup(sys.path.remove, str(SOURCE.parents[2] / 'config'))
        from coding_launch import PROVIDERS
        self.assertEqual(set(PROVIDERS.values()), {item['id'] for item in ui.client_registry()})

    def test_invalid_missing_oversized_and_nonregular_registry_refuses(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'KILIX_HOME': temp}):
            root = Path(temp); (root / 'config').mkdir()
            path = root / 'config/coding_clients.json'
            self.assertFalse(ui.is_coding(self.window(['codex'])))
            for contents in ('{', 'x' * 16385, '[' * 1100 + ']' * 1100, '{"schema":"wrong","clients":[]}'):
                path.write_text(contents)
                self.assertFalse(ui.is_coding(self.window(['codex'])))
            valid = json.loads((SOURCE.parents[2] / 'config/coding_clients.json').read_text())
            valid['clients'][0]['executable_names'] = ['../codex']
            path.write_text(json.dumps(valid))
            self.assertFalse(ui.is_coding(self.window(['codex'])))
            path.unlink(); path.symlink_to(SOURCE.parents[2] / 'config/coding_clients.json')
            self.assertFalse(ui.is_coding(self.window(['codex'])))
            path.unlink(); os.mkfifo(path)
            self.assertFalse(ui.is_coding(self.window(['codex'])))

    def test_boss_pane_and_single_pane_tab_routes_intercept_before_destruction(self):
        # Execute the actual routing methods without loading the native UI library.
        module = ast.parse((SOURCE.parent / 'boss.py').read_text())
        boss_class = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == 'Boss')
        names = {'kilix_close_persistent_window', 'close_window_with_confirmation', 'confirm_tab_close'}
        methods = [n for n in boss_class.body if isinstance(n, ast.FunctionDef) and n.name in names]
        for method in methods:
            method.decorator_list = []
        namespace = {}
        unit = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *methods], type_ignores=[])
        exec(compile(ast.fix_missing_locations(unit), 'boss-routing-fixture', 'exec'), namespace)
        window = self.window(['codex'])
        calls = []
        boss = SimpleNamespace(window_for_dispatch=window, active_window=window,
                               confirm_coding_close=lambda w: calls.append(w) or True,
                               confirm_qemu_close=lambda w: self.fail('coding pane fell through'))
        class SinglePane:
            active_window = window
            def __len__(self): return 1
        for name in names:
            namespace[name](boss, SinglePane()) if name == 'confirm_tab_close' else namespace[name](boss)
        self.assertEqual(calls, [window, window, window])

    def test_nonbroker_refuses_without_helper_or_legacy_close(self):
        window=self.window(['codex']);window.child.is_pty_brokered=False
        errors=[];boss=SimpleNamespace(show_error=lambda *args:errors.append(args))
        fast=ModuleType('kitty.fast_data_types');fast.add_timer=lambda *args:self.fail('must not schedule')
        tabs=ModuleType('kitty.tabs');tabs.SpecialWindow=object
        with patch.dict(sys.modules,{'kitty.fast_data_types':fast,'kitty.tabs':tabs}), patch.object(ui.subprocess,'run',side_effect=AssertionError('must not launch')):
            self.assertTrue(ui.show(boss,window))
        self.assertEqual(len(errors),1)

    def test_cancel_and_stale_callback_do_not_prepare_or_close(self):
        window = self.window(['codex']); calls=[]
        boss = SimpleNamespace(window_id_map={1:window}, choose=lambda *a,**k:calls.append((a,k)))
        fast=ModuleType('kitty.fast_data_types');fast.add_timer=lambda *a:self.fail('timer on Cancel')
        tabs=ModuleType('kitty.tabs');tabs.SpecialWindow=object
        with patch.dict(sys.modules, {'kitty.fast_data_types':fast,'kitty.tabs':tabs}), \
                patch.object(ui.subprocess,'run',side_effect=AssertionError('no process should launch')):
            self.assertTrue(ui.show(boss,window))
            args,kwargs=calls[0]
            self.assertEqual(kwargs['default'],'c')
            self.assertIn('l:Close and give path to rollout log',args)
            self.assertIn('b:Background to tmux',args)
            args[1]('c')
            boss.window_id_map[1]=self.window(['codex'])
            args[1]('b')
        self.assertFalse(ui._busy)

    def test_preparation_receipt_arm_close_commit_order_and_duplicate_click(self):
        for close_ok, lose_display in ((True, False), (False, False), (True, True)):
            with self.subTest(close_ok=close_ok, lose_display=lose_display), tempfile.TemporaryDirectory() as temp:
                root=Path(temp);(root/'config').mkdir();(root/'config/coding_handoff.py').write_text('fixture')
                (root/'config/coding_clients.json').write_bytes((SOURCE.parents[2]/'config/coding_clients.json').read_bytes())
                receipt=root/'receipt.json';events=[];timers=[];dialogs=[]
                window=self.window(['codex']);display=self.window(['bash']);display.id=2
                windows={1:window}
                def new_tab(special):
                    events.append('display');windows[2]=display
                    (root/'display-ready.json').write_text('{}')
                    return SimpleNamespace(active_window=display)
                def run(argv, **kwargs):
                    action=argv[2];events.append(action)
                    if action == 'commit' and not close_ok:
                        raise RuntimeError('DRAIN acknowledgment refused')
                    if action == 'release' and lose_display:
                        windows.pop(2)
                    return SimpleNamespace(stdout=json.dumps({'receipt':str(receipt)}))
                def close(w):
                    events.append('close')
                    self.assertIs(w,window)
                    return close_ok
                boss=SimpleNamespace(window_id_map=windows,choose=lambda *a,**k:dialogs.append(a),
                    _new_tab=new_tab,show_error=lambda *a:events.append('error'),mark_window_for_close=close,close_window_explicitly=lambda w:self.fail('legacy termination forbidden'))
                class Immediate:
                    def __init__(self,target,**kw):self.target=target
                    def start(self):self.target()
                fast=ModuleType('kitty.fast_data_types');fast.add_timer=lambda cb,*a:timers.append(cb)
                tabs=ModuleType('kitty.tabs');tabs.SpecialWindow=lambda *a,**k:None
                with patch.dict(sys.modules,{'kitty.fast_data_types':fast,'kitty.tabs':tabs}), \
                        patch.dict(os.environ,{'KILIX_HOME':temp}), \
                        patch.object(ui,'Thread',Immediate),patch.object(ui.shutil,'which',return_value='/bin/python3'), \
                        patch.object(ui.subprocess,'run',side_effect=run):
                    ui.show(boss,window);chosen=dialogs[0][1];chosen('b');chosen('b')
                    while timers:timers.pop(0)(0)
                expected=['prepare','display','release','commit','close'] if close_ok else ['prepare','display','release','commit','error']
                if lose_display:
                    expected=['prepare','display','release','cancel','error']
                self.assertEqual(events,expected)
                self.assertFalse(ui._busy)

if __name__ == '__main__':unittest.main()
