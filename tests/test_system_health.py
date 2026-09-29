"""System speech incidents using synthetic telemetry, never host stress."""
import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'config'))
from kilix_sdk.health import ALERTS, AlertPolicy, HealthMonitor, battery, link_online


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = AlertPolicy()

    def check(self, now, expected=(), **values):
        self.assertEqual(self.policy.update(now=now, **values), [ALERTS[k] for k in expected])

    def test_temperature_boundary_recovery_and_cooldown(self):
        self.check(0, temperature=101)
        self.check(2, ['overheat'], temperature=101.1)
        self.check(10, temperature=110)
        self.check(20, temperature=99)
        self.check(400, temperature=102)
        self.check(402, temperature=98)
        self.check(404, ['overheat'], temperature=102)
        self.check(406, temperature=97)
        self.check(408, temperature=102)  # cooldown, no repeated speech

    def test_missing_or_nonfinite_temperature_does_not_rearm(self):
        self.check(0, ['overheat'], temperature=102)
        for t in (None, 'unknown', float('nan'), float('inf')):
            self.check(400, temperature=t)
        self.check(402, temperature=102)

    def test_battery_strict_thresholds_escalation_and_recharge(self):
        self.check(0, power=(10, True))
        self.check(2, ['battery_low'], power=(9, True))
        self.check(4, power=(5, True))
        self.check(6, ['battery_critical'], power=(4, True))
        self.check(400, power=(4, True))
        self.check(402, power=(12, True))
        self.check(404, ['battery_critical'], power=(4, True))
        self.check(406, power=(3, False))
        self.check(800, ['battery_low'], power=(8, True))

    def test_starting_critical_does_not_queue_low_warning(self):
        self.check(0, ['battery_critical'], power=(4, True))
        self.check(400, power=(8, True))

    def test_offline_requires_prior_link_and_sustained_loss(self):
        self.check(0, online=False)
        self.check(10, online=False)
        self.check(12, online=True)
        self.check(14, online=False)
        self.check(16, online=True)  # link handover recovers before debounce
        self.check(18, online=False)
        self.check(20, online=None)  # unknown is not sustained loss
        self.check(22, online=False)
        self.check(24, online=False)
        self.check(26, ['offline'], online=False)
        self.check(100, online=False)
        self.check(102, online=True)
        self.check(104, online=False)
        self.check(108, ['offline'], online=False)

    def memory(self, stamp, pages, available=1, pressure=30):
        return (stamp, 100, available, 100, (pages, pages), pressure)

    def test_swap_requires_sustained_traffic_low_ram_and_stalls(self):
        for i in range(7):
            self.check(i*2, ['swap'] if i == 6 else (),
                       memory=self.memory(i*2, i*4096))
        self.check(20, memory=self.memory(20, 50000))  # sample gap
        self.check(22, memory=self.memory(22, 55000))
        self.check(24, memory=self.memory(24, 60000, available=10))
        for i in range(7):
            self.check(400+i*2, ['swap'] if i == 6 else (),
                       memory=self.memory(400+i*2, 70000+i*4096))

    def test_swap_all_three_conditions_are_required(self):
        for available, pressure, step in ((10, 30, 4096), (1, 0, 4096),
                                          (1, None, 4096), (1, 30, 10)):
            self.policy = AlertPolicy()
            for i in range(10):
                self.check(i*2, memory=self.memory(i*2, i*step, available, pressure))

    def test_counter_reset_missing_or_long_gap_breaks_continuity(self):
        for i in range(5):
            self.check(i*2, memory=self.memory(i*2, i*4096))
        self.check(10, memory=self.memory(10, 0))
        self.check(12, memory=None)
        self.check(30, memory=self.memory(30, 50000))
        self.check(32, memory=self.memory(32, 55000))


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def files(self, relative, **fields):
        p = self.root/relative
        p.mkdir(parents=True, exist_ok=True)
        for name, value in fields.items():
            (p/name).write_text(str(value))
        return p

    def test_battery_capacity_energy_and_unknown(self):
        p = self.files('sys/class/power_supply/BAT0', type='Battery',
                       capacity=9, status='Discharging', energy_now=9, energy_full=100)
        self.assertEqual(battery(self.root), (9, True))
        (p/'capacity').unlink()
        self.assertEqual(battery(self.root), (9, True))
        self.files('sys/class/power_supply/BAT1', type='Battery',
                   capacity=50, status='Full', energy_now=100, energy_full=200)
        self.assertAlmostEqual(battery(self.root)[0], 109/3)
        (p/'status').write_text('Unknown')
        self.assertIsNone(battery(self.root))

    def test_physical_links_ignore_virtual_and_preserve_unknown(self):
        self.assertIsNone(link_online(self.root))
        self.files('sys/class/net/tun0', carrier=1)
        p = self.files('sys/class/net/wlan0', carrier=1)
        (p/'device').mkdir()
        self.assertTrue(link_online(self.root))
        (p/'carrier').write_text('0')
        self.assertFalse(link_online(self.root))
        (p/'carrier').unlink()
        self.assertIsNone(link_online(self.root))
        (p/'operstate').write_text('down')
        self.assertFalse(link_online(self.root))

    def test_shared_sampler_stale_data_and_failure_do_not_hide_battery(self):
        clock = Mock(return_value=100)
        client = Mock()
        system = NS(vm={'pswpin': 0, 'pswpout': 0}, pressure={},
                    memory_total=100, memory_available=50, swap_total=100)
        snapshot = NS(monotonic_ns=100*10**9, thermal=[NS(celsius=102)], system=system)
        client.snapshot.return_value = snapshot
        monitor = HealthMonitor(client=client, root=self.root, clock=clock)
        self.assertEqual(monitor.poll(), [ALERTS['overheat']])
        client.snapshot.assert_called_with(start=True, fallback=False)
        clock.return_value = 102
        self.assertEqual(monitor.poll(), [])
        client.snapshot.assert_called_with(start=False, fallback=False)
        monitor.policy = AlertPolicy()
        clock.return_value = 110
        self.assertEqual(monitor.poll(), [])  # stale hot sample
        self.files('sys/class/power_supply/BAT0', type='Battery', capacity=4, status='Discharging')
        client.snapshot.side_effect = OSError('sampler unavailable')
        self.assertEqual(monitor.poll(), [ALERTS['battery_critical']])
        monitor.close()
        client.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
