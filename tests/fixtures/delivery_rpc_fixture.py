"""Inert terminal-client subprocess for Client.run deadline tests.

The fixture records only a payload length and digest. ``stage`` models the
remote send-text side effect, ``observe`` models a response-bearing screen
read, and ``submit`` models the separately authorized Enter request.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import time


state_path = Path(os.environ["DELIVERY_RPC_FIXTURE_STATE"])
state = json.loads(state_path.read_text()) if state_path.exists() else {"pids": []}
state["pids"].append(os.getpid())
state_path.write_text(json.dumps(state, sort_keys=True))
operation = sys.argv[1]

if operation == "stage":
    payload = sys.stdin.buffer.read()
    state.update(phase="pasting", payload_bytes=len(payload),
                 payload_sha256=hashlib.sha256(payload).hexdigest())
elif operation == "observe":
    time.sleep(float(sys.argv[2]))
    sys.stdout.write("composer verified\n")
elif operation == "submit":
    payload = sys.stdin.buffer.read()
    if payload != b"\r" or state.get("phase") != "pasting":
        raise SystemExit("unexpected fixture submission")
    state["phase"] = "submitted"
else:
    raise SystemExit("unknown fixture operation")

state_path.write_text(json.dumps(state, sort_keys=True))
