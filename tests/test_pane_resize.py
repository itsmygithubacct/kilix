"""Exact cell sizing, nested increments, and stale/missing identity controls."""
import copy
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
import panes_stub

panes = panes_stub.real_module()


class ResizeTests(unittest.TestCase):
    def setUp(self):
        self.data = [dict(id=1, tabs=[dict(id=10, title='work', layout='splits', windows=[
            dict(id=50, pid=500, title='target', columns=16, lines=20,
                 env={'KITTY_PTY_BROKER_SESSION': 'a' * 16}),
            dict(id=60, pid=600, title='neighbor', columns=16, lines=20,
                 env={'KITTY_PTY_BROKER_SESSION': 'b' * 16})])])]
        self.calls, self.scale = [], 1
        for change in (patch.object(panes, 'snapshot', side_effect=self.snapshot),
                       patch.object(panes, '_check', side_effect=self.mutate),
                       patch.object(panes.time, 'sleep')):
            change.start()
            self.addCleanup(change.stop)

    def snapshot(self):
        return panes.parse(copy.deepcopy(self.data))

    def mutate(self, argv, label):
        self.calls.append(argv)
        self.assertEqual(argv[argv.index('--match') + 1], 'id:50')
        field = 'columns' if argv[argv.index('--axis') + 1] == 'horizontal' else 'lines'
        amount = int(argv[argv.index('--increment') + 1])
        self.data[0]['tabs'][0]['windows'][0][field] += round(amount * self.scale)
        return ''

    def test_exact_absolute_and_relative_sizes(self):
        receipt = panes.resize('pane:50', columns=21)
        self.assertEqual((receipt['before'], receipt['observed']), (16, 21))
        self.assertTrue(receipt['completion_verified'])
        receipt = panes.resize('target', delta_lines=-3)
        self.assertEqual((receipt['dimension'], receipt['observed']), ('lines', 17))
        self.assertEqual(self.data[0]['tabs'][0]['windows'][1]['columns'], 16)

    def test_nested_split_correction_is_bounded_and_measured(self):
        self.scale = .5
        receipt = panes.resize(50, delta_columns=5)
        self.assertEqual(receipt['observed'], 21)
        self.assertGreater(receipt['commands'], 1)
        self.assertLessEqual(receipt['commands'], 5)

    def test_already_exact_size_needs_no_mutation(self):
        self.assertEqual(panes.resize(50, columns=16)['commands'], 0)
        self.assertEqual(self.calls, [])

    def test_clamped_or_wrong_direction_never_retries(self):
        for scale in (0, -1):
            with self.subTest(scale=scale):
                self.calls.clear()
                self.scale = scale
                with self.assertRaises(panes.PaneError):
                    panes.resize(50, delta_columns=5)
                self.assertEqual(len(self.calls), 1)

    def test_missing_ambiguous_or_invalid_size_never_mutates(self):
        for target, kwargs in [(999, {'columns': 21}), (50, {}), (50, {'lines': 0}),
                               (50, {'delta_columns': True}), (50, {'delta_lines': -50}),
                               (50, {'columns': 21, 'lines': 23})]:
            with self.subTest(target=target, kwargs=kwargs), self.assertRaises(panes.PaneError):
                panes.resize(target, **kwargs)
        self.data[0]['tabs'][0]['windows'][1]['title'] = 'target'
        with self.assertRaises(panes.PaneError):
            panes.resize('target', columns=21)
        self.assertEqual(self.calls, [])

    def test_stale_geometry_before_dispatch_never_mutates(self):
        count = 0
        def read():
            nonlocal count
            count += 1
            if count == 2:
                self.data[0]['tabs'][0]['windows'][0]['columns'] = 18
            return self.snapshot()
        with patch.object(panes, 'snapshot', side_effect=read), self.assertRaises(panes.PaneError):
            panes.resize(50, delta_columns=5)
        self.assertEqual(self.calls, [])

    def test_changed_neighbor_process_or_title_blocks_receipt_and_correction(self):
        for change in ('pid', 'title'):
            with self.subTest(change=change):
                self.calls.clear()
                def mutate(argv, label):
                    self.mutate(argv, label)
                    if change == 'pid':
                        self.data[0]['tabs'][0]['windows'][1]['pid'] += 1
                    else:
                        self.data[0]['tabs'][0]['title'] += '-changed'
                with patch.object(panes, '_check', side_effect=mutate), self.assertRaises(panes.PaneError):
                    panes.resize(50, delta_columns=5)
                self.assertEqual(len(self.calls), 1)

    def test_cli_relative_request_emits_the_verified_receipt(self):
        remote = panes_stub.load_remote(panes_stub.ROOT, 'remote_resize_test')
        out = io.StringIO()
        with patch.object(remote, 'load_panes', return_value=panes), redirect_stdout(out):
            code = remote.main(['pane', 'resize', 'pane:50', '--delta-columns', '5', '--json'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())['observed'], 21)

    def test_group_discovery_and_help_never_load_the_live_sdk(self):
        remote = panes_stub.load_remote(panes_stub.ROOT, 'remote_discovery_test')
        with patch.object(remote, 'load_panes') as load, redirect_stdout(io.StringIO()):
            for group in ('pane', 'tab'):
                for suffix in ([], ['--help'], ['help'], ['-h']):
                    self.assertEqual(remote.main([group, *suffix]), 0)
        load.assert_not_called()


if __name__ == '__main__':
    unittest.main()
