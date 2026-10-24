"""Owned PTY user ingress plus real Linux sandbox/broker integration; no model."""
import fcntl
import json
import os
from pathlib import Path
import pty as terminal_pty
import select
import subprocess
import socket
import signal
import threading
import sys
import termios
import time
import unittest
from unittest import mock

import test_pty_selector as fixtures
import kilix_pty_supervisor as supervisor

ROOT = Path(__file__).resolve().parents[1]


class SupervisorTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.SelectorEnds.setUpClass.__func__)
    setUp = fixtures.SelectorEnds.setUp
    tearDown = fixtures.SelectorEnds.tearDown

    def launch(self, actor, selector, *, lifetime=5, confirm=True, after_preview=None, revoke=False, revoke_after_request=False, via_launcher=False, read_only=False, model_channel=None, model_deadline=None, owned_panes=(), launch_env=None):
        script = self.fx.root / "actor.py"
        script.write_text(actor)
        master, slave = terminal_pty.openpty()
        def user_terminal():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        command = [sys.executable, str(ROOT / "config/kilix_pty_supervisor.py"),
                   "--broker", str(fixtures.BROKER), "--runtime", str(self.fx.runtime),
                   "--state", str(self.state), "--actor", str(script), "--lifetime", str(lifetime)]
        needle = os.environ.get("SELECTOR_NEEDLE_SOURCE")
        if needle:
            command += ["--needle-source", needle]
        if read_only:
            command.append("--read-only")
        env = dict(self.fx.env)
        if via_launcher:
            command = ["bash", str(ROOT / "kilix"), "pty", "supervise", "--actor", str(script),
                       "--lifetime", str(lifetime)]
            if needle:
                command += ["--needle-source", needle]
            if read_only:
                command.append("--read-only")
            env = {"PATH": os.environ["PATH"], "HOME": str(self.fx.root / "home"), "TMPDIR": "/tmp",
                   "KILIX_STORAGE_HOME": str(self.fx.root / "storage"),
                   "KITTY_PTY_BROKER_EXECUTABLE": str(fixtures.BROKER),
                   "KITTY_PTY_BROKER_RUNTIME": str(self.fx.runtime),
                   "KITTY_PTY_BROKER_SESSION": "caller_unrelated",
                   "XDG_RUNTIME_DIR": str(self.fx.root / "xdg")}
        env.update(launch_env or {})
        for pane_id in owned_panes:
            command += ['--owned-pane', str(pane_id)]
        if model_channel is not None:
            command += ["--profile", "model180", "--model-channel", str(model_channel),
                        "--model-deadline", str(model_deadline if model_deadline is not None else time.monotonic()+180)]
        process = subprocess.Popen(command, stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   preexec_fn=user_terminal, env=env)
        os.close(slave)
        seen = bytearray()
        def until(marker):
            deadline = time.monotonic() + 10
            while marker not in seen:
                if time.monotonic() >= deadline:
                    self.fail("missing trusted prompt " + repr(marker) + ": " + repr(seen))
                if select.select([master], [], [], .1)[0]:
                    try:
                        seen.extend(os.read(master, 8192))
                    except OSError:
                        out, err = process.communicate(timeout=3)
                        self.fail(f"supervisor closed UI: {out!r} {err!r}")
        try:
            if not read_only:
                until(b"Selector JSON")
                os.write(master, json.dumps(selector).encode() + b"\n")
            if not read_only and confirm is not None:
                until(b"Type END")
                if after_preview:
                    after_preview()
                os.write(master, b"END\n" if confirm else b"CANCEL\n")
            if revoke:
                until(b"ACTOR_RUNNING")
                if revoke_after_request:
                    until(b"REQUEST_COMPLETE")
                os.write(master, b"REVOKE\n")
            out, err = process.communicate(timeout=12)
            self.assertTrue(out, err.decode(errors="replace"))
            result = json.loads(out)
            self.assertNotIn("error", result, result)
            if "cleanup_observations" in result:
                self.assertTrue(result["cleanup_observations"], result)
                self.assertTrue(all(row["absent_or_exited"] for row in result["cleanup_observations"]), result)
            return result, process.returncode
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=3)
            os.close(master)

    def test_isolated_selector_positive_replay_and_access_refusals(self):
        self.fx.start("selected")
        self.fx.start("bystander")
        canary = self.fx.root / "outside-canary"
        canary.write_text("owned fixture only")
        actor = '''import json, os, socket, sys
sys.path.insert(0, '/tools')
from kilix_pty_agent_client import call
selection = json.load(open('/run/selection.json'))
assert not os.path.exists(CANARY)
assert not os.path.exists('/proc/1/root' + CANARY)
assert not os.path.exists(RUNTIME)
assert not os.path.exists(SOURCE)
assert not os.path.exists('/home')
assert not any(name.startswith('KILIX_') for name in os.environ)
try:
    os.open('/dev/tty', os.O_RDWR)
except OSError:
    pass
else:
    raise AssertionError('actor inherited user terminal')
status = open('/proc/self/status').read()
assert 'NoNewPrivs:\\t1' in status
assert 'CapEff:\\t0000000000000000' in status
assert os.readlink('/proc/self/ns/pid') != PARENT_PIDNS
assert os.readlink('/proc/self/ns/net') != PARENT_NETNS
assert os.readlink('/proc/self/ns/user') != PARENT_USERNS
assert os.readlink('/proc/self/ns/mnt') != PARENT_MNTNS
bad = call({'route':'select','request':selection['request']})
assert bad['code'] == 3
first = call({'route':'selector','request':selection['request']})
assert first['code'] == 0, first
assert first['receipt']['result'] == 'verified_absent'
second = call({'route':'selector','request':selection['request']})
assert second['receipt']['duplicate'] is True
print(json.dumps({'isolation_checked':True,'positive':True,'replay':True}))
'''
        values = {"CANARY": str(canary), "RUNTIME": str(self.fx.runtime),
                  "SOURCE": str(ROOT / "config/kilix_pty_controller.py"),
                  "PARENT_PIDNS": os.readlink('/proc/self/ns/pid'),
                  "PARENT_NETNS": os.readlink('/proc/self/ns/net'),
                  "PARENT_USERNS": os.readlink('/proc/self/ns/user'),
                  "PARENT_MNTNS": os.readlink('/proc/self/ns/mnt')}
        actor = '\n'.join(f'{key} = {value!r}' for key, value in values.items()) + '\n' + actor
        result, code = self.launch(actor, {"id": "selected"})
        self.assertEqual(code, 0, result)
        self.assertEqual(len(result["calls"]), 3)
        self.assertIn('"positive": true', result["actor_output"])
        self.assertTrue(self.fx.alive("bystander"))

    def test_raw_facade_preserves_original_prefix_qualifier_and_positive(self):
        self.fx.start("pair.attached")
        self.fx.start("pair.detached")
        self.fx.attach("pair.attached")
        actor = '''import json, subprocess
s = json.load(open('/run/selection.json'))
def raw(*args):
    return subprocess.run(['kitty-pty-broker', *args], capture_output=True, text=True)
assert raw('list','--json').returncode == 0
assert raw('status',s['issued']['resolved']['id'],'--json').returncode == 0
first = raw('kill',s['issued']['resolved']['id'])
assert first.returncode == 0, first.stdout + first.stderr
receipt = json.loads(first.stdout)
assert receipt['selector'] == {'id_prefix':'pair.','attached':False}
second = raw('kill',s['issued']['resolved']['id'])
assert json.loads(second.stdout)['duplicate'] is True
print('raw facade positive and replay')
'''
        result, code = self.launch(actor, {"id_prefix": "pair.", "attached": False})
        self.assertEqual(code, 0, result)
        self.assertTrue(self.fx.alive("pair.attached"))
        self.assertFalse(self.fx.alive("pair.detached"))

    def test_needle_adapter_positive(self):
        if not os.environ.get("SELECTOR_NEEDLE_SOURCE"):
            self.skipTest("SELECTOR_NEEDLE_SOURCE must identify exact owned Needle source")
        self.fx.start("selected")
        actor = '''import json, subprocess
s = json.load(open('/run/selection.json'))['request']
action = {'operation':'end','selector':s['selector'],'operation_id':s['operation_id']}
p = subprocess.run(['kilix-needle','pty','--request-json',json.dumps(action)],capture_output=True,text=True)
assert p.returncode == 0, p.stdout + p.stderr
assert json.loads(p.stdout)['result']['result'] == 'verified_absent'
print('Needle isolated positive')
'''
        result, code = self.launch(actor, {"id": "selected"}, via_launcher=True)
        self.assertEqual(code, 0, result)
        self.assertFalse(self.fx.alive("selected"))

    def test_model_profile_fake_provider_action_stays_isolated_and_expires(self):
        self.fx.start("bystander")
        for expired in (False, True):
            with self.subTest(expired=expired):
                target = "delayed" if expired else "prompt"
                self.fx.start(target)
                directory = self.fx.root / target
                directory.mkdir(mode=0o700)
                channel = directory / "model.sock"
                credential = directory / "credential"
                credential.write_text("owned fake provider canary; never sent")
                errors, requests = [], []
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                    listener.bind(str(channel))
                    channel.chmod(0o600)
                    listener.listen(1)
                    listener.settimeout(8)
                    def provider():
                        try:
                            for sequence in range(2):
                                with listener.accept()[0] as connection:
                                    connection.settimeout(4)
                                    with connection.makefile('rb') as stream:
                                        message = json.loads(stream.readline(262145))
                                    requests.append(message)
                                    assert message['sequence'] == sequence
                                    assert message['schema'] == 'kilix.measured.model-request/v1'
                                    if expired and sequence == 0:
                                        time.sleep(.35)
                                    payload = {'argv':['/usr/bin/python3','-c',
                                        "import json,sys; sys.path.insert(0,'/tools'); "
                                        "from kilix_pty_agent_client import RemoteController; "
                                        "s=json.load(open('/run/selection.json'))['request']; "
                                        "print(json.dumps(RemoteController().dispatch(s)))"]} if sequence == 0 else 'done'
                                    reply = {'schema':'kilix.measured.model-response/v1','sequence':sequence,
                                             'kind':'action' if sequence == 0 else 'final','payload':payload}
                                    connection.sendall(json.dumps(reply).encode()+b'\n')
                        except Exception as error:
                            errors.append(repr(error))
                    thread = threading.Thread(target=provider, daemon=True)
                    thread.start()
                    actor = r'''import json,os,resource,socket,subprocess
assert not os.path.exists(CREDENTIAL)
assert not os.path.exists('/proc/1/root'+CREDENTIAL)
assert resource.getrlimit(resource.RLIMIT_AS) == (536870912,536870912)
assert resource.getrlimit(resource.RLIMIT_CPU) == (30,30)
results = []
for sequence in range(2):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect('/run/model.sock')
        connection.sendall(json.dumps({'schema':'kilix.measured.model-request/v1','sequence':sequence,'tool_results':results}).encode()+b'\n')
        with connection.makefile('rb') as stream:
            response = json.loads(stream.readline(262145))
    if response['kind'] == 'action':
        p = subprocess.run(response['payload']['argv'],capture_output=True,text=True)
        assert p.returncode == 0, p.stderr
        code, receipt = json.loads(p.stdout)
        assert receipt['reason'] == 'authority_expired' if EXPIRED else receipt['result'] == 'verified_absent', receipt
        results = [{'code':code,'receipt':receipt}]
    else:
        assert response['kind'] == 'final'
print(json.dumps(results))
'''
                    try:
                        result, code = self.launch(f"EXPIRED={expired!r}\nCREDENTIAL={str(credential)!r}\n"+actor,
                                                   {'id':target}, lifetime=.15 if expired else 5,
                                                   model_channel=channel, via_launcher=True)
                    finally:
                        thread.join(timeout=9)
                    self.assertFalse(thread.is_alive())
                    self.assertFalse(errors, errors)
                    self.assertEqual(len(requests), 2)
                    self.assertEqual(result['actor_wall_seconds'], 185)
                    self.assertEqual(result['profile'], 'model180')
                    self.assertEqual(code, 3 if expired else 0, result)
                    self.assertEqual(self.fx.alive(target), expired)
                    self.assertTrue(self.fx.alive('bystander'))

    def test_model_work_deadline_interrupts_pending_tool_and_cleans_actor(self):
        self.fx.start('selected')
        directory = self.fx.root / 'deadline-provider'
        directory.mkdir(mode=0o700)
        endpoint = directory / 'model.sock'
        actor = directory / 'actor.py'
        actor.write_text("import sys; sys.path.insert(0,'/tools'); from kilix_pty_agent_client import call; "
                         "call({'route':'broker','argv':['list','--json']})")
        controller = supervisor.authority.Controller(self.broker, str(self.state))
        request = fixtures.SelectorEnds.request(self, {'id':'selected'})
        issued = controller.select(request)
        self.assertTrue(issued['issued'])
        master, slave = terminal_pty.openpty()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(endpoint))
                endpoint.chmod(0o600)
                deadline = time.monotonic() + .5
                with mock.patch.object(supervisor, 'dispatch', side_effect=lambda *a: time.sleep(10)) as pending:
                    report = supervisor.run_actor(controller,self.broker,request,issued,actor,slave,30,
                                                  profile='model180',model_channel=endpoint,model_deadline=deadline)
                self.assertTrue(pending.called)
                self.assertEqual(report['stop'], 'model_work_deadline')
                self.assertEqual(report['operation_outcome'], 'uncertain')
                self.assertTrue(report['interrupted_call'])
                self.assertEqual(report['work_deadline_monotonic'], deadline)
                self.assertLessEqual(report['cleanup_finished_monotonic'], deadline+5)
                self.assertTrue(all(o['absent_or_exited'] for o in report['cleanup_observations']))
                self.assertTrue(self.fx.alive('selected'))
                self.assertEqual(controller.dispatch(request)[1]['reason'], 'authority_required')
        finally:
            os.close(master)
            os.close(slave)

    def test_model_endpoint_rejects_untrusted_layout(self):
        directory = self.fx.root / 'provider'
        directory.mkdir(mode=0o700)
        endpoint = directory / 'model.sock'
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(endpoint))
            endpoint.chmod(0o600)
            self.assertEqual(supervisor.model_endpoint(endpoint), endpoint)
            endpoint.chmod(0o666)
            with self.assertRaises(ValueError):
                supervisor.model_endpoint(endpoint)
            endpoint.chmod(0o600)
            alias = directory / 'alias'
            alias.symlink_to(endpoint)
            with self.assertRaises(ValueError):
                supervisor.model_endpoint(alias)
            directory.chmod(0o755)
            with self.assertRaises(ValueError):
                supervisor.model_endpoint(endpoint)

    def test_actual_host_selector_and_request_cli(self):
        self.fx.start("bystander")
        for mode in ("selector", "request"):
            with self.subTest(mode=mode):
                self.fx.start(mode)
                actor = r'''import json, subprocess
s = json.load(open('/run/selection.json'))['request']
if MODE == 'selector':
    argv = ['kilix','pty','end','--selector-json',json.dumps(s['selector']),
            '--operation-id',s['operation_id'],'--yes','--json']
    p = subprocess.run(argv,capture_output=True,text=True)
else:
    p = subprocess.run(['kilix','pty','request','--request-json','-'],input=json.dumps(s),capture_output=True,text=True)
assert p.returncode == 0, p.stdout+p.stderr
assert json.loads(p.stdout)['result'] == 'verified_absent'
print('actual host CLI positive')
'''
                result, code = self.launch(f"MODE={mode!r}\n"+actor, {"id":mode})
                self.assertEqual(code, 0, result)
                self.assertFalse(self.fx.alive(mode))
                self.assertTrue(self.fx.alive("bystander"))

    def test_actual_needle_grammar_and_stdio_mcp(self):
        if not os.environ.get("SELECTOR_NEEDLE_SOURCE"):
            self.skipTest("SELECTOR_NEEDLE_SOURCE required")
        self.fx.start("bystander")
        for mode in ("grammar", "mcp", "mcp_pair", "help", "guidance"):
            with self.subTest(mode=mode):
                self.fx.start(mode)
                actor = r'''import json, subprocess
s = json.load(open('/run/selection.json'))['request']
if MODE in ('help', 'guidance'):
    if MODE == 'help':
        help = subprocess.run(['kilix-needle','pty','--help'],capture_output=True,text=True)
        assert help.returncode == 0 and 'selector' in help.stdout, help
    else:
        guidance = open('/tools/PTY-GUIDANCE.txt').read()
        assert 'Never convert a reference to a chosen exact ID.' in guidance
if MODE in ('grammar', 'help', 'guidance'):
    read = subprocess.run(['kilix-needle','pty','list sessions'],capture_output=True,text=True)
    assert read.returncode == 0, read.stdout + read.stderr
    text = 'end selector ' + json.dumps(s['selector']) + ' operation ' + s['operation_id']
    p = subprocess.run(['kilix-needle','pty',text],capture_output=True,text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert json.loads(p.stdout)['result']['result'] == 'verified_absent'
else:
    if MODE == 'mcp_pair':
        init = {'jsonrpc':'2.0','id':1,'method':'initialize'}
        listing = {'jsonrpc':'2.0','id':2,'method':'tools/list'}
        p = subprocess.run(['kilix-needle','mcp','--tools','all'],input=json.dumps(init)+'\n'+json.dumps(listing)+'\n',capture_output=True,text=True)
        assert p.returncode == 0, p.stderr
        menu = [json.loads(line) for line in p.stdout.splitlines()]
        tools = menu[1]['result']['tools']
        assert len(tools) > 3 and not any(t['name'].startswith('kilix_pty_') for t in tools), menu
        attempts = [{'jsonrpc':'2.0','id':i+1,'method':'tools/call','params':{'name':t['name'],'arguments':{}}} for i,t in enumerate(tools)]
        denied = subprocess.run(['kilix-needle','mcp','--tools','all'],input=''.join(json.dumps(m)+'\n' for m in attempts),capture_output=True,text=True)
        assert denied.returncode == 0, denied.stderr
        rows = [json.loads(line) for line in denied.stdout.splitlines()]
        assert len(rows) == len(tools) and all(r['result']['structuredContent']['refused'] == 'capability_unavailable' for r in rows), rows
        print(json.dumps({'discovery_exchanges': menu, 'refusal_exchanges': rows}))
    action = {'operation':'end','selector':s['selector'],'operation_id':s['operation_id']}
    messages = [
      {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18'}},
      {'jsonrpc':'2.0','method':'notifications/initialized'},
      {'jsonrpc':'2.0','id':2,'method':'tools/list'},
      {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'kilix_pty_read','arguments':{'request':'list sessions'}}},
      {'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'kilix_pty_act','arguments':{'request':action}}},
      {'jsonrpc':'2.0','id':5,'method':'tools/call','params':{'name':'kilix_action_act','arguments':{}}}]
    p = subprocess.run(['kilix-needle','mcp','--tools','pty'],input=''.join(json.dumps(m)+'\n' for m in messages),capture_output=True,text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    rows = [json.loads(line) for line in p.stdout.splitlines()]
    assert [r['id'] for r in rows] == [1,2,3,4,5], rows
    assert len(rows[1]['result']['tools']) == 3, rows
    assert rows[2]['result']['structuredContent']['status'] == 0, rows
    assert rows[3]['result']['structuredContent']['result']['result'] == 'verified_absent', rows
    assert rows[4]['error']['code'] == -32602, rows
    if MODE == 'mcp_pair':
        print(json.dumps({'pty_exchanges': rows}))
print('actual grammar/MCP positive')
'''
                result, code = self.launch(f"MODE={mode!r}\n" + actor, {"id": mode})
                self.assertEqual(code, 0, result)
                self.assertFalse(self.fx.alive(mode))
                self.assertTrue(self.fx.alive("bystander"))

    def test_actual_live_and_archived_reads_help_and_skill(self):
        if not os.environ.get('SELECTOR_NEEDLE_SOURCE'):
            self.skipTest('SELECTOR_NEEDLE_SOURCE required')
        self.fx.start('bystander')
        self.fx.start('read-live', [sys.executable, '-c',
                                   "import time; print('owned-first-line\\nowned-final-line', flush=True); time.sleep(60)"])
        actor = r'''import json, subprocess
def run(argv, input=None):
    p = subprocess.run(argv,input=input,capture_output=True,text=True)
    assert p.returncode == 0, (argv,p.stdout,p.stderr)
    return p.stdout
assert 'read-live' in run(['kilix','pty','list'])
assert 'read-live' in run(['kilix','pty','status','read-live'])
help = run(['kilix','pty','help'])
assert 'observe ID --once' in help and 'journals show ID' in help
skill = open('/tools/kilix-pty/SKILL.md').read()
assert 'observe ID --once --text --lines 50 --json' in skill
assert open('/tools/kilix-pty/references/receipts.md').read()
plain = json.loads(run(['kilix','pty','observe','read-live','--once','--text','--lines','50','--json']))
assert plain['text'].splitlines()[-1] == 'owned-final-line', plain
request = {'schema':'kilix.pty.request/v1','verb':'observe','args':{'id':'read-live','text':True,'max_lines':50}}
typed = run(['kilix','pty','request','--request-json','-'],json.dumps(request))
assert 'owned-final-line' in typed, typed
grammar = run(['kilix-needle','pty','read last 50 lines from session "read-live"'])
assert 'owned-final-line' in grammar, grammar
msg = {'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'kilix_pty_read','arguments':{'request':{'operation':'observe','id':'read-live','max_lines':50}}}}
reply = json.loads(run(['kilix-needle','mcp','--tools','pty'],json.dumps(msg)+'\n'))
assert reply['result']['structuredContent']['status'] == 0, reply
assert 'owned-final-line' in json.dumps(reply), reply
print(json.dumps({'live_last_line':plain['text'].splitlines()[-1], 'help_used':True,'skill_used':True}))
'''
        result, code = self.launch(actor, None, read_only=True)
        self.assertEqual(code, 0, result)
        self.assertEqual(len(result['runtime_asset_sha256']), 6)
        self.assertTrue(self.fx.alive('bystander'))

        # Actual output is archived by the real reaper, never fabricated files.
        handles = [os.pidfd_open(self.fx.started['read-live'][key]) for key in ('broker_pid','child_pid')]
        try:
            for handle in handles:
                try:
                    signal.pidfd_send_signal(handle, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        finally:
            for handle in handles:
                os.close(handle)
        time.sleep(.05)
        env = {'PATH':os.environ['PATH'], 'TMPDIR':'/tmp', 'HOME':str(self.fx.root/'home'),
               'KILIX_STORAGE_HOME':str(self.fx.root), 'KILIX_STATE_DIRECTORY':str(self.state),
               'KITTY_PTY_BROKER_SESSION':'caller_unrelated',
               'KITTY_PTY_BROKER_EXECUTABLE':str(fixtures.BROKER),
               'KITTY_PTY_BROKER_RUNTIME':str(self.fx.runtime), 'KILIX_PTY_ZSTD_TIMEOUT':'5'}
        reaped = subprocess.run(['bash',str(ROOT/'kilix'),'pty','reap','--runtime',str(self.fx.runtime)],
                                env=env,capture_output=True,text=True,timeout=12)
        self.assertEqual(reaped.returncode,0,reaped.stdout+reaped.stderr)
        self.assertTrue(list((self.state/'pty-journals').glob('read-live.*.journal.zst')))
        archived = r'''import json, subprocess
commands = [
 ['kitty-pty-broker','journals','show','read-live','--text','--lines','50','--json'],
 ['kilix','pty','journals','show','read-live','--text','--lines','50','--json'],
 ['kilix-needle','pty','read journal "read-live"']]
for argv in commands:
    p = subprocess.run(argv,capture_output=True,text=True)
    assert p.returncode == 0 and 'owned-final-line' in p.stdout, (argv,p.stdout,p.stderr)
    print(json.dumps({'argv':argv,'reply':json.loads(p.stdout)}))
for argv in (['kilix','pty','journals','--json'],['kitty-pty-broker','journals','--json']):
    p = subprocess.run(argv,capture_output=True,text=True)
    assert p.returncode == 0 and 'read-live' in p.stdout, (argv,p.stdout,p.stderr)
'''
        result, code = self.launch(archived, None, read_only=True)
        self.assertEqual(code,0,result)
        self.assertTrue(self.fx.alive('bystander'))

    def test_launcher_preserves_trusted_pane_credential_path_without_actor_access(self):
        # Refusal-only transport fixture, NOT an actual T4 positive mapping.
        password = self.fx.root / 'fixture-pane-password'
        password.write_text('owned inert credential canary')
        password.chmod(0o600)
        client = self.fx.root / 'fixture-kitten'
        client.write_text('#!/usr/bin/python3\nimport sys\nassert sys.argv[1:] == '+
                          repr(['@','--password-file',str(password),'ls'])+'\nprint("[]")\n')
        client.chmod(0o700)
        actor = '''import json,os,sys
sys.path.insert(0,'/tools')
from kilix_pty_agent_client import call
assert not os.path.exists(PASSWORD)
reply = call({'route':'read','request':{'schema':'kilix.pty.request/v1','verb':'pane','args':{'pane_id':7}}})
assert reply['code'] == 4 and reply['receipt']['reason'] == 'pane_not_found', reply
print(json.dumps(reply))
'''
        result, code = self.launch('PASSWORD='+repr(str(password))+'\n'+actor,None,read_only=True,
                                  via_launcher=True,owned_panes=(7,),
                                  launch_env={'KILIX_KITTEN':str(client),'KILIX_RC_PASSWORD_FILE':str(password)})
        self.assertEqual(code,0,result)

    def test_pane_timeout_serialization_reaches_native_status(self):
        # Transport regression only: mocked mapping is NOT actual T4 coverage.
        import kilix_pty_pane_lookup
        self.fx.start('read-status')
        lookup = kilix_pty_pane_lookup.PaneLookup.__new__(kilix_pty_pane_lookup.PaneLookup)
        lookup.owned = frozenset({7})
        for timeout in ('.3','5'):
            self.broker.timeout = timeout
            with self.subTest(timeout=timeout), mock.patch.object(lookup,'snapshot',return_value='read-status'):
                code, document = lookup.lookup(7,self.broker)
                self.assertEqual(code,0,document)
                self.assertEqual(document['started_millis'],self.fx.started['read-status']['started_millis'])
                self.assertEqual(self.broker.timeout,timeout)
        self.assertTrue(self.fx.alive('read-status'))

    def test_ambiguous_declined_and_changed_preview_do_not_launch_actor(self):
        self.fx.start("pair.one")
        self.fx.start("pair.two")
        result, code = self.launch("raise AssertionError('must not run')", {"id_prefix": "pair."}, confirm=None)
        self.assertFalse(result["issued"])
        self.assertNotIn("actor_exit", result)
        self.assertEqual(code, 3)
        result, _ = self.launch("raise AssertionError('must not run')", {"id": "pair.one"}, confirm=False)
        self.assertEqual(result["selection"]["reason"], "declined")
        result, _ = self.launch("raise AssertionError('must not run')", {"id": "pair.one"},
                                after_preview=lambda: self.fx.attach("pair.one"))
        self.assertEqual(result["selection"]["reason"], "selection_changed")
        self.assertTrue(self.fx.alive("pair.one"))
        self.assertTrue(self.fx.alive("pair.two"))

    def test_mismatched_expired_and_stale_raw_authority_refuse(self):
        self.fx.start("selected")
        for mode in ("mismatch", "expire", "stale_raw"):
            with self.subTest(mode=mode):
                actor = '''import json, sys, time
sys.path.insert(0, '/tools')
from kilix_pty_agent_client import call
s = json.load(open('/run/selection.json'))
if MODE == 'mismatch':
    s['request']['selector'] = {'id':'unselected'}
    reply = call({'route':'selector','request':s['request']})
elif MODE == 'expire':
    time.sleep(.3)
    reply = call({'route':'selector','request':s['request']})
else:
    reply = call({'route':'broker','argv':['kill','selected','--expect-started','1']})
assert reply['code'] == 3, reply
print(json.dumps(reply))
'''
                result, code = self.launch(f'MODE = {mode!r}\n' + actor, {"id": "selected"},
                                           lifetime=.1 if mode == "expire" else 5)
                self.assertEqual(code, 3, result)
                self.assertEqual(result["calls"][0]["code"], 3)
                self.assertTrue(self.fx.alive("selected"))

    def test_user_revocation_kills_owned_actor_without_dispatch(self):
        self.fx.start("selected")
        actor = '''import subprocess, time, sys
sys.path.insert(0, '/tools')
from kilix_pty_agent_client import call
subprocess.Popen(['/bin/sleep','60'])
assert call({'route':'broker','argv':['list','--json']})['code'] == 0
time.sleep(60)
'''
        result, code = self.launch(actor, {"id": "selected"}, revoke=True, revoke_after_request=True)
        self.assertEqual((code, result["stop"], len(result["calls"])), (3, "user_revoked", 1))
        self.assertGreaterEqual(len(result["cleanup_observations"]), 3)
        self.assertTrue(self.fx.alive("selected"))

    def test_actor_output_cannot_claim_completion_without_dispatch(self):
        self.fx.start("selected")
        result, code = self.launch("print('verified_absent')", {"id": "selected"})
        self.assertEqual((code, result["actor_exit"], result["operation_outcome"]), (3, 0, "not_requested"))
        self.assertEqual(result["calls"], [])
        self.assertTrue(self.fx.alive("selected"))

    def test_read_only_actor_can_inspect_but_cannot_end(self):
        self.fx.start("selected")
        actor = """import json, subprocess
p = subprocess.run(['kitty-pty-broker','status','selected','--json'],capture_output=True,text=True)
assert p.returncode == 0, p.stdout + p.stderr
p = subprocess.run(['kitty-pty-broker','kill','selected'],capture_output=True,text=True)
assert p.returncode == 3
assert json.loads(p.stdout)['reason'] == 'authority_required'
"""
        result, code = self.launch(actor, None, read_only=True, via_launcher=True)
        self.assertEqual(code, 0, result)
        self.assertFalse(result["issued"])
        self.assertTrue(self.fx.alive("selected"))

    def test_nonterminal_launch_refuses_without_actor(self):
        result = subprocess.run([sys.executable, str(ROOT / 'config/kilix_pty_supervisor.py'),
                                 '--broker', str(fixtures.BROKER), '--runtime', str(self.fx.runtime),
                                 '--state', str(self.state), '--actor', '/nonexistent'],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, start_new_session=True)
        self.assertEqual(result.returncode, 3)
        self.assertFalse(json.loads(result.stdout)['issued'])


if __name__ == '__main__':
    unittest.main()
