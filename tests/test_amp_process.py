"""Actual query/setup supervision across detached descendants and caller loss."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]


class OwnedSetupTests(unittest.TestCase):
    def test_success_cancel_deadline_and_owner_loss_reap_escaped_tree(self):
        for ending in ('success','cancel','deadline','owner-loss'):
            with self.subTest(ending=ending), tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary)
                marker=root/'pids'
                leaf='import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)'
                engine=('import os,subprocess,sys,time,signal\n'
                    'signal.signal(signal.SIGTERM,signal.SIG_IGN)\n'
                    f'p=subprocess.Popen([sys.executable,"-c",{leaf!r}],start_new_session=True)\n'
                    f'open({str(marker)!r},"w").write(str(os.getpid())+" "+str(p.pid))\n'
                    'time.sleep(30)\n')
                driver=root/'driver.py'
                driver.write_text('import os,sys,subprocess,time\n'
                    +f'sys.path.insert(0,{str(ROOT/"scripts")!r})\n'
                    +'from _amp_process import run_owned\n'
                    +'def action():\n'
                    +f'    subprocess.Popen([sys.executable,"-c",{engine!r}],start_new_session=True)\n'
                    +f'    while not os.path.exists({str(marker)!r}): time.sleep(.01)\n'
                    +('    return 0\n' if ending=='success' else '    time.sleep(30)\n    return 0\n')
                    +f'raise SystemExit(run_owned(action,timeout={.2 if ending=="deadline" else 5}))\n')
                command=[sys.executable,str(driver)]
                if ending=='owner-loss':
                    # A separate owning process dies after its supervised
                    # child starts. It is not this test's unrelated child.
                    owner=root/'owner.py'
                    owner.write_text('import subprocess,sys,time\n'
                        +f'p=subprocess.Popen({command!r})\n'
                        +f'open({str(root/"supervisor")!r},"w").write(str(p.pid))\n'
                        +'time.sleep(30)\n')
                    command=[sys.executable,str(owner)]
                process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
                try:
                    limit=time.monotonic()+2
                    while not marker.exists() or len(marker.read_text().split())!=2:
                        self.assertLess(time.monotonic(),limit)
                        time.sleep(.01)
                    pids=[int(word) for word in marker.read_text().split()]
                    if ending in ('cancel','owner-loss'):process.terminate()
                    out,error=process.communicate(timeout=6)
                    limit=time.monotonic()+4
                    while any(Path('/proc',str(pid)).exists() for pid in pids):
                        self.assertLess(time.monotonic(),limit,error.decode())
                        time.sleep(.01)
                    if ending=='success':self.assertEqual(process.returncode,0,error)
                    self.assertIsNone(unrelated.poll())
                finally:
                    if process.poll() is None:process.kill();process.wait()
                    process.stdout.close();process.stderr.close()
                    unrelated.kill();unrelated.wait()


if __name__=='__main__':unittest.main()
