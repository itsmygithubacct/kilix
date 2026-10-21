"""Real ancestor/SDK/protocol source; memory adapters only, no native effects."""
import array
import ast
import errno
from contextlib import contextmanager, ExitStack
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import struct
import sys
import types
import unittest

ROOT, OLD, PLEB, OS_ROOT, OUT = map(Path, sys.argv[1:])
OUT.mkdir(parents=True, exist_ok=False)
spec=importlib.util.spec_from_file_location('loss_helpers',ROOT/'tests/test_bus_ownership_inert.py')
H=importlib.util.module_from_spec(spec);sys.modules[spec.name]=H;spec.loader.exec_module(H)
H.install_denials()
def audit(event,args):
    if event=='import' and args and str(args[0]).split('.')[0] in ('dbus','gi','Xlib'):H.deny()
    if event in ('open','os.listdir','os.scandir','os.mkdir','os.rmdir','os.remove','os.rename') and args:
        path=os.fsdecode(args[0]) if isinstance(args[0],(str,bytes,os.PathLike)) else ''
        if path.startswith(('/run/dbus','/run/user','/run/systemd','/run/kilix-private-bus','/etc/systemd','/usr/libexec/kilix-private-bus','/usr/share/polkit-1')):H.deny()
sys.addaudithook(audit)
for key in ('HOME','XDG_CONFIG_HOME','XDG_CACHE_HOME','XDG_DATA_HOME','XDG_STATE_HOME','XDG_RUNTIME_DIR','TMPDIR','GPU_TERMINAL_HOME'):
    assert Path(os.environ[key]).is_relative_to(OUT.parent.parent)
assert 'DISPLAY' not in os.environ and 'WAYLAND_DISPLAY' not in os.environ
for key in ('setuid','seteuid','setresuid','setreuid','setgid','setegid','setresgid','setregid','setgroups','initgroups','execvpe','execve','execv'):
    if hasattr(os,key):setattr(os,key,H.deny)
TOKEN='a'*32
IDENTITY=('1'*32,1000000100,'/system.slice/kilix-private-bus-supervisor@test-'+TOKEN+'.service')

class Family(H.Model):
    def __init__(self,depth=12,primary=True):
        super().__init__();self.alias={}
        self.node('sdk','physical-session',scope='sdk');self.node('cleanup-owner','owner',scope='outer')
        parent='cleanup-owner'
        for i in range(depth):
            name='descendant-'+str(i);self.node(name,parent);parent=name
        self.node('foreign','foreign-ancestor',scope='inner',instance='B');self.node('unrelated','sdk',scope='other',instance='B')
    def fd(self,value):return self.alias.get(value,value)
    def ready(self,fd,timeout=0):
        if isinstance(fd,int) and fd in self.closed:raise AssertionError('use after inert FD close')
        return super().ready(self.fd(fd),timeout)
    def signal(self,fd,sig):
        if isinstance(fd,int) and fd in self.closed:raise AssertionError('signal after inert FD close')
        return super().signal(self.fd(fd),sig)
    def reaped(self,fd):return super().reaped(self.fd(fd))
    def pidfd_pid(self,fd):return super().pidfd_pid(self.fd(fd))
    def wait_exact(self,fd):return super().wait_exact(self.fd(fd))
    def exit(self,node,status=-9):
        if node.identity=='sdk':
            node.live,node.status=False,status
            for child in self.nodes.values():
                if child.parent=='sdk':child.parent='physical-session';child.adoption=None
        else:super().exit(node,status)
    def advance(self,delta):
        super().advance(delta)
    def close(self,fd):
        if isinstance(fd,int):
            if fd in self.closed:raise AssertionError('duplicate inert numeric close')
            self.closed.append(fd)
        else:super().close(fd)

class Dual:
    def __init__(self,model):self.outer=H.Boundary(model,outer=True);self.inner=H.Boundary(model);self.identity=self.outer.identity
    def fds(self):return tuple(range(410001,410009))
    def empty(self):return self.outer.empty()
    def kill(self):return self.outer.kill()
    def retire(self):self.inner.retire();self.outer.retire()

class Wire:
    def __init__(self):self.queue=[];self.sent=[];self.closed=False;self.flags=[]
    def put(self,value,fds=(),flags=0,extra=()):
        anc=[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',fds).tobytes())] if fds else []
        self.queue.append((json.dumps(dict(value,version=2)).encode(),anc+list(extra),flags,None))
    def recvmsg(self,_size,_space,flags):
        self.flags.append(flags)
        if not self.queue:
            error=TimeoutError('inert protocol queue empty')
            error.receive_not_consumed=self
            raise error
        return self.queue.pop(0)
    def sendmsg(self,data,ancillary):
        fds=[]
        for _level,_kind,body in ancillary:fds.extend(body)
        self.sent.append((json.loads(data[0]),fds));return len(data[0])
    def settimeout(self,_value):pass
    def close(self):self.closed=True

class MemoryNative:
    def __init__(self,source,depth=12,primary=True):
        self.candidate=source
        self.source,self.ops=source,Family(depth,primary);self.login,self.token,self.uid='test',TOKEN,1000
        self.identity=IDENTITY;self.handles=source.supervision.Handles(self.ops.close);self.control=Wire();self.boundary=Dual(self.ops)
        self.sdk_fd,self.owner_fd=H.FD('sdk'),H.FD('cleanup-owner');self.events=[];self.retire_failures=0
        self.primary_attempted=self.primary_request=self.primary_payload=None
        self.primary_pid=self.primary_fd=self.primary_status_fd=None
        self.primary_waited=self.primary_exec_status=None
        self.primary_gate_committed=self.primary_exec_complete=False
        self.owner_ack=False
        self.control.put(dict(op='launch',instance=TOKEN,login='test',sdk=self.ops.nodes['sdk'].pid,source='/user/config/kilix_sdk/private_bus.py',env={'HOME':'/user'}),range(420001,420006))
    def setup(self):self.events.append('subreaper')
    def accept(self,_deadline):return self.ops.nodes['sdk'].pid,1000
    def authenticate_sdk(self,_fd,pid):
        if pid!=self.ops.nodes['sdk'].pid:raise ValueError('foreign SDK')
        self.events.append('authenticated')
    def validate_channels(self,fds):
        if len(fds)!=4:raise ValueError('channel count')
    def allocate(self):self.events.append('boundaries');return self.boundary
    def spawn(self,_hello,_fds):self.events.append('birth')
    def spawn_primary(self,value,fds):
        self.candidate.server.NativeAncestor._validate_primary(value)
        if value['instance']!=TOKEN or not self.ops.nodes['sdk'].live or not self.ops.nodes['cleanup-owner'].live:
            raise ValueError('primary launch authority is not current')
        if self.primary_attempted:
            if value!=self.primary_payload:raise RuntimeError('changed primary retry')
            for fd in fds:self.handles.release_one(fd)
            return self.primary_fd
        if len(fds)!=4:raise ValueError('primary needs three stream descriptors and status')
        self.primary_attempted=True;self.primary_request=value['request'];self.primary_payload=dict(value)
        self.primary_status_fd=fds[3]
        primary=self.ops.node('primary','owner',scope='inner')
        self.primary_pid=self.ops.nodes['primary'].pid;self.primary_fd=430100
        self.ops.alias[self.primary_fd]=H.FD('primary',1)  # birth pin differs from each drain acquisition
        self.handles.add(self.primary_fd)
        for fd in fds[:3]:self.handles.release_one(fd)
        self.events.append(('gated-primary-birth',self.primary_pid,value['env']))
        return self.primary_fd
    def commit_primary(self,request):
        if (request!=self.primary_request or self.primary_fd is None or
            not self.ops.nodes['sdk'].live or not self.ops.nodes['cleanup-owner'].live):
            raise RuntimeError('cannot release primary gate without live launch authorities')
        self.primary_gate_committed=self.primary_exec_complete=True
        self.events.append(('primary-gate-release',request))
        return self.primary_pid
    def collect_primary(self):
        if self.primary_fd is None or self.primary_exec_status is not None:return self.primary_exec_status
        info=self.ops.wait_exact(self.primary_fd)
        if info is None:return None
        self.primary_exec_status=info.si_status if info.si_code==os.CLD_EXITED else -info.si_status
        self.primary_waited=True
        if self.primary_status_fd is not None:
            self.handles.release_one(self.primary_status_fd);self.primary_status_fd=None
        return self.primary_exec_status
    def recover_primary_pin(self):
        if self.primary_fd is None or self.primary_waited:raise self.candidate.own.CleanupIncomplete('primary-birth-authority-lost',resource=self)
        return self.primary_fd
    def wait_event(self):return 'close'
    def packet_ready(self):return bool(self.control.queue)
    def retire(self):
        if self.retire_failures:self.retire_failures-=1;raise RuntimeError('inert retirement refusal')
        self.boundary.retire();self.handles.close();self.events.append('retired')
    def close_connections(self):self.control.close()

def source(root,label):
    value=H.load_source(root,label)
    for alias,name in (('supervision','bus_supervision'),('server','bus_supervisor'),('pin','process_pin'),('platform','bus_platform')):
        setattr(value,alias,sys.modules[label+'.'+name])
    return value
CURRENT=source(ROOT,'loss_candidate')

def stream_source(root):
    path=root/'config/stream.py';tree=ast.parse(path.read_text(),filename=str(path))
    original=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='StreamSupervisor')
    defined={n.name:n for n in original.body if isinstance(n,ast.FunctionDef)}
    # Close over every StreamSupervisor method reached through self, so a helper
    # added to spawn/cleanup can never be silently missing from the extraction.
    helpers={'spawn','cleanup'};todo=list(helpers)
    while todo:
        for a in ast.walk(defined[todo.pop()]):
            if isinstance(a,ast.Attribute) and isinstance(a.value,ast.Name) and a.value.id=='self' and a.attr in defined and a.attr not in helpers:
                helpers.add(a.attr);todo.append(a.attr)
    methods=[n for n in original.body if isinstance(n,ast.FunctionDef) and n.name in helpers]
    selected=ast.ClassDef(name=original.name,bases=original.bases,keywords=original.keywords,body=methods,decorator_list=original.decorator_list)
    actual=ast.fix_missing_locations(ast.Module(body=[selected],type_ignores=[]))
    assert [ast.dump(n,include_attributes=True) for n in methods]==[ast.dump(n,include_attributes=True) for n in actual.body[0].body]
    value=types.ModuleType('loss_stream_'+str(len(H.BINDINGS)));exec(compile(actual,str(path),'exec'),value.__dict__)
    value.CheckedDescriptor=CURRENT.own.CheckedDescriptor
    value.CleanupIncomplete=CURRENT.own.CleanupIncomplete
    H.BINDINGS.append(dict(path=str(path),sha256=H.digest(path.read_bytes()),mode='exact-AST-selection',nodes={n.name:H.digest(ast.dump(n,include_attributes=True).encode()) for n in methods},excluded='native imports and other stream functions'))
    return value

class LossTests(unittest.TestCase):
    candidate=CURRENT
    def server(self,depth=12,primary=True):
        n=MemoryNative(self.candidate,depth,primary);server=self.candidate.server.SupervisorServer(n);server.bootstrap()
        if primary:
            value=dict(op='primary-launch',instance=TOKEN,request='b'*32,
                       argv=['/usr/bin/inert-app'],env={'PLEB_DESKTOP_SESSION_ID':'42','XDG_SESSION_ID':'42',
                       'DISPLAY':':0','DBUS_SESSION_BUS_ADDRESS':'unix:path=/private'},cwd='/inert')
            n.control.put(value,(430001,430002,430003,430004));server.packet()
            n.owner_ack=True
            n.control.put(dict(op='primary-commit',instance=TOKEN,request='b'*32));server.packet()
        return server,n
    def proof(self,server,n):
        value=server.finish();self.assertEqual(value['source'],'ancestor-waits');self.assertTrue(value['adoption_drained'] and value['primary_reaped'])
        self.assertFalse(n.ops.survivors())
        expected={name for name,node in n.ops.nodes.items() if node.instance=='A' and node.scope in ('inner','outer')}
        self.assertEqual(set(n.ops.waited),expected)
        if n.primary_fd is not None:self.assertIn('primary',n.ops.waited)
        self.assertTrue(n.ops.nodes['foreign'].live and n.ops.nodes['unrelated'].live)
        return value
    def test_simultaneous_sdk_and_owner_loss_delivers_full_waits(self):
        server,n=self.server(64)
        self.assertEqual(n.primary_payload['env']['XDG_SESSION_ID'],'42')
        self.assertEqual(n.primary_payload['env']['PLEB_DESKTOP_SESSION_ID'],'42')
        self.assertTrue(n.primary_gate_committed)
        n.ops.exit(n.ops.nodes['sdk']);n.ops.exit(n.ops.nodes['cleanup-owner']);self.proof(server,n)
    def test_single_sdk_loss_and_single_owner_loss(self):
        for lost in ('sdk','cleanup-owner'):
            with self.subTest(lost=lost):
                server,n=self.server(24);n.ops.exit(n.ops.nodes[lost]);self.proof(server,n)
    def test_reordered_combined_loss_before_and_after_handoff(self):
        for order in (('sdk','cleanup-owner'),('cleanup-owner','sdk')):
            for primary in (False,True):
                with self.subTest(order=order,primary=primary):
                    server,n=self.server(12,primary)
                    for name in order:n.ops.exit(n.ops.nodes[name]);n.ops.advance(.001)
                    self.proof(server,n)
    def test_stopped_branching_late_descendants_without_depth_bound(self):
        server,n=self.server(64)
        for i in range(10):n.ops.node('branch-'+str(i),'descendant-5')
        n.ops.late.append((.15,lambda:n.ops.node('last-fork','descendant-63')));self.proof(server,n);self.assertIn('last-fork',n.ops.waited)
    def test_budget_expiry_retains_registry_for_retry(self):
        server,n=self.server(24);n.ops.costs[('acquire',2)]=2
        with self.assertRaises(self.candidate.own.CleanupIncomplete):server.finish()
        self.assertTrue(server.cleanup.cleanup.drain.owned);self.assertFalse(n.boundary.outer.retired);self.proof(server,n)
    def test_delayed_adoption_wait_certificate_is_required(self):
        server,n=self.server(24);n.ops.adoption_delay=1;n.ops.exit(n.ops.nodes['cleanup-owner']);self.proof(server,n)
    def test_primary_exit_without_reaping_is_incomplete(self):
        server,n=self.server();cleanup=server.cleanup.cleanup;real_attempt=cleanup.attempt
        n.boundary.kill()  # Exit evidence alone cannot certify exact primary wait.
        cleanup.attempt=lambda *_args,**_kwargs:self.candidate.own.completion(
            TOKEN,n.boundary,True,complete=True)
        with self.assertRaises(self.candidate.own.CleanupIncomplete):server.finish()
        self.assertIsNone(server.receipt);self.assertFalse(n.boundary.outer.retired)
        cleanup.attempt=real_attempt;self.proof(server,n)
    def test_subreaper_boundary_authentication_before_birth(self):
        server,n=self.server(primary=False);self.assertEqual(n.events[:4],['subreaper','authenticated','boundaries','birth'])
        self.assertEqual(n.control.sent[0][1],list(n.boundary.fds()));self.assertEqual(n.control.flags[0],socket.MSG_CMSG_CLOEXEC);self.proof(server,n)
    def test_caller_born_or_foreign_primary_protocol_never_becomes_authority(self):
        server,n=self.server(primary=False);n.control.put(dict(op='primary',instance=TOKEN),(430099,))
        with self.assertRaises(ValueError):server.packet()
        self.assertIsNone(server.cleanup.primary);self.proof(server,n)
    def test_partial_retirement_retains_proof_and_retries(self):
        server,n=self.server();n.retire_failures=1
        with self.assertRaises(RuntimeError):server.finish()
        self.assertIsNotNone(server.receipt);self.assertFalse(n.boundary.outer.retired);self.proof(server,n)
    def test_server_cancellation_and_sdk_eof_do_not_abandon_children(self):
        for event in (KeyboardInterrupt('inert cancellation'),ConnectionResetError('SDK EOF')):
            n=MemoryNative(self.candidate,primary=False)
            def choose():raise event
            n.wait_event=choose;server=self.candidate.server.SupervisorServer(n)
            self.assertEqual(server.run(),0);self.assertFalse(n.ops.survivors());self.assertTrue(n.control.closed)
    def test_receipt_rejects_foreign_stale_cgroup_only_or_partial_proof(self):
        server,n=self.server();value=self.proof(server,n);s=self.candidate.supervision
        self.assertEqual(s.validate_receipt(value,TOKEN,n.boundary.identity,IDENTITY,True),value)
        for key,wrong in (('instance','b'*32),('supervisor',['2'*32,IDENTITY[1],IDENTITY[2]]),('source','cgroup-empty'),('adoption_drained',False),('primary_reaped',False),('primary_wait_authority','pidfd-readable'),('bound',False),('pending',[1])):
            with self.subTest(key=key),self.assertRaises(ValueError):s.validate_receipt(dict(value,**{key:wrong}),TOKEN,n.boundary.identity,IDENTITY,True)
    def test_invalid_ancillary_keeps_every_delivered_descriptor(self):
        s=self.candidate.supervision
        for count,flags,extra in ((8,socket.MSG_CTRUNC,()),(7,0,()),(9,0,()),(8,0,((999,999,b'data'),))):
            wire=Wire();wire.put(dict(op='boundary'),range(440001,440001+count),flags,extra);closed=[];handles=s.Handles(closed.append)
            with self.assertRaises(ValueError):s.receive(wire,8,handles)
            self.assertEqual(handles.fds,list(range(440001,440001+count)));handles.close();self.assertEqual(closed,list(range(440001,440001+count)))
    def test_late_release_is_invalidated_and_preoperation_failure_retained(self):
        s=self.candidate.supervision;attempts=[];closed=[]
        faults={450001:PermissionError('pre-operation'),450002:self.candidate.own.DescriptorReleased('late release')}
        def closer(fd):
            attempts.append(fd);problem=faults.pop(fd,None)
            if isinstance(problem,self.candidate.own.DescriptorReleased):closed.append(fd)
            if problem:raise problem
            closed.append(fd)
        handles=s.Handles(closer)
        for fd in (450001,450002,450003):handles.add(fd)
        with self.assertRaises(self.candidate.own.CleanupIncomplete):handles.close()
        self.assertEqual(handles.fds,[450001]);handles.close();self.assertEqual(attempts.count(450002),1);self.assertEqual(set(closed),{450001,450002,450003})
    def test_cancelled_close_never_retries_replacement_fd(self):
        state={'inode':100};calls=[]
        def closer(fd):calls.append(fd);state['inode']=999;raise KeyboardInterrupt('post-release cancellation')
        lease=self.candidate.own.CheckedDescriptor(460001,types.SimpleNamespace(fstat=lambda _fd:types.SimpleNamespace(st_dev=1,st_ino=state['inode'],st_mode=0o100600),close=closer));lease.capture()
        with self.assertRaises(KeyboardInterrupt):lease.close()
        try:lease.close()
        except BaseException:pass
        self.assertEqual(calls,[460001]);self.assertIsNone(lease.fd)
    def test_preoperation_cancellation_retains_original_descriptor(self):
        calls=[]
        def closer(fd):
            calls.append(fd)
            if len(calls)==1:raise getattr(self.candidate.own,'DescriptorCloseCancelled',KeyboardInterrupt)('pre-operation cancellation')
        lease=self.candidate.own.CheckedDescriptor(460001,types.SimpleNamespace(fstat=lambda _fd:types.SimpleNamespace(st_dev=1,st_ino=100,st_mode=0o100600),close=closer),raw=False);lease.capture()
        with self.assertRaises(KeyboardInterrupt):lease.close()
        self.assertEqual(lease.fd,460001);lease.close();self.assertIsNone(lease.fd)
    def test_missing_bootstrap_fd_and_concurrent_reaper_never_open_pid(self):
        module=self.candidate.bus;bus=module.PrivateBus.__new__(module.PrivateBus);bus.owner_pidfd=None;bus.ops=H.Model()
        bus.ops.node('replacement','other',scope='other',instance='B');acquired=[]
        bus.owner_process=types.SimpleNamespace(pid=1000000090,returncode=None,poll=lambda:0)
        def acquire(pid):acquired.append(pid);return H.FD('replacement')
        with H.replace(module,os=types.SimpleNamespace(pidfd_open=acquire)):
            with self.assertRaises(self.candidate.own.CleanupIncomplete):bus._stop_bootstrap()
            bus.owner_process.returncode=0;bus._stop_bootstrap()
        self.assertEqual(acquired,[]);self.assertTrue(bus.ops.nodes['replacement'].live)
    def test_primary_external_reaper_uses_retained_pidfd_only(self):
        process=types.SimpleNamespace(pid=1000000090,returncode=None,_waitpid_lock=H.DummyLock());calls=[]
        def wait(fd):calls.append(fd);raise ChildProcessError('other waiter')
        ops=types.SimpleNamespace(wait_exact=wait,reaped=lambda _fd:True,close=lambda fd:calls.append(('close',fd)))
        pin=self.candidate.pin.PidfdProcess(process,470001,ops);self.assertIs(pin.poll(),self.candidate.pin.UnknownExitStatus.REAPED_ELSEWHERE)
        pin.release();self.assertEqual(calls,[470001,('close',470001)]);self.assertIsNotNone(process.returncode)
    def test_primary_missing_wait_evidence_retains_fd(self):
        process=types.SimpleNamespace(returncode=None,_waitpid_lock=H.DummyLock())
        def wait(_fd):raise ChildProcessError()
        pin=self.candidate.pin.PidfdProcess(process,470001,types.SimpleNamespace(wait_exact=wait,reaped=lambda _fd:False))
        with self.assertRaises(self.candidate.own.CleanupIncomplete):pin.poll()
        self.assertEqual(pin.fd,470001);self.assertIsNone(process.returncode)
    def test_linux514_readable_zombie_is_not_reaping_evidence(self):
        module=self.candidate.own
        for pid,ready,expected in ((1000000020,True,False),(-1,True,True),(1000000020,False,False)):
            with H.replace(module,Path=lambda _path:types.SimpleNamespace(read_text=lambda:f'Pid:\t{pid}\n'),select=types.SimpleNamespace(select=lambda *_args:([470001] if ready else [],[],[]))):
                self.assertIs(module.LinuxProcesses.reaped(470001),expected)
    def test_anonymous_descriptor_cancellation_never_closes_same_stat_replacement(self):
        calls=[]
        def closer(fd):calls.append(fd);raise KeyboardInterrupt('ambiguous anon-inode release')
        lease=self.candidate.own.CheckedDescriptor(470002,types.SimpleNamespace(fstat=lambda _fd:types.SimpleNamespace(st_dev=1,st_ino=1,st_mode=0o600),close=closer));lease.capture()
        with self.assertRaises(KeyboardInterrupt):lease.close()
        with self.assertRaisesRegex(self.candidate.own.CleanupIncomplete,'anonymous'):lease.close()
        self.assertEqual(calls,[470002]);self.assertEqual(lease.fd,470002)
    def test_registry_cancellation_attempts_independent_caps_and_retains_identity(self):
        s=self.candidate.supervision;state={471001:1,471002:2};calls=[]
        def closer(fd):
            calls.append(fd)
            if fd==471001:state[fd]=100;raise KeyboardInterrupt('late raw close cancellation')
        fs=types.SimpleNamespace(fstat=lambda fd:types.SimpleNamespace(st_dev=1,st_ino=state[fd],st_mode=0o100600),close=closer)
        handles=s.Handles(closer,fs);handles.add(471001);handles.add(471002)
        with self.assertRaises(self.candidate.own.CleanupIncomplete):handles.close()
        self.assertEqual(calls,[471001,471002]);self.assertEqual(handles.fds,[471001])
        handles.close();self.assertEqual(calls,[471001,471002]);self.assertEqual(handles.fds,[])
    def test_registry_single_release_and_bulk_close_share_cancelled_authority(self):
        calls=[];state={471101:1,471102:2}
        def closer(fd):
            calls.append(fd)
            if fd==471101:state[fd]=100;raise KeyboardInterrupt('ambiguous root channel release')
        fs=types.SimpleNamespace(fstat=lambda fd:types.SimpleNamespace(st_dev=1,st_ino=state[fd],st_mode=0o100600),close=closer)
        handles=self.candidate.supervision.Handles(closer,fs)
        for fd in state:handles.add(fd)
        with self.assertRaises(KeyboardInterrupt):handles.release_one(471101)
        handles.close();self.assertEqual(calls,[471101,471102]);self.assertEqual(handles.fds,[])
    def test_ancillary_capture_failure_keeps_entire_delivery_and_packet(self):
        wire=Wire();wire.put(dict(op='boundary'),range(471201,471209));calls=[];retained=[]
        def fstat(fd):
            if fd==471201:raise KeyboardInterrupt('capture cancellation')
            return types.SimpleNamespace(st_dev=1,st_ino=fd,st_mode=0o100600)
        handles=self.candidate.supervision.Handles(calls.append,types.SimpleNamespace(fstat=fstat,close=calls.append))
        with self.assertRaises(KeyboardInterrupt):self.candidate.supervision.receive(wire,8,handles,lambda *packet:retained.append(packet))
        self.assertEqual(handles.fds,list(range(471201,471209)));self.assertEqual(retained[0][1],handles.fds)
        handles.close();self.assertEqual(calls,list(range(471201,471209)))
    def test_native_setup_subreaper_and_explicit_wait_policy_precede_listener(self):
        module=self.candidate.server;events=[];n=module.NativeAncestor.__new__(module.NativeAncestor)
        n.instance='test-'+TOKEN;n.identity=None
        listener=types.SimpleNamespace(bind=lambda path:events.append('bind'),listen=lambda count:events.append('listen'))
        fake_os=types.SimpleNamespace(getuid=lambda:0,geteuid=lambda:0,getppid=lambda:1,getpid=lambda:IDENTITY[1],
            environ={'INVOCATION_ID':IDENTITY[0]},stat=lambda *_args,**_kw:types.SimpleNamespace(st_mode=0o40711,st_uid=0),chmod=lambda *_args:None)
        fake_signal=types.SimpleNamespace(SIGCHLD=signal.SIGCHLD,SIG_DFL=signal.SIG_DFL,SIGTERM=signal.SIGTERM,SIGHUP=signal.SIGHUP,SIGINT=signal.SIGINT,
            signal=lambda sig,handler:events.append(('handler',sig,handler)))
        with H.replace(module,os=fake_os,prctl=lambda option,value:events.append(('prctl',option,value)),
            signal=fake_signal,Path=lambda _path:types.SimpleNamespace(read_text=lambda:'0::'+IDENTITY[2]+'\n'),
            socket=types.SimpleNamespace(AF_UNIX=1,SOCK_SEQPACKET=5,SOCK_CLOEXEC=0,socket=lambda *_args:events.append('socket') or listener)):
            n.setup()
        self.assertLess(events.index(('prctl',36,1)),events.index('socket'))
        self.assertLess(events.index(('handler',signal.SIGCHLD,signal.SIG_DFL)),events.index('socket'))
        self.assertEqual(n.identity,IDENTITY)
    def test_primary_birth_is_pinned_and_migrated_while_gated_before_exec(self):
        module=self.candidate.server;n=module.NativeAncestor.__new__(module.NativeAncestor);events=[];uid={'ids':(0,0,0)}
        n.login,n.token,n.uid='test',TOKEN,1000;n.account=types.SimpleNamespace(pw_uid=1000,pw_gid=1000)
        n.boundary=types.SimpleNamespace(inner=types.SimpleNamespace(join_fd=480010));n.sdk_fd,n.owner_fd=480011,480012
        n.ops=types.SimpleNamespace(ready=lambda _fd:False,pidfd_pid=lambda _fd:480100,
                                    close=lambda fd:events.append(('close',fd)))
        def fstat(fd):
            return types.SimpleNamespace(st_dev=1,st_ino=fd,st_mode=0o010600 if fd==480023 else 0o100600)
        handles=self.candidate.supervision.Handles(n.ops.close,types.SimpleNamespace(fstat=fstat,close=n.ops.close))
        n.handles=handles
        request=dict(version=2,op='primary-launch',instance=TOKEN,request='c'*32,
                     argv=['/usr/bin/inert-app'],env={'XDG_SESSION_ID':'42','PLEB_DESKTOP_SESSION_ID':'42',
                     'DISPLAY':':71','DBUS_SESSION_BUS_ADDRESS':'unix:path=/private'},cwd='/inert')
        def write(fd,data):events.append(('write',fd,data));return len(data)
        def pipe2(_flags):
            count=sum(1 for row in events if row[0]=='pipe');events.append(('pipe',count))
            return (480002,480003) if count==0 else (480004,480005)
        fake_os=types.SimpleNamespace(fstat=fstat,getresuid=lambda:uid['ids'],
            setresuid=lambda *ids:uid.update(ids=ids),getpid=lambda:480001,
            fork=lambda:events.append(('fork',)) or 480100,pidfd_open=lambda pid:events.append(('pidfd-open',pid)) or 480013,
            pipe2=pipe2,pthread_sigmask=lambda how,mask:events.append(('mask',how,tuple(mask))) or set(),
            write=write,read=lambda _fd,_size:b'',close=n.ops.close,
            O_CLOEXEC=0,O_ACCMODE=3,O_WRONLY=1,O_NONBLOCK=2048)
        fake_os.path=types.SimpleNamespace(isabs=lambda value:value.startswith('/'))
        fake_signal=types.SimpleNamespace(SIGINT=signal.SIGINT,SIGTERM=signal.SIGTERM,SIGHUP=signal.SIGHUP,
            SIG_BLOCK=signal.SIG_BLOCK,SIG_SETMASK=signal.SIG_SETMASK,
            pthread_sigmask=lambda *_args:events.append(('signal-mask',)) or set())
        n.primary_attempted=n.primary_gate_attempted=n.primary_gate_committed=False
        n.primary_request=n.primary_payload=n.primary_pid=n.primary_fd=n.primary_gate_fd=None
        n.primary_exec_error_fd=n.primary_status_fd=None;n.primary_exec_complete=False
        n.primary_exec_error=n.primary_exec_status=None;n.primary_waited=False
        fds=(480020,480021,480022,480023)
        for fd in fds:handles.add(fd)
        gate_written={'value':False}
        def select_ready(fds,*_args):
            return ([480004],[],[]) if gate_written['value'] else ([],[],[])
        def commit_write(fd,data):
            if fd==480003 and data==b'G':gate_written['value']=True
            return write(fd,data)
        fake_os.write=commit_write
        with H.replace(module,os=fake_os,fcntl=types.SimpleNamespace(F_GETFL=1,fcntl=lambda _fd,_op:2049),
            resource=types.SimpleNamespace(RLIMIT_NOFILE=7,RLIM_INFINITY=-1,getrlimit=lambda _key:(500000,500000)),
            signal=fake_signal,select=types.SimpleNamespace(select=select_ready),
            Path=lambda _path:types.SimpleNamespace(read_text=lambda:'CapEff:\t0000000000000000\n')):
            n.spawn_primary(request,fds)
            self.assertEqual(n.primary_payload['env'],request['env'])
            self.assertEqual((n.primary_fd,n.primary_pid),(480013,480100))
            self.assertIn(480003,handles.fds);self.assertIn(480004,handles.fds);self.assertIn(480023,handles.fds)
            self.assertLess(events.index(('pidfd-open',480100)),events.index(('write',480010,b'480100\n')))
            self.assertFalse(gate_written['value'])
            self.assertEqual(n.commit_primary(request['request']),480100)
            self.assertTrue(n.primary_exec_complete)
            self.assertLess(events.index(('write',480010,b'480100\n')),events.index(('write',480003,b'G')))
        self.assertEqual(uid['ids'],(0,0,0))
    def test_primary_commit_refuses_sdk_loss_without_releasing_gate(self):
        module=self.candidate.server;n=module.NativeAncestor.__new__(module.NativeAncestor);events=[]
        n.primary_attempted=True;n.primary_request='9'*32;n.primary_exec_complete=False
        n.primary_exec_error=n.primary_exec_status=None;n.primary_gate_committed=n.primary_gate_attempted=False
        n.primary_gate_fd=480301;n.primary_exec_error_fd=480302;n.primary_pid=480303
        n.sdk_fd=480304;n.owner_fd=480305
        n.ops=types.SimpleNamespace(ready=lambda fd:fd==n.sdk_fd)
        n.handles=types.SimpleNamespace(release_one=lambda fd:events.append(('release',fd)))
        with H.replace(module,os=types.SimpleNamespace(write=lambda fd,data:events.append(('write',fd,data)) or len(data)),
                       select=types.SimpleNamespace(select=lambda *_args:([],[],[]))):
            with self.assertRaises(self.candidate.own.CleanupIncomplete):n.commit_primary('9'*32)
        self.assertFalse(any(event[0]=='write' and event[2]==b'G' for event in events))
        self.assertFalse(any(event[0]=='release' and event[1]==480301 for event in events))
    def test_native_owner_adapter_drops_uid_before_user_exec(self):
        module=self.candidate.server;n=module.NativeAncestor.__new__(module.NativeAncestor);events=[];uid={'value':0}
        n.account=types.SimpleNamespace(pw_uid=1000,pw_gid=1000);n.login,n.token,n.uid='test',TOKEN,1000
        n.boundary=types.SimpleNamespace(outer=types.SimpleNamespace(join_fd=490001))
        n.birth_pid=None;n.birth_attempted=n.birth_gate_committed=False;n.owner_fd=None;n.process=None
        n.ops=types.SimpleNamespace(close=lambda fd:events.append(('close',fd)),pidfd_pid=lambda _fd:1000000091)
        fs=types.SimpleNamespace(fstat=lambda fd:types.SimpleNamespace(st_dev=1,st_ino=fd,st_mode=0o100600),
                                 close=n.ops.close)
        n.handles=self.candidate.supervision.Handles(n.ops.close,fs)
        fds=tuple(range(490011,490016))
        for fd in fds:n.handles.add(fd)
        def write(fd,data):events.append(('write',fd,data));return len(data)
        fake_os=types.SimpleNamespace(getpid=lambda:1000000100,fork=lambda:events.append('fork') or 1000000091,
            pipe2=lambda _flags:(490030,490031),pidfd_open=lambda pid:events.append(('pidfd-open',pid)) or 490020,
            write=write,close=n.ops.close,O_CLOEXEC=0)
        with H.replace(module,os=fake_os,resource=types.SimpleNamespace(RLIMIT_NOFILE=7,RLIM_INFINITY=-1,
                getrlimit=lambda _key:(500000,500000))):
            n.spawn({'source':'/user/private_bus.py','sdk':1000000090,'env':{'PATH':'/user/untrusted'}},fds)
        self.assertLess(events.index('fork'),events.index(('pidfd-open',1000000091)))
        self.assertLess(events.index(('pidfd-open',1000000091)),events.index(('write',490001,b'1000000091\n')))
        self.assertLess(events.index(('write',490001,b'1000000091\n')),events.index(('write',490031,b'G')))
        self.assertEqual(n.handles.fds,[fds[0],490020])
        exec_events=[]
        def execve(path,argv,env):
            self.assertEqual((path,argv[0]),('/usr/bin/python3','/usr/bin/python3'))
            self.assertEqual(uid['value'],1000);exec_events.append(('exec',argv,env))
        child_os=types.SimpleNamespace(getppid=lambda:1000000100,read=lambda _fd,_n:b'G',close=lambda fd:exec_events.append(('close',fd)),
            initgroups=lambda *args:exec_events.append(('groups',args)),setgid=lambda gid:exec_events.append(('gid',gid)),
            setuid=lambda value:uid.update(value=value),getuid=lambda:uid['value'],geteuid=lambda:uid['value'],
            set_inheritable=lambda fd,value:exec_events.append(('inherit',fd,value)),
            closerange=lambda low,high:exec_events.append(('close-range',low,high)),execve=execve)
        child_signal=types.SimpleNamespace(SIGKILL=signal.SIGKILL,SIGTERM=signal.SIGTERM,SIGHUP=signal.SIGHUP,
            SIGINT=signal.SIGINT,SIG_DFL=signal.SIG_DFL,signal=lambda sig,handler:exec_events.append(('handler',sig,handler)))
        with H.replace(module,os=child_os,signal=child_signal,prctl=lambda option,value:exec_events.append(('prctl',option,value))):
            n.exec_owner({'source':'/user/private_bus.py','sdk':1000000090,'env':{'PATH':'/user/untrusted'}},fds,
                         1000000100,490030,500000)
        self.assertLess(exec_events.index(('gid',1000)),next(i for i,row in enumerate(exec_events) if row[0]=='exec'))
        self.assertLess(next(i for i,row in enumerate(exec_events) if row==('prctl',38,1)),
                        next(i for i,row in enumerate(exec_events) if row[0]=='exec'))
    def test_native_allocation_delegates_only_owned_workload_migration_files(self):
        module=self.candidate.server;n=module.NativeAncestor.__new__(module.NativeAncestor);calls=[]
        n.uid,n.account,n.token,n.identity=1000,types.SimpleNamespace(pw_gid=1000),TOKEN,IDENTITY
        boundary=types.SimpleNamespace(outer=types.SimpleNamespace(join_fd=492001),inner=types.SimpleNamespace(join_fd=492002))
        n.delegation=types.SimpleNamespace(open=lambda path,pid:calls.append(('open',path,pid)) or 492000,boundary=lambda fd,token:boundary)
        with H.replace(module,os=types.SimpleNamespace(fchown=lambda fd,uid,gid:calls.append(('grant',fd,uid,gid)),fchmod=lambda fd,mode:calls.append(('mode',fd,mode)))):self.assertIs(n.allocate(),boundary)
        self.assertEqual(calls,[('open',IDENTITY[2],IDENTITY[1]),('grant',492001,1000,0),('mode',492001,0o660),('grant',492002,1000,0),('mode',492002,0o660)])
    def test_primary_request_rejects_unbound_command_env_and_stale_instance(self):
        module=self.candidate.server;valid=dict(version=2,op='primary-launch',instance=TOKEN,request='d'*32,
            argv=['/usr/bin/inert-app'],env={'PLEB_DESKTOP_SESSION_ID':'42','XDG_SESSION_ID':'42'},cwd='/inert')
        module.NativeAncestor._validate_primary(valid)
        for change in (dict(request='not-a-request'),dict(argv=[]),
                       dict(env={'bad=name':'value'}),dict(env={'X':'bad\x00value'}),dict(cwd='relative')):
            with self.subTest(change=change),self.assertRaises(ValueError):
                module.NativeAncestor._validate_primary(dict(valid,**change))
        n=module.NativeAncestor.__new__(module.NativeAncestor);n.token=TOKEN
        with self.assertRaises(ValueError):n.spawn_primary(dict(valid,instance='e'*32),())
    def test_private_bus_environment_keeps_physical_lock_context(self):
        bus=self.candidate.bus.PrivateBus.__new__(self.candidate.bus.PrivateBus)
        bus.address='unix:path=/private-bus';bus.daemon_pid=1000000101
        original={'XDG_SESSION_ID':'42','PLEB_DESKTOP_SESSION_ID':'42','PLEB_DESKTOP_DISPLAY':':71',
                  'PLEB_DESKTOP_BUS_ADDRESS':'unix:path=/physical','DISPLAY':':99',
                  'DBUS_STARTER_ADDRESS':'stale','DBUS_STARTER_BUS_TYPE':'session'}
        value=bus.environment(original)
        for key in ('XDG_SESSION_ID','PLEB_DESKTOP_SESSION_ID','PLEB_DESKTOP_DISPLAY',
                    'PLEB_DESKTOP_BUS_ADDRESS','DISPLAY'):
            self.assertEqual(value[key],original[key])
        self.assertEqual(value['DBUS_SESSION_BUS_ADDRESS'],bus.address)
        self.assertNotIn('DBUS_STARTER_ADDRESS',value);self.assertNotIn('DBUS_STARTER_BUS_TYPE',value)
    def test_sdk_opens_primary_gate_only_after_owner_and_guardian_ack(self):
        module=self.candidate.bus;bus=module.PrivateBus.__new__(module.PrivateBus);events=[]
        class Control:
            def recv(self,_size):events.append('owner-guardian-ack');return b'A'
        class App:
            pid=480101;fd=480102
            def enable_user_signals(self):events.append('user-signals-enabled')
        class Platform:
            receipt=None
            def commit_primary(self,request):events.append(('root-commit',request))
        bus._lock=H.DummyLock();bus._closing=bus.closed=False;bus.cleanup_state=types.SimpleNamespace(fault=None)
        bus.primary_process=App();bus._primary_bound=False;bus.control=Control();bus.pidfd=480103
        bus.owner_pidfd=480104;bus.platform=Platform();bus.ops=types.SimpleNamespace(ready=lambda _fd:False)
        bus._primary_request_id='e'*32;bus.app_fd=bus.gate_write=None
        with H.replace(module,send_pidfd=lambda _control,_data,fd:events.append(('owner-fd',fd)),
                       select=types.SimpleNamespace(select=lambda *_args:([bus.control],[],[]))):
            bus.bind(bus.primary_process)
        self.assertLess(events.index(('owner-fd',480102)),events.index('owner-guardian-ack'))
        self.assertLess(events.index('owner-guardian-ack'),events.index(('root-commit','e'*32)))
        self.assertLess(events.index(('root-commit','e'*32)),events.index('user-signals-enabled'))
    def test_remote_primary_signals_are_blocked_until_ancestor_commit(self):
        module=self.candidate.pin;calls=[]
        fs=types.SimpleNamespace(fstat=lambda fd:types.SimpleNamespace(st_dev=1,st_ino=fd,st_mode=0o100600),
                                 close=lambda fd:calls.append(('close',fd)))
        ops=types.SimpleNamespace(ready=lambda _fd:False,signal=lambda fd,sig:calls.append(('signal',fd,sig)),
                                  reaped=lambda _fd:False,close=lambda fd:calls.append(('close-exact',fd)))
        process=module.RemoteProcess(480201,480202,480203,['inert-app'],ops=ops,filesystem=fs)
        with self.assertRaises(self.candidate.own.CleanupIncomplete):process.terminate()
        process.enable_user_signals();process.terminate()
        self.assertEqual(calls,[('signal',480202,signal.SIGTERM)])
    def test_root_signal_uses_caller_real_and_effective_uid_without_capability(self):
        module=self.candidate.server
        for caps,refusal in ((0,False),(0,True),(1,False)):
            n=module.NativeAncestor.__new__(module.NativeAncestor);n.uid=1000;state={'ids':(0,0,0)};calls=[]
            n.birth_pid=None;n.birth_attempted=False;n.birth_gate_committed=False
            n.primary_pid=None;n.primary_waited=False;n.primary_exec_complete=False
            def signal_exact(fd,sig):
                self.assertEqual(state['ids'],(1000,1000,0));calls.append((fd,sig))
                if refusal:raise PermissionError('kernel denies privileged target')
            n._signal_exact=signal_exact
            with H.replace(module,os=types.SimpleNamespace(getresuid=lambda:state['ids'],setresuid=lambda *ids:state.update(ids=ids)),
                Path=lambda _path:types.SimpleNamespace(read_text=lambda:f'CapEff:\t{caps:016x}\n')):
                if caps:
                    with self.assertRaises(self.candidate.own.CleanupIncomplete):n.signal_owned(494001,signal.SIGKILL)
                    self.assertEqual(calls,[])
                elif refusal:
                    with self.assertRaises(PermissionError):n.signal_owned(494001,signal.SIGKILL)
                else:n.signal_owned(494001,signal.SIGKILL)
                self.assertEqual(state['ids'],(0,0,0))
    @contextmanager
    def frontend(self,peer_uid=0,peer_pid=IDENTITY[1],launch=True):
        s=self.candidate.supervision;n=MemoryNative(self.candidate,primary=False);n.control.queue.clear()
        n.ops.node('platform-root','pid1',scope='supervisor');main=H.FD('platform-root');n.ops.alias[510000]=main
        server=self.candidate.server.SupervisorServer(n);fixture=self
        n.ops.node('wait-worker','pid1',scope='wait-domain',instance='W')
        n.ops.alias[510099]=H.FD('wait-worker')
        wait_id=('b'*32,n.ops.nodes['wait-worker'].pid,'/system.slice/'+s.wait_name('test-'+TOKEN))
        def envelope(value):return dict(version=s.VERSION,op='wait-certificate',instance=TOKEN,wait_owner=list(wait_id),certificate=value)

        class Manager:
            started=closed=False;faults=0;replacement=False
            def connect(self,_deadline):pass
            def _safe_directory(self,path,uid):fixture.assertEqual(uid,0)
            def start(self,name,_deadline):self.started=True;self.name=name
            def unit(self,name,_deadline):
                if self.faults:self.faults-=1;raise ConnectionError('memory manager disconnect')
                if not self.started:return None
                value=fixture.record(p)
                if not n.ops.nodes['platform-root'].live:value.update(ActiveState='inactive',ControlGroup='')
                if self.replacement:value['InvocationID']=b'3'*16
                return value
            def scope(self,name,_deadline):
                if not n.ops.nodes['wait-worker'].live:return None
                return dict(Id=s.wait_name('test-'+TOKEN),Transient=True,Delegate=True,KillMode='control-group',SendSIGKILL=True,CollectMode='inactive-or-failed',ActiveState='active',Description='Kilix exact wait owner test-'+TOKEN,InvocationID=bytes.fromhex(wait_id[0]),ControlGroup=wait_id[2])
            def reconnect(self,_deadline):self.reconnected=True
            def close(self):self.closed=True
        manager=Manager()
        class Client(Wire):
            lose_primary_ack=False
            def connect(self,_path):self.put(dict(op='wait-owner',instance=TOKEN,supervisor=list(IDENTITY),wait_owner=list(wait_id)),(510099,))
            def getsockopt(self,*_args):return struct.pack('3i',peer_pid,peer_uid,0)
            def sendmsg(self,data,ancillary):
                size=super().sendmsg(data,ancillary);value,fds=self.sent[-1]
                if value['op']=='launch':
                    n.control.put(value,range(520001,520006));server.bootstrap()
                    response,_fds=n.control.sent[-1];self.put(response,range(510001,510009))
                elif value['op']=='primary-launch':
                    n.control.put(value,tuple(fds));server.packet()
                    response,received_fds=n.control.sent[-1]
                    if not self.lose_primary_ack:
                        # SCM_RIGHTS creates a receiver-local descriptor.
                        local_fd=530100
                        n.ops.alias[local_fd]=H.FD('primary',1)
                        self.put(response,(local_fd,))
                elif value['op']=='primary-commit':
                    n.owner_ack=True;n.control.put(value);server.packet()
                    self.put(n.control.sent[-1][0])
                elif value['op']=='close':
                    receipt=server.finish();n.ops.exit(n.ops.nodes['platform-root']);n.ops.exit(n.ops.nodes['wait-worker'])
                    # The ordinary frontend positive models actual helper collection.
                    # Adverse controls separately leave the exited task uncollected.
                    n.ops.nodes['wait-worker'].reaped=True
                    self.put(envelope(receipt))
                return size
        client=Client();client.envelope=envelope;fake_os=types.SimpleNamespace(getuid=lambda:1000,geteuid=lambda:1000,getpid=lambda:n.ops.nodes['sdk'].pid,pidfd_open=lambda pid:510000,
            fstat=lambda fd:types.SimpleNamespace(st_dev=1,st_ino=fd,st_mode=0o100600),close=n.ops.close,stat=H.deny)
        def left(deadline):
            if deadline<=n.ops.now():raise TimeoutError('memory budget')
            return deadline-n.ops.now()
        def choose(fds,*_args):
            if client in fds and client.queue:return [client],[],[]
            n.ops.advance(.01);return [],[],[]
        def leaf(*fds):
            value=H.Boundary(n.ops,outer=fds[0]==510001);value.identity=(11,101 if value.outer else 102)
            value.fds=lambda:fds
            return value
        with ExitStack() as stack:
            stack.enter_context(H.replace(s,os=fake_os,pwd=types.SimpleNamespace(getpwuid=lambda _uid:types.SimpleNamespace(pw_name='test')),
                socket=types.SimpleNamespace(AF_UNIX=1,SOCK_SEQPACKET=5,SOCK_CLOEXEC=0,SO_PEERCRED=17,SOL_SOCKET=1,SCM_RIGHTS=1,MSG_CMSG_CLOEXEC=socket.MSG_CMSG_CLOEXEC,MSG_TRUNC=socket.MSG_TRUNC,MSG_CTRUNC=socket.MSG_CTRUNC,CMSG_SPACE=socket.CMSG_SPACE,socket=lambda *_args:client),
                select=types.SimpleNamespace(select=choose),remaining=left,CgroupBoundary=leaf))
            p=s.SupervisedPlatform(TOKEN,manager,n.ops);p._installed=lambda:None
            if launch:
                p.prepare();custodian=types.SimpleNamespace(owner_pidfd=None);p.set_custodian(custodian)
                process,fd,boundary=p.launch_owner(None,{},530000,*[types.SimpleNamespace(fileno=lambda i=i:i) for i in range(530001,530005)],'/user/private_bus.py')
                custodian.owner_pidfd=fd;p.claim_main(custodian)
            yield p,n,manager,client,server
    def test_default_constructor_final_releases_are_one_root_supervised_transaction(self):
        for fault in ('none','eintr','cancel','multiple'):
            with self.subTest(fault=fault),self.frontend(launch=False) as (p,n,manager,client,server):
                module=self.candidate.bus;controls=[];attempts=[];sockets=[]
                class Control:
                    def __init__(self,number,data=b''):self.number,self.data,self.closed=number,data,False
                    def fileno(self):return -1 if self.closed else self.number
                    def recv(self,_size):data,self.data=self.data,b'';return data
                    def close(self):
                        if self.closed:return
                        self.closed=True;attempts.append(('socket',self.number))
                        if fault=='multiple' and self.number==550003:raise InterruptedError('late temporary close')
                def pair(**_kw):
                    number=550000+len(controls)*2
                    first=Control(number,b'{"address":"unix:path=/inert","pid":1000000010}\n' if not controls else b'ready')
                    second=Control(number+1);controls.append((first,second));sockets.extend((first,second));return first,second
                class Watcher:
                    ident=None
                    def __init__(self,*,target,args,**_kw):self.target,self.args,self.live=target,args,False
                    def start(self):self.live=True
                    def join(self,timeout):
                        if self.live:self.target(*self.args)
                        self.live=False
                    def is_alive(self):return self.live
                def close(fd):
                    attempts.append(('fd',fd));n.ops.close(fd)
                    if fd==550110 and fault!='none':
                        if fault=='cancel':raise KeyboardInterrupt('post-release constructor cancellation')
                        raise InterruptedError(errno.EINTR,'post-release constructor EINTR')
                def fstat(fd):
                    if fd in n.ops.closed:raise OSError(errno.EBADF,'inert released descriptor')
                    return types.SimpleNamespace(st_dev=1,st_ino=fd,st_mode=0o100600) if isinstance(fd,int) else n.ops.fstat(fd)
                fake_os=types.SimpleNamespace(getpid=lambda:n.ops.nodes['sdk'].pid,pidfd_open=lambda _pid:550110,
                    fstat=fstat,close=close,dup=lambda _fd:550101,O_CLOEXEC=0,pipe2=lambda _flags:(H.FD('gate-read'),H.FD('gate-write')))
                n.ops.alias[550100]=H.FD('guardian');n.ops.alias[550101]=H.FD('platform-root',2)
                n.ops.alias[550110]=H.FD('sdk',2);n.ops.node('guardian','cleanup-owner',resistant=False)
                bus=module.PrivateBus.__new__(module.PrivateBus);error=None
                with H.replace(module,os=fake_os,LinuxProcesses=lambda:n.ops,PlatformScope=lambda _token:p,
                    secrets=types.SimpleNamespace(token_hex=lambda _n:TOKEN),socket=types.SimpleNamespace(socketpair=pair,SOCK_SEQPACKET=5),
                    threading=types.SimpleNamespace(RLock=H.DummyLock,Event=H.DummyEvent,Thread=Watcher),
                    time=types.SimpleNamespace(monotonic=n.ops.now),select=types.SimpleNamespace(select=lambda fds,*_args:([fd for fd in fds if getattr(fd,'data',None)],[],[])),
                    send_boundary=lambda *_args:None,receive_pidfd=lambda *_args:(b'1000000011',550100)):
                    try:module.PrivateBus.__init__(bus,None,{})
                    except H.NativeOperationForbidden:raise
                    except BaseException as problem:error=problem
                    if fault=='none':self.assertIsNone(error);bus.close()
                    else:
                        self.assertIsNotNone(error);self.assertIs(getattr(error,'resource',None),bus)
                        if fault=='cancel':self.assertIsInstance(error,KeyboardInterrupt)
                        if not bus.closed:bus.close()
                    self.assertTrue(bus.closed and p.closed);self.assertIs(bus.platform,p)
                    self.assertEqual(attempts.count(('fd',550110)),1)
                    self.assertTrue(all(control.closed for control in sockets));self.assertIsNone(bus._watcher)
                    self.assertFalse(n.ops.survivors());self.assertTrue(n.ops.nodes['foreign'].live and n.ops.nodes['unrelated'].live)
    def test_retained_bootstrap_response_retries_without_reopening_service(self):
        with self.frontend() as (p,n,manager,client,server):
            packet=p.bootstrap_packet;p.bootstrap_packet=None;p.boundary=None;p.handles.register(packet[1])
            p.bootstrap_packet=packet;p.custodian.owner_pidfd=None;p.handles.add(519000)
            p.retire();self.assertTrue(p.closed);self.assertEqual(p.handles.fds,[])
            self.assertEqual(n.ops.closed.count(519000),1);self.assertFalse(n.ops.survivors())
    def test_lost_primary_ack_resolves_only_from_bound_root_wait_proof(self):
        with self.frontend() as (p,n,manager,client,server):
            client.lose_primary_ack=True
            with self.assertRaises(TimeoutError):
                p.launch_primary(['/usr/bin/inert-app'],{'XDG_SESSION_ID':'42','PLEB_DESKTOP_SESSION_ID':'42'},
                                 '/inert',(520101,520102,520103,520104),'f'*32)
            self.assertTrue(p.primary_requested);self.assertIsNone(p.primary_fd)
            self.assertIsNotNone(n.primary_fd);self.assertFalse(n.primary_gate_committed)
            receipt=p.finish();self.assertTrue(receipt['bound']);self.assertIn('primary',n.ops.waited)
            p.retire();n.ops.close(510000)
    def test_bootstrap_exit_alone_cannot_retire_a_sent_launch(self):
        with self.frontend() as (p,n,manager,client,server):
            p.boundary=None;p.bootstrap_packet=None;client.queue.clear();client.close()
            client.sendmsg=lambda *_args:(_ for _ in ()).throw(BrokenPipeError('lost root receipt'))
            n.ops.exit(n.ops.nodes['platform-root'])
            with self.assertRaises((TimeoutError,self.candidate.own.CleanupIncomplete)):p.retire()
            self.assertFalse(p.closed);self.assertIsNone(p.receipt);self.assertTrue(n.ops.nodes['foreign'].live)
    def test_failed_owner_birth_before_fd_response_still_requires_root_waits(self):
        with self.frontend() as (p,n,manager,client,server):
            p.handles.register(p.bootstrap_packet[1]);p.boundary=None;p.bootstrap_packet=None
            p.retire();self.assertTrue(p.closed);self.assertEqual(p.receipt['source'],'ancestor-waits')
            self.assertEqual(p.remote_boundary_identity,n.boundary.identity);self.assertFalse(n.ops.survivors())
            self.assertTrue(n.ops.nodes['foreign'].live and n.ops.nodes['unrelated'].live);n.ops.close(510000)
    def test_no_birth_certificate_is_generated_only_after_empty_waits_and_retirement(self):
        for empty in (False,True):
            n=MemoryNative(self.candidate,depth=0,primary=False);server=self.candidate.server.SupervisorServer(n)
            if empty:n.ops.nodes.pop('cleanup-owner')
            if not empty:
                with self.assertRaises(self.candidate.own.CleanupIncomplete):server.finish()
                self.assertIsNone(server.receipt);self.assertFalse(n.boundary.outer.retired)
            else:
                value=server.finish();self.assertEqual(value['op'],'unlaunched');self.assertTrue(value['no_descendant_birth'] and value['adoption_drained'])
                self.assertTrue(n.boundary.outer.retired);self.assertTrue(n.ops.nodes['foreign'].live and n.ops.nodes['unrelated'].live)
    def test_full_sdk_frontend_handoff_close_parent_wait_and_retry(self):
        with self.frontend() as (p,n,manager,client,server):
            self.assertIsNotNone(p.identity);self.assertEqual(p.handles.fds,[])
            pid,fd=p.launch_primary(['/usr/bin/inert-app'],{'XDG_SESSION_ID':'42','PLEB_DESKTOP_SESSION_ID':'42',
                'DISPLAY':':71','DBUS_SESSION_BUS_ADDRESS':'unix:path=/private'},'/inert',
                (520101,520102,520103,520104),'1'*32)
            self.assertEqual(pid,n.primary_pid);self.assertEqual(n.ops.fd(fd).identity,n.ops.fd(n.primary_fd).identity)
            self.assertFalse(n.primary_gate_committed)
            p.commit_primary('1'*32);self.assertTrue(n.primary_gate_committed)
            receipt=p.finish();self.assertTrue(receipt['bound']);self.assertIn('primary',n.ops.waited)
            self.assertTrue(n.ops.nodes['foreign'].live and n.ops.nodes['unrelated'].live)
            manager.faults=1;p.retire();self.assertTrue(manager.reconnected and p.closed and manager.closed)
            self.assertNotIn(510000,n.ops.closed)  # The PrivateBus caller still owns this exact main FD.
            n.ops.close(510000);p.retire()
    def test_sdk_manager_replacement_retires_local_caps_but_never_stops_new_service(self):
        with self.frontend() as (p,n,manager,client,server):
            p.finish();manager.replacement=True
            with self.assertRaises(self.candidate.own.CleanupIncomplete):p.retire()
            self.assertFalse(p.closed);self.assertTrue(p._boundary_retired);self.assertIsNone(p.boundary)
            self.assertTrue(n.ops.nodes['foreign'].live);manager.replacement=False;p.retire();self.assertTrue(p.closed)
    def test_sdk_frontend_rejects_wrong_kernel_peer_before_launch(self):
        for uid,pid in ((1000,IDENTITY[1]),(0,1000000900)):
            with self.assertRaises(self.candidate.supervision.PlatformUnsupported):
                with self.frontend(uid,pid):self.fail('unauthenticated peer launched owner')
    def test_sdk_preflight_failure_releases_transport_without_unit_calls(self):
        s=self.candidate.supervision
        with H.replace(s,os=types.SimpleNamespace(getuid=lambda:1000,geteuid=lambda:1000),pwd=types.SimpleNamespace(getpwuid=lambda _uid:types.SimpleNamespace(pw_name='test'))):
            manager=types.SimpleNamespace(closed=False,unit=H.deny)
            manager.close=lambda:setattr(manager,'closed',True)
            p=s.SupervisedPlatform(TOKEN,manager,H.Model());p.retire();self.assertTrue(p.closed and manager.closed)
    def test_sdk_cancelled_return_and_partial_fd_transfer_keep_one_custodian(self):
        with self.frontend() as (p,n,manager,client,server):
            p.custodian.owner_pidfd=None;p.handles.add(519000)
            # Use distinct synthetic allocations. The registry is the only
            # close owner even when recipient fields alias these capabilities.
            p.handles.add(519001);p.handles.add(519005)
            p.retire();self.assertTrue(p.closed);self.assertIsNone(p.pidfd);self.assertEqual(p.handles.fds,[])
            self.assertEqual(n.ops.closed.count(519000),1)
            self.assertEqual(n.ops.closed.count(519001),1)
            self.assertEqual(n.ops.closed.count(519005),1)
            self.assertTrue(n.ops.nodes['foreign'].live and n.ops.nodes['unrelated'].live)
    def test_same_primary_launch_retry_is_idempotent_and_request_bound(self):
        with self.frontend() as (p,n,manager,client,server):
            args=(['/usr/bin/inert-app'],{'XDG_SESSION_ID':'42','PLEB_DESKTOP_SESSION_ID':'42'},
                  '/inert',(520201,520202,520203,520204),'2'*32)
            first=p.launch_primary(*args);sent=len(client.sent)
            self.assertEqual(p.launch_primary(*args),first);self.assertEqual(len(client.sent),sent)
            with self.assertRaises(RuntimeError):
                p.launch_primary(['/usr/bin/other'],args[1],args[2],args[3],args[4])
            self.assertEqual(n.primary_pid,first[0]);p.finish();p.retire();n.ops.close(510000)
    def test_owner_loss_fault_remains_observable_after_ancestor_recovery(self):
        server,n=self.server();server.trigger='owner-loss';value=self.proof(server,n)
        s=self.candidate.supervision;platform=types.SimpleNamespace(primary_process=None,primary_bound=True,finish=lambda *_args,**_kw:value)
        cleanup=s.SupervisedCleanup(TOKEN,n.boundary,n.ops,types.SimpleNamespace(platform=platform),None,None)
        cleanup.observe_exit();self.assertIsNotNone(cleanup.fault);self.assertIsNone(cleanup.receipt)
        with self.assertRaisesRegex(self.candidate.own.CleanupIncomplete,'recovered-fault'):cleanup.finish()
        self.assertEqual(cleanup.finish(),value);self.assertIsNone(cleanup.fault)
    def stream(self):
        module=stream_source(self.candidate.root);events=[]
        raw=types.SimpleNamespace(pid=1000000090,returncode=None,_waitpid_lock=H.DummyLock(),stdin=None,stdout=None,stderr=None)
        raw.poll=lambda:events.append('numeric-poll') or 0;raw.wait=lambda **_kw:events.append('numeric-wait') or 0
        raw.terminate=lambda:events.append('numeric-term');raw.kill=lambda:events.append('numeric-kill')
        ops=types.SimpleNamespace(now=lambda:0,wait_exact=lambda fd:types.SimpleNamespace(si_code=os.CLD_EXITED,si_status=0),ready=lambda *_args:True,sleep=lambda _delta:None,close=lambda fd:events.append(('fd-close',fd)),signal=lambda fd,sig:events.append(('exact-signal',fd,sig)))
        pin=self.candidate.pin.PidfdProcess(raw,495001,ops)
        class Sink:
            def __enter__(self):return self
            def __exit__(self,*_args):pass
            def write(self,data):events.append(('pidfile',data))
        module.subprocess=types.SimpleNamespace(Popen=lambda *_args,**_kw:raw,run=H.deny)
        module.os=types.SimpleNamespace(path=types.SimpleNamespace(join=lambda path,name:path+'/'+name),unlink=lambda path:events.append(('unlink',path)),close=H.deny)
        module.json=json;module._private_open=lambda *_args:Sink();module._proc_start_time=lambda pid:'foreign-replacement-start'
        module.atexit=types.SimpleNamespace(unregister=lambda *_args:None);module.time=types.SimpleNamespace(time=lambda:0)
        module._safe_runtime_dir=lambda *_args:False;module._close_handles=lambda handles:[h.close() for h in handles]
        supervisor=module.StreamSupervisor.__new__(module.StreamSupervisor);supervisor.children=[];supervisor.runtime_dir='/inert';supervisor.runtime_root='/inert'
        supervisor._cleaned=False;supervisor._locks=[];supervisor._pulse_modules=[]
        return module,supervisor,raw,pin,events
    def test_stream_exact_primary_never_publishes_reusable_pidfile(self):
        module,supervisor,raw,pin,events=self.stream()
        self.assertIs(supervisor.spawn('app',['inert'],_kilix_pin_receiver=lambda _raw:pin),pin)
        self.assertIsNone(supervisor.children[0][3]);self.assertEqual(events,[])
        supervisor.cleanup();self.assertEqual(events,[])
    def test_stream_failed_pin_retry_never_uses_numeric_poll_signal_or_wait(self):
        module,supervisor,raw,pin,events=self.stream();attempts=[]
        def receiver(process):
            attempts.append(process)
            if len(attempts)==1:raise self.candidate.own.CleanupIncomplete('inert transient handoff')
            return pin
        with self.assertRaises(self.candidate.own.CleanupIncomplete):supervisor.spawn('app',['inert'],_kilix_pin_receiver=receiver)
        self.assertIsNone(supervisor.children[0][3]);supervisor.cleanup()
        self.assertEqual(events,[]);self.assertEqual(attempts,[raw,raw]);self.assertFalse(pin._kilix_unpinned_gated)
    def test_remote_stream_descriptors_retry_by_retained_identity(self):
        module=stream_source(self.candidate.root);events=[];next_fd={'value':3000};fail_once={3000}
        class LocalStream:
            def __init__(self,fd,mode):self.fd,self.mode,self.closed=fd,mode,False
            def fileno(self):return self.fd
            def close(self):self.closed=True;events.append(('stream-close',self.fd))
        def pipe2(_flags):
            fd=next_fd['value'];next_fd['value']+=2;events.append(('pipe',fd));return fd,fd+1
        def fstat(fd):return types.SimpleNamespace(st_dev=1,st_ino=fd,st_mode=0o100600)
        def close(fd):
            events.append(('fd-close',fd))
            if fd in fail_once:
                fail_once.remove(fd);raise getattr(self.candidate.own,'DescriptorCloseCancelled',KeyboardInterrupt)('inert pre-operation cancellation')
        fake_os=types.SimpleNamespace(O_CLOEXEC=1,O_RDONLY=0,O_WRONLY=1,environ={},devnull='/dev/null',
            pipe2=pipe2,fstat=fstat,close=close,fdopen=lambda fd,mode,buffering=0:LocalStream(fd,mode),
            dup=lambda fd:events.append(('dup',fd)) or 3004)
        fake_process=types.SimpleNamespace(pid=1000000200,returncode=0,_kilix_primary_pin=object(),
            poll=lambda:0,wait=lambda **_kwargs:0)
        def launcher(argv,*,env,cwd,stream_fds):
            events.append(('launch',tuple(stream_fds),env,cwd));return fake_process
        supervisor=module.StreamSupervisor.__new__(module.StreamSupervisor)
        supervisor.children=[];supervisor._pending_launch_fds=[];supervisor._pending_launch_allocations=[];supervisor._cleaned=False
        supervisor._locks=[];supervisor._pulse_modules=[];supervisor.runtime_root='/inert';supervisor.runtime_dir='/inert'
        for key in ('os','subprocess','time','atexit','_safe_runtime_dir','_close_handles'):
            if not hasattr(module,key):setattr(module,key,None)
        with H.replace(module,os=fake_os,subprocess=__import__('subprocess'),time=types.SimpleNamespace(time=lambda:0),
            atexit=types.SimpleNamespace(unregister=lambda *_args:None),_safe_runtime_dir=lambda *_args:False,
            _close_handles=lambda _handles:None,
            CheckedDescriptor=lambda fd,filesystem:self.candidate.own.CheckedDescriptor(fd,filesystem,raw=False)):
            with self.assertRaises(self.candidate.own.CleanupIncomplete):
                supervisor.spawn('primary',['/usr/bin/inert-app'],env={'XDG_SESSION_ID':'42'},cwd='/inert',
                    stdin=__import__('subprocess').PIPE,stdout=__import__('subprocess').PIPE,
                    stderr=__import__('subprocess').STDOUT,_kilix_remote_launcher=launcher)
            self.assertEqual(events[[row[0] for row in events].index('launch')][1],(3000,3003,3004))
            self.assertEqual(len(supervisor.children),1);self.assertEqual(len(supervisor._pending_launch_fds),1)
            self.assertEqual(supervisor._pending_launch_fds[0].fd,3000)
            supervisor.cleanup()
        self.assertEqual(events.count(('fd-close',3000)),2)
        self.assertTrue(fake_process.stdin.closed and fake_process.stdout.closed)
        self.assertEqual(supervisor._pending_launch_fds,[]);self.assertTrue(supervisor._cleaned)
    def test_login_and_hello_cannot_select_foreign_authority(self):
        s=self.candidate.supervision;self.assertEqual(s.decode_instance(s.instance_name('user-with-dash',TOKEN)),('user-with-dash',TOKEN))
        for login in ('root','../user','user:other','user/x','.'):
            with self.assertRaises(ValueError):s.instance_name(login,TOKEN)
        value=dict(version=2,op='launch',instance=TOKEN,login='test',sdk=1000000090,source='/user/private_bus.py',env={})
        for change in (dict(login='foreign'),dict(sdk=1000000001),dict(env={'X':'a\x00b'})):
            with self.assertRaises(ValueError):s.validate_hello(dict(value,**change),'test',TOKEN,1000000090,1000,1000)
        with self.assertRaises(ValueError):s.validate_hello(value,'test',TOKEN,1000000090,1001,1000)
    def record(self,platform):
        s=self.candidate.supervision
        return dict(Id=platform.name,FragmentPath=s.FRAGMENT,User='root',Type='exec',ExitType='main',Restart='no',Delegate=True,KillMode='control-group',CollectMode='inactive-or-failed',SendSIGKILL=True,ExecStart=[('/usr/bin/python3',['/usr/bin/python3','-I',s.installed_program(Path(s.__file__).parent),platform.instance])],InvocationID=bytes.fromhex(IDENTITY[0]),MainPID=IDENTITY[1],ControlGroup=IDENTITY[2],ActiveState='active')
    def test_service_rejects_mutable_exec_unsafe_user_and_replacement(self):
        s=self.candidate.supervision;p=s.SupervisedPlatform.__new__(s.SupervisedPlatform);p.instance='test-'+TOKEN;p.name=s.PREFIX+p.instance+'.service';p.identity=None;record=self.record(p)
        self.assertEqual(p._service(record),IDENTITY)
        for key,value in (('User','test'),('ExecStart',[('/user/program',[])]),('Delegate',False),('KillMode','process'),('FragmentPath','/user/alias.service'),('Restart','always'),('ControlGroup','/system.slice/foreign.service')):
            with self.subTest(key=key),self.assertRaises(s.PlatformUnsupported):p._service(dict(record,**{key:value}))
        p.identity=IDENTITY
        with self.assertRaises(s.PlatformUnsupported):p._service(dict(record,InvocationID=b'2'*16))
    def test_root_manager_only_starts_fixed_unit_without_properties(self):
        s=self.candidate.supervision;m=s.RootManager.__new__(s.RootManager);m.system,m.record=object(),':1.1';calls=[];m._call=lambda *args:calls.append(args)
        m.start(s.PREFIX+'test-'+TOKEN+'.service',1);self.assertEqual(calls[0][4],'StartUnit');self.assertEqual(calls[0][5],(s.PREFIX+'test-'+TOKEN+'.service','fail'))
    def test_root_manager_reexec_reattests_pid1_credentials(self):
        s=self.candidate.supervision
        for uid,pid in ((0,1),(1000,1),(0,9)):
            m=s.RootManager.__new__(s.RootManager);m.system=object();m._call=lambda _c,_d,_p,_i,method,_args,_t:({'GetNameOwner':':1.2','GetConnectionUnixUser':uid,'GetConnectionUnixProcessID':pid}[method])
            if (uid,pid)==(0,1):self.assertEqual(m._root_identity(1),':1.2')
            else:
                with self.assertRaises(s.PlatformUnsupported):m._root_identity(1)
    def test_supervisor_exit_is_not_a_wait_certificate(self):
        s=self.candidate.supervision;n=MemoryNative(self.candidate);n.ops.node('platform-root','pid1',scope='supervisor');n.ops.exit(n.ops.nodes['platform-root'])
        p=s.SupervisedPlatform.__new__(s.SupervisedPlatform);p.ops,p.pidfd,p.boundary=n.ops,H.FD('platform-root'),n.boundary;p.control=None;p.receipt=None;p.token=TOKEN;p.identity=IDENTITY;p.handles=s.Handles(n.ops.close);p.launch_attempted=False;p.primary_process=None
        with self.assertRaises(self.candidate.own.CleanupIncomplete):p.finish(H.FD('primary'))
        self.assertIsNone(p.receipt);self.assertTrue(n.ops.nodes['foreign'].live)
    def test_versioned_installer_and_narrow_per_instance_policy(self):
        installer=H.load_module('loss_installer',ROOT/'scripts/install-private-bus-supervisor.py');records=installer.plan(ROOT);directory=Path(self.candidate.supervision.installed_program(ROOT/'config/kilix_sdk')).parent
        self.assertEqual({Path(r['target']).parent for r in records[:4]},{directory});template=records[-2]['data'].decode();rule=records[-1]['data'].decode()
        self.assertIn(str(directory/'bus_supervisor.py'),template);self.assertNotIn('[Install]',template);self.assertIn('NoNewPrivileges=yes',template)
        self.assertIn('match[1] === subject.user',rule);self.assertIn('action.lookup("verb") === "start"',rule);self.assertNotIn('polkit.spawn',rule)
        install=(PLEB/'lib/install.sh').read_text().split('do_install() {',1)[1];self.assertLess(install.index('install-private-bus-supervisor.py'),install.index('"$PLEB_BIN_SRC" "$SESSION_BIN_DST"'));self.assertIn('polkitd',(OS_ROOT/'provision/install-deps.sh').read_text())
    def test_installer_failure_cancellation_rolls_back_exact_files(self):
        installer=H.load_module('loss_installer_rollback',ROOT/'scripts/install-private-bus-supervisor.py');records=installer.plan(ROOT)
        for failure in (RuntimeError('reload'),KeyboardInterrupt('cancel')):
            old={r['target']:None for r in records}
            for r in records[-2:]:old[r['target']]=b'old'
            old['/foreign']=b'unrelated';state=dict(old);calls=[]
            class FS:
                def read_safe(self,path):return state.get(path)
                def replace(self,path,data,_mode):state[path]=data
                def remove(self,path):state[path]=None
            def reload():
                calls.append(1)
                if len(calls)==1:raise failure
            with self.assertRaises(installer.InstallFailure):installer.deliver(records,FS(),types.SimpleNamespace(reload=reload))
            self.assertEqual(state,old);self.assertEqual(len(calls),2)

def run(suite,path):
    with path.open('x') as log:return unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
result=run(unittest.defaultTestLoader.loadTestsFromTestCase(LossTests),OUT/'tests.log')
report=dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),bindings=H.BINDINGS,mutants=[],native_effects=0)
(OUT/'REPORT.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='bindings'}))
if not result.wasSuccessful():print((OUT/'tests.log').read_text());raise SystemExit(1)
mutations=(
 ('skip-adoption-wait','bus_ownership.py','and ops.empty_children()','and True','test_delayed_adoption_wait_certificate_is_required'),
 ('skip-primary-reap','bus_supervision.py','while not self.cleanup.ops.reaped(self.primary):','while False:','test_primary_exit_without_reaping_is_incomplete'),
 ('accept-foreign-receipt','bus_supervision.py','or value["instance"] != token or value["boundary"] != list(boundary)','or False or value["boundary"] != list(boundary)','test_receipt_rejects_foreign_stale_cgroup_only_or_partial_proof'),
 ('accept-empty-only','bus_supervision.py','or value["supervisor"] != list(supervisor) or value["source"] != "ancestor-waits"','or value["supervisor"] != list(supervisor)','test_receipt_rejects_foreign_stale_cgroup_only_or_partial_proof'),
 ('accept-caller-born-primary','bus_supervisor.py','raise ValueError("primary must be born behind the independent ancestor gate")','return None','test_caller_born_or_foreign_primary_protocol_never_becomes_authority'),
 ('commit-after-sdk-loss','bus_supervisor.py','if self.ops.ready(self.sdk_fd) or self.ops.ready(self.owner_fd):\n            raise CleanupIncomplete("primary-controller-lost-before-exec", resource=self)','if False:\n            raise CleanupIncomplete("primary-controller-lost-before-exec", resource=self)','test_primary_commit_refuses_sdk_loss_without_releasing_gate'),
 ('allow-mutable-root-command','bus_supervision.py','or record.get("ExecStart") != [("/usr/bin/python3", argv)]','or False','test_service_rejects_mutable_exec_unsafe_user_and_replacement'),
 ('ignore-service-replacement','bus_supervision.py','if self.identity is not None and identity != self.identity:','if False:','test_service_rejects_mutable_exec_unsafe_user_and_replacement'),
 ('reopen-reaped-bootstrap','private_bus.py','if getattr(self.owner_process, "returncode", None) is not None:\n                return','self.owner_pidfd = os.pidfd_open(self.owner_process.pid)\n            if getattr(self.owner_process, "returncode", None) is not None:\n                return','test_missing_bootstrap_fd_and_concurrent_reaper_never_open_pid'),
 ('retry-replacement-descriptor','bus_ownership.py','if (st.st_dev, st.st_ino, getattr(st, "st_mode", 0)) != self.identity:','if False:','test_cancelled_close_never_retries_replacement_fd'),
 ('keep-late-released-fd','bus_supervision.py','                    self.transfer(fd)\n','                    pass\n','test_late_release_is_invalidated_and_preoperation_failure_retained'),
 ('grant-no-workload-migration','bus_supervisor.py','os.fchown(leaf.join_fd, self.uid, 0)','pass','test_native_allocation_delegates_only_owned_workload_migration_files'),
 ('accept-nonroot-supervisor-peer','bus_supervision.py','if uid != 0 or pid != self.identity[1] or self.ops.ready(self.pidfd):','if pid != self.identity[1] or self.ops.ready(self.pidfd):','test_sdk_frontend_rejects_wrong_kernel_peer_before_launch'),
 ('accept-readable-zombie-as-reaped','bus_ownership.py','and values == ["-1"]','and True','test_linux514_readable_zombie_is_not_reaping_evidence'),
 ('reintroduce-primary-pidfile','../stream.py','None if getattr(p, "_kilix_primary_pin", None) is not None else','None if False else','test_stream_exact_primary_never_publishes_reusable_pidfile'),
 ('hide-recovered-owner-fault','bus_supervision.py','if not self.fault_reported and value["trigger"] in','if False and value["trigger"] in','test_owner_loss_fault_remains_observable_after_ancestor_recovery'),
 ('lend-root-signal-capability','bus_supervisor.py','if os.getresuid() != (self.uid, self.uid, 0) or int(fields.get("CapEff", "-1"), 16) != 0:\n                raise CleanupIncomplete("ancestor-signal-credentials", resource=self)','if False:\n                raise CleanupIncomplete("ancestor-signal-credentials", resource=self)','test_root_signal_uses_caller_real_and_effective_uid_without_capability'),
)
for i,(name,filename,old,new,test) in enumerate(mutations):
    target=OUT/'mutants'/name/'config/kilix_sdk';target.mkdir(parents=True)
    for item in ('bus_ownership.py','bus_platform.py','bus_supervision.py','bus_supervisor.py','process_pin.py','private_bus.py','xapp.py'):
        data=(ROOT/'config/kilix_sdk'/item).read_text()
        if item==filename:
            count=2 if name=='skip-adoption-wait' else 1
            assert data.count(old)==count,(name,'exact anchors')
            data=data.replace(old,new,count)
        (target/item).write_text(data)
    data=(ROOT/'config/stream.py').read_text()
    if filename=='../stream.py':assert data.count(old)==1;data=data.replace(old,new,1)
    (target.parent/'stream.py').write_text(data)
    LossTests.candidate=source(target.parents[1],'loss_mutant_'+str(i));outcome=run(unittest.TestSuite([LossTests(test)]),OUT/(name+'.log'))
    report['mutants'].append(dict(name=name,test=test,failures=len(outcome.failures),errors=len(outcome.errors),sha256=H.digest((target/filename).read_bytes())))
    assert outcome.failures and not outcome.errors,(name,(OUT/(name+'.log')).read_text())
LossTests.candidate=CURRENT;report['bindings']=H.BINDINGS
(OUT/'REPORT.json').write_text(json.dumps(report,indent=2)+'\n');print(str(len(report['mutants']))+' new semantic mutants rejected by assertions')
