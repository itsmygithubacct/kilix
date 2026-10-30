"""Cancellation and GLib source lifetime without a reader or D-Bus service."""
import signal
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
import fingerlock_core as core


class FakeGLib:
    PRIORITY_DEFAULT = 0

    def __init__(self, event):
        self.event = event
        self.sources = {}
        self.next_id = 0
        self.signal_calls = []

    def MainLoop(self):
        return self

    def timeout_add(self, interval, callback):
        self.next_id += 1
        self.sources[self.next_id] = callback
        return self.next_id

    def unix_signal_add(self, priority, signum, callback):
        self.signal_calls.append(signum)
        return self.timeout_add(0, callback)

    def run(self):
        identifier = {"timeout": 1, "finish": 2, "interrupt": 3}[self.event]
        if not self.sources[identifier]():
            del self.sources[identifier]

    def quit(self):
        pass

    def source_remove(self, identifier):
        # Removing a source twice is the GLib warning this regression checks.
        del self.sources[identifier]


class WaitTests(unittest.TestCase):
    def test_success_timeout_and_interrupt_remove_each_source_once(self):
        for event in ("finish", "timeout", "interrupt"):
            with self.subTest(event=event):
                bus = core.SystemBus.__new__(core.SystemBus)
                bus._glib = FakeGLib(event)
                reads = []
                def predicate():
                    reads.append(1)
                    return len(reads) > 1
                if event == "interrupt":
                    with self.assertRaises(KeyboardInterrupt):
                        bus.run_until(predicate, 2)
                else:
                    self.assertEqual(bus.run_until(predicate, 2), event == "finish")
                self.assertEqual(bus._glib.sources, {})
                self.assertEqual(bus._glib.signal_calls, [signal.SIGINT])

    def test_worker_wait_does_not_own_sigint(self):
        bus = core.SystemBus.__new__(core.SystemBus)
        bus._glib = FakeGLib("timeout")
        result = []
        thread = threading.Thread(target=lambda: result.append(bus.run_until(lambda: False, 2)))
        thread.start()
        thread.join(2)
        self.assertEqual(result, [False])
        self.assertEqual(bus._glib.signal_calls, [])
        self.assertEqual(bus._glib.sources, {})


if __name__ == "__main__":
    unittest.main()
