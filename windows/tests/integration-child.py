"""Owned ConPTY input receiver; never evaluates pasted or dropped input."""
import json
import os
from pathlib import Path
import sys
import time

root = Path(os.environ['KILIX_QUAL_RESULT'])
sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')
print('KILIX_QUAL_READY', flush=True)
received = [sys.stdin.readline().rstrip('\r\n') for _ in range(2)]
(root / 'child.json').write_text(json.dumps({'received': received, 'cwd': os.getcwd()}, ensure_ascii=False), encoding='utf-8')
print('KILIX_QUAL_RECEIVED', flush=True)
time.sleep(2)
