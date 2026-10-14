"""Focused regression for the shared keymap rebind grace barrier."""
import time
from types import SimpleNamespace

import private_input_fixture as fixture
import xinject


class SharedRebindBarrier(fixture.NativeInputFixture):
    def two_rows(self):
        first = self.xd.display.info.min_keycode
        empty = [first + i for i, row in enumerate(self.original) if not any(row)]
        self.assertGreaterEqual(len(empty), 2)
        codes = empty[:2]
        self.inj._spare = list(codes)
        return codes

    def controlled_clock(self):
        now = [time.monotonic()]
        real_time = xinject.time
        xinject.time = SimpleNamespace(monotonic=lambda: now[0], sleep=lambda _s: None)
        self.addCleanup(setattr, xinject, 'time', real_time)
        return now

    def test_one_rebind_barrier_covers_all_preceding_releases(self):
        first = self.xd.display.info.min_keycode
        empty = [first + i for i, row in enumerate(self.original) if not any(row)]
        self.assertGreaterEqual(len(empty), 2)
        codes = empty[:2]
        self.inj._spare = codes

        now = [time.monotonic()]
        real_time = xinject.time
        xinject.time = SimpleNamespace(monotonic=lambda: now[0])
        self.addCleanup(setattr, xinject, 'time', real_time)

        text = '漢界文字'
        expected = [xinject.UNICODE_KEYSYM | ord(ch) for ch in text]
        self.inj.queue_input('paste', text)
        self.inj.dispatch_pending()

        self.assertEqual(self.inj._input[0][2], 2)
        self.assertEqual(self.inj._rebind_pending[:2],
                         (self.inj.keysym_for(text[2]), codes[0]))
        self.assertEqual(self.xd.get_keyboard_mapping(codes[0], 1)[0][0], expected[0])
        self.assertEqual(self.xd.get_keyboard_mapping(codes[1], 1)[0][0], expected[1])
        self.assertEqual(self.inj._released_codes, set(codes))

        # Let the client consume the first two events under their original
        # mappings before the grace expires and the rows are rebound.
        seen = []
        self.app.sync()
        self.events(seen)
        self.assertEqual(seen, expected[:2])

        now[0] += xinject.REBIND_DELAY
        self.inj.dispatch_pending()
        self.assertFalse(self.inj._input)
        self.assertIsNone(self.inj._rebind_pending)
        self.app.sync()
        self.events(seen)
        self.assertEqual(seen, expected)
        self.assertEqual(self.held(), [])

    def test_release_during_outstanding_barrier_is_not_covered_by_it(self):
        codes = self.two_rows()
        now = self.controlled_clock()
        _c0, c1 = codes
        self.inj.queue_input('paste', '漢界文')
        self.inj.dispatch_pending()
        self.assertIsNotNone(self.inj._rebind_pending)

        # A direct release can happen after the barrier snapshot was made.
        self.assertTrue(self.inj.chord('界', 0, 1))
        self.assertTrue(self.inj.chord('界', 0, 3))
        self.assertIn(c1, self.inj._released_codes)
        now[0] += xinject.REBIND_DELAY + .001
        self.inj.dispatch_pending()

        self.assertFalse(self.inj._input)
        self.assertNotIn(c1, self.inj._rebind_safe_codes,
                         'a release after the fence must not inherit its grace')
        self.assertIn(c1, self.inj._released_codes)

    def test_release_all_revokes_barrier_cover(self):
        codes = self.two_rows()
        now = self.controlled_clock()
        _c0, c1 = codes
        self.inj.queue_input('paste', '漢界文')
        self.inj.dispatch_pending()
        self.assertIsNotNone(self.inj._rebind_pending)
        now[0] += xinject.REBIND_DELAY + .001
        self.inj.dispatch_pending()
        self.assertFalse(self.inj._input)
        self.assertIn(c1, self.inj._rebind_safe_codes)

        self.assertTrue(self.inj.chord('界', 0, 1))
        self.assertIn(c1, self.inj._keys_down)
        self.inj.release_all()
        self.assertNotIn(c1, self.inj._rebind_safe_codes)
        before = self.xd.get_keyboard_mapping(c1, 1)[0][0]
        self.inj.queue_input('paste', '字')
        self.inj.dispatch_pending()
        self.assertIsNotNone(self.inj._rebind_pending,
                             'a just-released row needs a fresh grace interval')
        self.assertEqual(self.xd.get_keyboard_mapping(c1, 1)[0][0], before)

    def test_sync_path_one_sleep_for_many_rows(self):
        self.two_rows()
        sleeps = []
        real_time = xinject.time
        xinject.time = SimpleNamespace(monotonic=time.monotonic, sleep=sleeps.append)
        self.addCleanup(setattr, xinject, 'time', real_time)

        for ch in '漢界文字':
            self.assertTrue(self.inj._paste_key(ch))
        self.assertEqual(len(sleeps), 1,
                         'the synchronous path must share one grace across released rows')


def load_tests(loader, tests, pattern):
    return fixture.x11_sandbox.sandbox_load_tests(loader, tests, __name__)
