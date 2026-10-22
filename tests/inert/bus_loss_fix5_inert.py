"""Full lifecycle additions: actual source methods and inert effect boundaries.

No native effects. Unknown kernel outcomes remain unresolved; only typed
no-effect refusals authorize retry. The frozen review challenge is also retained.
"""
import ast, dis, errno, hashlib, json, struct, sys, threading, types, unittest
from pathlib import Path
ROOT, OLD, PLEB, OS_ROOT, OUT = map(Path, sys.argv[1:6])
fixture=ROOT/'tests/inert/bus_loss_inert.py'
nodes=[]
for node in ast.parse(fixture.read_bytes(),filename=str(fixture)).body:
    if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='result' for t in node.targets):break
    nodes.append(node)
ns=dict(__name__='fix5_definitions',__file__=str(fixture))
exec(compile(ast.Module(body=nodes,type_ignores=[]),str(fixture),'exec'),ns)
H,C,F=ns['H'],ns['CURRENT'],ns['LossTests']()
observations=[]
def method(cls,name):
    path=Path(cls.__dict__[name].__code__.co_filename)
    c=next(n for n in ast.parse(path.read_bytes()).body if isinstance(n,ast.ClassDef) and n.name==cls.__name__)
    return next(n for n in c.body if isinstance(n,ast.FunctionDef) and n.name==name)
def at(function,line,call):
    fired=[]
    def trace(frame,event,arg):
        if frame.f_code is function.__code__ and event=='line' and frame.f_lineno==line and not fired:
            fired.append(line);raise KeyboardInterrupt('one-shot source boundary')
        return trace
    sys.settrace(trace)
    try:return call()
    finally:sys.settrace(None);observations.append(dict(function=function.__qualname__,line=line,fired=fired))
class FileModel:
    def __init__(self):
        self.current='original';self.identity=(11,12,0o100600);self.calls=[];self.foreign=[];self.problem=None
    def fstat(self,fd):
        if self.current is None:raise OSError(errno.EBADF,'inert released')
        d,i,m=self.identity;return types.SimpleNamespace(st_dev=d,st_ino=i,st_mode=m)
    def close(self,fd):
        self.calls.append((fd,self.current))
        if self.problem is not None:
            problem,self.problem=self.problem,None;raise problem
        if self.current=='replacement':self.foreign.append(fd)
        self.current=None
    def replacement(self,same):
        self.current='replacement'
        if not same:self.identity=(21,22,0o100600)
class Lifecycle(unittest.TestCase):
    def lease(self,raw=True):
        fs=FileModel();lease=C.own.CheckedDescriptor(610001,fs,raw=raw);lease.capture();return lease,fs
    def test_clean_raw_and_typed_once(self):
        for raw in (False,True):
            with self.subTest(raw=raw):
                lease,fs=self.lease(raw);lease.close();fs.replacement(True);lease.close()
                self.assertEqual(fs.calls,[(610001,'original')]);self.assertIsNone(lease.fd)
    def test_typed_refusal_preserves_original_then_retries(self):
        for error in (PermissionError(errno.EACCES,'typed pre-effect refusal'),getattr(C.own,'DescriptorCloseCancelled',KeyboardInterrupt)('typed before-close cancellation')):
            with self.subTest(error=type(error).__name__):
                lease,fs=self.lease(False);fs.problem=error
                with self.assertRaises(type(error)):lease.close()
                lease.close();self.assertIsNone(lease.fd);self.assertEqual(fs.foreign,[])
    def test_raw_late_error_and_typed_released_never_reclose(self):
        for raw in (False,True):
            with self.subTest(raw=raw):
                lease,fs=self.lease(raw)
                error=OSError(errno.EINTR,'Linux late error') if raw else C.own.DescriptorReleased('typed released')
                def close(fd):fs.calls.append((fd,fs.current));fs.current=None;raise error
                fs.close=close
                with self.assertRaises(C.own.DescriptorReleased):lease.close()
                fs.replacement(True);lease.close();self.assertEqual(len(fs.calls),1);self.assertEqual(fs.foreign,[])
    def test_ambiguous_before_effect_never_uses_identity_as_authority(self):
        lease,fs=self.lease();fs.problem=KeyboardInterrupt('raw outcome unknown')
        with self.assertRaises(KeyboardInterrupt):lease.close()
        with self.assertRaises(C.own.CleanupIncomplete):lease.close()
        self.assertEqual(len(fs.calls),1);self.assertEqual(lease.fd,610001)
    def test_post_effect_equal_and_distinct_object_reuse(self):
        for same in (False,True):
            with self.subTest(same=same):
                lease,fs=self.lease();node=method(C.own.CheckedDescriptor,'close')
                line=next(n for n in node.body if isinstance(n,ast.Try)).orelse[0].lineno
                with self.assertRaises(KeyboardInterrupt):at(C.own.CheckedDescriptor.close,line,lease.close)
                fs.replacement(same)
                try:lease.close()
                except C.own.CleanupIncomplete:pass
                self.assertEqual(fs.foreign,[]);self.assertEqual(len(fs.calls),1)
                if same:self.assertTrue(lease.uncertain);self.assertIsNotNone(lease.fd)
                else:self.assertIsNone(lease.fd)
    def test_missing_and_anonymous_identity_never_authorize_retry(self):
        for identity in (None,(11,12,0)):
            with self.subTest(identity=identity):
                lease,fs=self.lease();lease.identity=identity;fs.identity=(11,12,0);fs.problem=KeyboardInterrupt('unknown')
                with self.assertRaises(KeyboardInterrupt):lease.close()
                with self.assertRaises(C.own.CleanupIncomplete):lease.close()
                self.assertEqual(len(fs.calls),1)
    def test_retained_closer_publication_callback_cancellation(self):
        fs=FileModel();closer=C.own.RetainedCloser(fs.close,fs);owned=[610001]
        def cancelled():raise KeyboardInterrupt('after close before owner publication')
        with self.assertRaises(KeyboardInterrupt):C.own.release(610001,closer,cancelled)
        fs.replacement(True)
        C.own.release(610001,closer,lambda:owned.remove(610001))
        self.assertEqual(fs.foreign,[]);self.assertEqual(len(fs.calls),1);self.assertEqual(owned,[])
    def test_independent_handles_release_and_uncertain_incarnation_holds(self):
        for same in (True,False):
            with self.subTest(same=same):
                first=FileModel();second=FileModel();calls=[]
                def close(fd):
                    calls.append(fd)
                    if fd==610001:
                        first.current=None;first.replacement(same);raise KeyboardInterrupt('after first effect')
                    second.close(fd)
                fs=types.SimpleNamespace(fstat=lambda fd:(first if fd==610001 else second).fstat(fd),close=close)
                handles=C.supervision.Handles(close,fs);handles.register((610001,610002));handles.capture((610001,610002))
                with self.assertRaises(C.own.CleanupIncomplete):handles.close()
                try:handles.close()
                except C.own.CleanupIncomplete:pass
                self.assertEqual(calls,[610001,610002]);self.assertIsNone(second.current)
                if same:self.assertIn(610001,handles.fds)
                else:self.assertEqual(handles.fds,[])
    def test_composed_pending_and_authenticated_post_close_boundary(self):
        for pending in (False,True):
            with self.subTest(pending=pending),F.frontend(launch=False) as (p,n,m,*_):
                for key,node in list(n.ops.nodes.items()):
                    if node.instance=='A' and node.scope in ('outer','inner'):del n.ops.nodes[key]
                if pending:
                    original=p.handles.register
                    def register(fds):
                        if 510099 in fds:p.handles.register=original;raise KeyboardInterrupt('before registry publication')
                        return original(fds)
                    p.handles.register=register
                    with self.assertRaises(KeyboardInterrupt):p.prepare()
                else:p.prepare()
                n.ops.exit(n.ops.nodes['platform-root']);n.ops.nodes['platform-root'].reaped=True;m.unit=lambda *_:None
                n.ops.exit(n.ops.nodes['wait-worker']);n.ops.nodes['wait-worker'].reaped=True
                lease=p.wait_receive.leases[510099];fs=FileModel();fs.identity=lease.identity;lease.filesystem=fs
                line=next(n for n in method(C.own.CheckedDescriptor,'close').body if isinstance(n,ast.Try)).orelse[0].lineno
                with self.assertRaises(C.own.CleanupIncomplete):at(C.own.CheckedDescriptor.close,line,lambda:p.retire(timeout=.03))
                fs.replacement(False);p.retire(timeout=.03)
                self.assertEqual(fs.foreign,[]);self.assertTrue(p.closed);self.assertTrue(n.ops.nodes['foreign'].live)
    def status(self,status):
        n=C.server.NativeAncestor.__new__(C.server.NativeAncestor)
        n.primary_fd=620001;n.primary_status_fd=620002;n.primary_exec_status=None;n.primary_waited=False
        n._primary_wait_attempted=False;n._primary_wait_result=None;n._primary_status_state='pending';n._primary_status_lease=None
        waits=[];writes=[];closed=[]
        info=types.SimpleNamespace(si_code=C.server.os.CLD_EXITED if status>=0 else C.server.os.CLD_KILLED,si_status=abs(status))
        def wait(fd):
            waits.append(fd)
            if len(waits)>1:raise AssertionError('second exact wait')
            return info
        n._wait_exact=wait;n.handles=types.SimpleNamespace(release_one=closed.append)
        fs=types.SimpleNamespace(CLD_EXITED=C.server.os.CLD_EXITED,CLD_KILLED=C.server.os.CLD_KILLED,CLD_DUMPED=C.server.os.CLD_DUMPED,write=lambda fd,data:writes.append((fd,data)) or len(data))
        return n,fs,waits,writes,closed,info
    def test_zero_nonzero_signalled_and_dumped_status_consumer(self):
        for status in (0,7,123,-9,-15):
            for dumped in (False,True):
                with self.subTest(status=status,dumped=dumped):
                    n,fs,waits,writes,closed,info=self.status(status)
                    if dumped and status<0:info.si_code=fs.CLD_DUMPED
                    with H.replace(C.server,os=fs):
                        self.assertEqual(n.collect_primary(),status);self.assertEqual(n.collect_primary(),status)
                    self.assertEqual(waits,[620001]);self.assertEqual(writes,[(620002,struct.pack('!i',status))]);self.assertEqual(closed,[620002]);self.assertTrue(n.primary_waited)
                    reader=C.pin.RemoteProcess.__new__(C.pin.RemoteProcess);reader._lock=threading.RLock();reader.returncode=None;reader.status_fd=620003;reader.fd=620001;reader._status=bytearray();reader.ops=types.SimpleNamespace(reaped=lambda fd:True)
                    parts=[writes[0][1][:1],writes[0][1][1:]]
                    with H.replace(C.pin,select_ready=lambda fd:bool(parts),os=types.SimpleNamespace(read=lambda *_:parts.pop(0))):self.assertEqual(reader.poll(),status)
    def test_saved_status_publication_cancellation_resumes_delivery(self):
        for status in (0,7,-9):
            with self.subTest(status=status):
                n,fs,waits,writes,closed,_=self.status(status)
                line=next(n for n in method(C.server.NativeAncestor,'_record_primary_status').body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Attribute) and t.attr=='primary_waited' for t in n.targets)).lineno
                with H.replace(C.server,os=fs):
                    with self.assertRaises(KeyboardInterrupt):at(C.server.NativeAncestor._record_primary_status,line,n.collect_primary)
                    self.assertEqual(n.collect_primary(),status)
                self.assertEqual(waits,[620001]);self.assertEqual(len(writes),1);self.assertEqual(closed,[620002]);self.assertTrue(n.primary_waited)
    def test_known_no_write_errors_retry_cache_without_rewait(self):
        for status in (0,7,-9):
            for code in (errno.EAGAIN,errno.EINTR):
                with self.subTest(status=status,code=code):
                    n,fs,waits,writes,closed,_=self.status(status);original=fs.write
                    def refuse(fd,payload):fs.write=original;raise OSError(code,'known no-effect pipe refusal')
                    fs.write=refuse
                    with H.replace(C.server,os=fs):
                        with self.assertRaises(OSError):n.collect_primary()
                        self.assertEqual(n.collect_primary(),status);self.assertEqual(n.collect_primary(),status)
                    self.assertEqual(waits,[620001]);self.assertEqual(len(writes),1);self.assertEqual(closed,[620002])
    def test_before_delivery_source_cancellation_resumes_cache(self):
        n,fs,waits,writes,closed,_=self.status(7)
        if not hasattr(C.server.NativeAncestor,'_deliver_primary_status'):self.fail('cached delivery has no independently resumable transaction')
        node=method(C.server.NativeAncestor,'_deliver_primary_status');line=next(n for n in ast.walk(node) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='payload' for t in n.targets)).lineno
        with H.replace(C.server,os=fs):
            with self.assertRaises(KeyboardInterrupt):at(C.server.NativeAncestor._deliver_primary_status,line,n.collect_primary)
            self.assertEqual(n.collect_primary(),7)
        self.assertEqual(waits,[620001]);self.assertEqual(len(writes),1);self.assertEqual(closed,[620002])
    def test_unknown_and_short_write_never_resend_or_publish_success(self):
        for effect in ('before','after','short','unknown-oserror'):
            for status in (0,7,-9):
                with self.subTest(effect=effect,status=status):
                    n,fs,waits,writes,closed,_=self.status(status);attempts=[]
                    def unknown(fd,payload):
                        attempts.append(fd)
                        if effect=='after':writes.append((fd,payload));raise KeyboardInterrupt('unknown post-write')
                        if effect=='before':raise KeyboardInterrupt('unknown before-write')
                        if effect=='short':writes.append((fd,payload[:2]));return 2
                        raise OSError(errno.EIO,'unknown effect')
                    fs.write=unknown
                    with H.replace(C.server,os=fs):
                        try:n.collect_primary()
                        except BaseException:pass
                        with self.assertRaises(C.own.CleanupIncomplete):n.collect_primary()
                    self.assertEqual(waits,[620001]);self.assertEqual(attempts,[620002]);self.assertEqual(closed,[]);self.assertEqual(n.primary_exec_status,status);self.assertIsNotNone(n.primary_status_fd)
    def test_post_write_publication_boundary_retains_uncertainty(self):
        if not hasattr(C.server.NativeAncestor,'_deliver_primary_status'):self.fail('no delivery state')
        n,fs,waits,writes,closed,_=self.status(-9)
        node=method(C.server.NativeAncestor,'_deliver_primary_status');line=next(n for n in ast.walk(node) if isinstance(n,ast.Try) and n.orelse).orelse[0].lineno
        with H.replace(C.server,os=fs):
            with self.assertRaises(KeyboardInterrupt):at(C.server.NativeAncestor._deliver_primary_status,line,n.collect_primary)
            with self.assertRaises(C.own.CleanupIncomplete):n.collect_primary()
        self.assertEqual(waits,[620001]);self.assertEqual(len(writes),1);self.assertEqual(closed,[])
    def test_known_delivery_writer_refusal_retries_only_release(self):
        n,fs,waits,writes,closed,_=self.status(0);count=[]
        def refuse(fd):
            count.append(fd)
            if len(count)==1:raise PermissionError('typed writer close refusal')
            closed.append(fd)
        n.handles.release_one=refuse
        with H.replace(C.server,os=fs):
            with self.assertRaises(PermissionError):n.collect_primary()
            self.assertEqual(n.collect_primary(),0);self.assertEqual(n.collect_primary(),0)
        self.assertEqual(waits,[620001]);self.assertEqual(len(writes),1);self.assertEqual(closed,[620002])
    def test_cancelled_registry_removal_uses_original_writer_lease(self):
        n,fs,waits,writes,closed,_=self.status(7);files=FileModel();handles=C.supervision.Handles(files.close,files);handles.add(620002);n.handles=handles;n._primary_status_lease=handles.leases[620002]
        transfer=handles.transfer
        def cancelled(fd):transfer(fd);raise KeyboardInterrupt('after registry removal')
        handles.transfer=cancelled
        with H.replace(C.server,os=fs):
            with self.assertRaises(KeyboardInterrupt):n.collect_primary()
            files.replacement(True);self.assertEqual(n.collect_primary(),7)
        self.assertEqual(waits,[620001]);self.assertEqual(len(writes),1);self.assertEqual(len(files.calls),1);self.assertEqual(files.foreign,[]);self.assertIsNone(n.primary_status_fd)
    def test_unknown_wait_does_not_rewait_or_invent_integer(self):
        n,fs,waits,writes,closed,_=self.status(7)
        def unknown(fd):
            waits.append(fd)
            if len(waits)>1:raise AssertionError('rewait after ambiguous collection')
            raise KeyboardInterrupt('wait result lost')
        n._wait_exact=unknown
        with H.replace(C.server,os=fs):
            with self.assertRaises(KeyboardInterrupt):n.collect_primary()
            with self.assertRaises(C.own.CleanupIncomplete):n.collect_primary()
        self.assertEqual(waits,[620001]);self.assertEqual(writes,[]);self.assertEqual(closed,[]);self.assertIsNone(n.primary_exec_status)
    def test_wait_exact_adapter_cache_and_invalid_status(self):
        n,fs,waits,writes,closed,info=self.status(7)
        with H.replace(C.server,os=fs):
            self.assertIs(n.wait_exact_owned(620001),info);self.assertIs(n.wait_exact_owned(620001),info)
        self.assertEqual(waits,[620001]);self.assertEqual(len(writes),1)
        n,fs,waits,writes,closed,info=self.status(7);info.si_code=999
        with H.replace(C.server,os=fs):
            with self.assertRaises(C.own.CleanupIncomplete):n.collect_primary()
            with self.assertRaises(C.own.CleanupIncomplete):n.collect_primary()
        self.assertIsNone(n.primary_exec_status);self.assertEqual(writes,[]);self.assertEqual(waits,[620001])
    def test_retirement_preserves_status_obligation_and_releases_independent_handles(self):
        for kind in ('unknown-wait','unknown-delivery','known-delivery'):
            with self.subTest(kind=kind):
                n,fs,waits,writes,closed,info=self.status(7);files=FileModel()
                n.handles=C.supervision.Handles(files.close,files)
                n.handles.register((620001,620002,620003));n.handles.capture((620001,620002,620003))
                n._primary_status_lease=n.handles.leases[620002]
                n.restore_credentials=lambda:None;n.boundary=n.partial=n.root=None;n.delegation=types.SimpleNamespace(close_pending=lambda:None)
                n._primary_wait_attempted=True
                if kind!='unknown-wait':n.primary_exec_status=7;n.primary_waited=True;n._primary_status_state='uncertain' if kind=='unknown-delivery' else 'delivered'
                with H.replace(C.server,os=fs):
                    if kind=='known-delivery':n.retire();self.assertEqual(n.handles.fds,[])
                    else:
                        with self.assertRaises(C.own.CleanupIncomplete):n.retire()
                        self.assertEqual(n.handles.fds,[620001,620002]);self.assertEqual(files.calls,[(620003,'original')])
                self.assertEqual(waits,[]);self.assertEqual(writes,[])
    def closer(self,app):
        if app:
            obj=C.xapp.XAppSession('inert',10,10,supervisor=types.SimpleNamespace());attribute='_cleanup_active';closed='_closed';call='_close_resources'
        else:
            obj=C.bus.PrivateBus.__new__(C.bus.PrivateBus);obj.closed=False;obj._finalizer=None;obj._lock=threading.RLock();obj._close_receipt=lambda:{'state':'complete'};attribute='_finalizer';closed='closed';call='_close_attempt'
        calls=[]
        def callback():calls.append('cleanup');setattr(obj,closed,True);return None if app else {'state':'complete'}
        setattr(obj,call,callback)
        return obj,attribute,closed,call,calls
    def test_cancel_every_pre_and_post_finalizer_acquisition_line(self):
        for app in (False,True):
            cls=C.xapp.XAppSession if app else C.bus.PrivateBus;node=method(cls,'close')
            protect=next(n for n in node.body if isinstance(n,ast.Try));end=min(x.lineno for x in protect.finalbody)
            lines=sorted({line for _,line in dis.findlinestarts(cls.close.__code__) if line is not None and node.lineno<line<end})
            for line in lines:
                with self.subTest(app=app,line=line):
                    obj,attribute,closed,call,calls=self.closer(app)
                    try:at(cls.close,line,obj.close)
                    except KeyboardInterrupt:pass
                    obj.close();self.assertTrue(getattr(obj,closed));self.assertEqual(calls,['cleanup']);self.assertIsNone(getattr(obj,attribute))
    def test_actual_callback_same_caller_reentry_keeps_outer_owner(self):
        for app in (False,True):
            with self.subTest(app=app):
                obj,attribute,closed,call,calls=self.closer(app);original=getattr(obj,call);seen=[]
                def callback():
                    owner=getattr(obj,attribute);seen.append(obj.close());self.assertIs(getattr(obj,attribute),owner);return original()
                setattr(obj,call,callback);obj.close();obj.close()
                self.assertEqual(seen[0]['state'],'incomplete');self.assertEqual(calls,['cleanup']);self.assertIsNone(getattr(obj,attribute))
    def test_other_caller_and_cancelled_nonowner_cannot_release_owner(self):
        for app in (False,True):
            for cancel in (False,True):
                with self.subTest(app=app,cancel=cancel):
                    obj,attribute,closed,call,calls=self.closer(app);original=getattr(obj,call);cls=type(obj);namespace=cls.close.__globals__;real=namespace['get_ident'];error=C.xapp.SessionCleanupError if app else C.own.CleanupIncomplete
                    def callback():
                        owner=getattr(obj,attribute);namespace['get_ident']=lambda:real()+1
                        try:
                            if cancel:
                                node=method(cls,'close');line=next(n for n in ast.walk(node) if isinstance(n,ast.With)).lineno
                                with self.assertRaises(KeyboardInterrupt):at(cls.close,line,obj.close)
                            else:
                                with self.assertRaises(error):obj.close()
                        finally:namespace['get_ident']=real
                        self.assertIs(getattr(obj,attribute),owner);return original()
                    setattr(obj,call,callback);obj.close();self.assertTrue(getattr(obj,closed));self.assertEqual(calls,['cleanup'])
    def test_reentry_then_exception_releases_attempt_and_retains_resource(self):
        x=C.xapp.XAppSession('inert',10,10,supervisor=types.SimpleNamespace(cleanup=lambda:None,children=[]));events=[]
        class Bus:
            def close(self):
                events.append('bus');self.pending=x.close()
                if events.count('bus')==1:raise RuntimeError('inert bus refusal')
        b=Bus();x._app_bus=b;x.release_input=lambda:events.append('input');x.stop_capture=lambda:events.append('capture')
        with self.assertRaises(C.xapp.SessionCleanupError):x.close()
        self.assertIs(x._app_bus,b);self.assertEqual(b.pending['state'],'incomplete');self.assertIn('input',events);self.assertIn('capture',events);self.assertIsNone(x._cleanup_active)
        x.close();self.assertTrue(x._closed);self.assertEqual(events.count('bus'),2);self.assertEqual(events.count('input'),1);self.assertEqual(events.count('capture'),1)
with (OUT/'tests.log').open('x') as log:
    result=unittest.TextTestRunner(stream=log,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Lifecycle))
report=dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),failure_names=[t.id() for t,_ in result.failures],error_names=[t.id() for t,_ in result.errors],observations=observations,bindings=H.BINDINGS,native_effects=0,scope='Actual full selected source/inert lifecycle; no native qualification',test_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
(OUT/'REPORT.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:report[k] for k in ('tests','failures','errors','failure_names','error_names')}));raise SystemExit(0 if result.wasSuccessful() else 1)
