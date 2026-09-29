"""Power transitions, local disk incidents and post-commit update notices."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'config'))
from kilix_sdk.health import ALERTS, AlertPolicy, HealthMonitor
from kilix_sdk.system_events import UpdateNotice, disk_space, external_power


class SystemEventsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def supply(self, name, **fields):
        p=self.root/'sys/class/power_supply'/name
        p.mkdir(parents=True, exist_ok=True)
        for key, value in fields.items():(p/key).write_text(str(value))
        return p

    def test_power_source_requires_system_battery_and_prefers_online(self):
        ac=self.supply('AC', type='Mains', online=1)
        self.assertIsNone(external_power(self.root))
        bat=self.supply('BAT0', type='Battery', status='Full')
        self.assertIs(external_power(self.root), True)
        (ac/'online').write_text('0')
        self.assertIs(external_power(self.root), False)
        self.supply('mouse', type='USB', online=1, scope='Device')
        self.assertIs(external_power(self.root), False)
        (ac/'online').unlink()
        (bat/'status').write_text('Discharging')
        self.assertIs(external_power(self.root), False)
        (bat/'status').write_text('Unknown')
        self.assertIsNone(external_power(self.root))

    def test_power_debounce_unknown_and_directional_cooldown(self):
        p=AlertPolicy()
        cases=[(0,True,[]),(2,False,[]),(4,True,[]),(6,False,[]),
               (8,None,[]),(10,False,[]),(14,False,['on_battery']),
               (16,True,[]),(20,True,['power_connected']),
               (22,False,[]),(26,False,[]),  # same direction inside 15 s
               (30,True,[]),(36,True,['power_connected'])]
        for now, value, expected in cases:
            self.assertEqual(p.update(now=now,external=value),[ALERTS[k] for k in expected])

    def test_disk_thresholds_unknown_recovery_and_repeat_suppression(self):
        p=AlertPolicy();gib=1024**3
        cases=[(0,[(100*gib,5*gib)],[]), (30,[(100*gib,2*gib)],[]),
               (60,[(100*gib,gib)],['disk_low']), (400,[(100*gib,gib)],[]),
               (430,[None,(100*gib,10*gib)],[]), (460,[(100*gib,gib)],[]),
               (490,[(100*gib,3*gib)],[]), (520,[(100*gib,gib)],['disk_low'])]
        for now, readings, expected in cases:
            self.assertEqual(p.update(now=now,disks=readings),[ALERTS[k] for k in expected])
        # A tiny volume is judged by percentage, a huge volume by capped bytes.
        self.assertEqual(AlertPolicy().update(now=0,disks=[(1000,49)]),[ALERTS['disk_low']])
        self.assertEqual(AlertPolicy().update(now=0,disks=[(1000,50)]),[])
        self.assertEqual(AlertPolicy().update(now=0,disks=[(10000*gib,10*gib)]),[])

    def test_disk_reader_deduplicates_local_and_never_stats_network(self):
        info=self.root/'mountinfo'
        info.write_text('1 0 8:1 / / rw - ext4 /dev/root rw\n'
                        '2 1 8:1 /bind /data rw - ext4 /dev/root rw\n'
                        '3 1 0:9 / /home rw - nfs server:/home rw\n'
                        '4 1 8:2 / /space\\040disk rw - xfs /dev/second rw\n')
        stat=Mock(return_value=NS(f_blocks=100,f_frsize=4096,f_bavail=5,f_flag=0))
        self.assertEqual(disk_space(('/', '/data/models','/home','/space disk'),mountinfo=info,statvfs=stat),[(409600,20480)]*2)
        self.assertEqual([c.args[0] for c in stat.call_args_list],['/','/space disk'])
        stat.side_effect=OSError('unavailable')
        self.assertEqual(disk_space(('/',),mountinfo=info,statvfs=stat),[None])
        stat.side_effect=None;stat.return_value.f_flag=os.ST_RDONLY
        self.assertIsNone(disk_space(('/',),mountinfo=info,statvfs=stat))

    def test_notice_new_events_only_and_invalid_content_never_speaks(self):
        path=self.root/'notice';path.write_text('a'*32+'\n')
        notice=UpdateNotice(path)
        self.assertFalse(notice.poll())
        path.write_text('b'*32+'\n')
        self.assertTrue(notice.poll());self.assertFalse(notice.poll())
        path.write_text('arbitrary speech is not allowed')
        self.assertFalse(notice.poll())
        path.unlink();self.assertFalse(notice.poll())
        path.write_text('b'*32);self.assertFalse(notice.poll())
        path.write_text('c'*32);self.assertTrue(notice.poll())
        self.assertFalse(UpdateNotice(path).poll())  # Restart consumed its baseline.

    def test_notice_does_not_follow_symlinks_or_block_on_fifo(self):
        path=self.root/'notice';target=self.root/'target'
        target.write_text('e'*32);path.symlink_to(target)
        self.assertIsNone(UpdateNotice(path).seen)
        path.unlink();os.mkfifo(path)
        self.assertIsNone(UpdateNotice(path).seen)

    def test_monitor_wires_new_sources_and_limits_disk_sampling(self):
        client=Mock();client.snapshot.return_value=None
        clock=Mock(return_value=0);disk=Mock(return_value=[(1000,1)])
        notice=self.root/'notice';ac=self.supply('AC',type='Mains',online=1)
        self.supply('BAT0',type='Battery',status='Full',capacity=100)
        monitor=HealthMonitor(client=client,root=self.root,clock=clock,
                              disk_reader=disk,update_path=notice)
        self.assertEqual(monitor.poll(),[ALERTS['disk_low']])
        (ac/'online').write_text('0');clock.return_value=2
        self.assertEqual(monitor.poll(),[])
        notice.write_text('d'*32);clock.return_value=6
        self.assertEqual(monitor.poll(),[ALERTS['on_battery'],ALERTS['update_complete']])
        clock.return_value=8;self.assertEqual(monitor.poll(),[])
        disk.assert_called_once()
        clock.return_value=30;monitor.poll();self.assertEqual(disk.call_count,2)
        monitor.close();client.close.assert_called_once()


if __name__ == '__main__':unittest.main()
