"""Independent transaction controls using the unchanged inspected memory frontend."""
import ast, hashlib, json, sys, types, unittest, socket, errno
from pathlib import Path

ROOT, OLD, PLEB, OS_ROOT, OUT = map(Path, sys.argv[1:6])
fixture = ROOT / 'tests/test_bus_loss_inert.py'
tree = ast.parse(fixture.read_bytes(), filename=str(fixture))
nodes=[]
for node in tree.body:
    if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='result' for t in node.targets): break
    nodes.append(node)
ns={'__name__':'independent_transaction_definitions','__file__':str(fixture)}
selected_names=sys.argv[6:]
sys.argv=sys.argv[:6]
exec(compile(ast.Module(body=nodes,type_ignores=[]),str(fixture),'exec'),ns)
H,C,F=ns['H'],ns['CURRENT'],ns['LossTests']()
observations=[]

def clean(n):
    for key,node in list(n.ops.nodes.items()):
        if node.instance=='A' and node.scope in ('outer','inner'): del n.ops.nodes[key]

def controller_gone(n,manager):
    n.ops.exit(n.ops.nodes['platform-root'])
    n.ops.nodes['platform-root'].reaped=True
    manager.unit=lambda *_:None

def cancelled_prepare(p, point):
    if point in ('register-before','register-after','capture-before','capture-after','transfer-before','transfer-after'):
        method=point.split('-')[0]; original=getattr(p.handles,method)
        def cancel(value):
            matched=510099 in value if isinstance(value,(tuple,list)) else value==510099
            if not matched:return original(value)
            setattr(p.handles,method,original)
            if point.endswith('after'): original(value)
            raise KeyboardInterrupt('one-shot inert '+point)
        setattr(p.handles,method,cancel)
    elif point.startswith('publish-'):
        key,when=point[len('publish-'):].rsplit('-',1)
        class Cancel(C.supervision.SupervisedPlatform):
            def __setattr__(self,name,value):
                if name==key and getattr(self,'_armed',False):
                    self._armed=False
                    if when=='after':super().__setattr__(name,value)
                    raise KeyboardInterrupt('one-shot inert '+point)
                super().__setattr__(name,value)
        p.__class__=Cancel;p._armed=True
    else:raise AssertionError(point)

class Transactions(unittest.TestCase):
    def retained_live(self,point):
        with F.frontend(launch=False) as (p,n,m,*_):
            clean(n);cancelled_prepare(p,point)
            with self.assertRaises(KeyboardInterrupt) as caught:p.prepare()
            self.assertIs(caught.exception.resource,p)
            controller_gone(n,m)
            error=None
            try:p.retire(timeout=.03)
            except C.own.CleanupIncomplete as caught:error=str(caught)
            observations.append(dict(case=point,closed=p.closed,error=error,pending=p.pending_wait_packet is not None,
                                     wait_alive=n.ops.nodes['wait-worker'].live,fd_closed=510099 in n.ops.closed))
            self.assertFalse(p.closed,'live separately owned wait process must keep retirement retryable')
            self.assertIsNotNone(error)
            self.assertNotIn(510099,n.ops.closed)
            self.assertTrue(n.ops.nodes['foreign'].live)
    def test_registration_cancellation_before_and_after(self):
        for when in ('before','after'):
            with self.subTest(when=when):self.retained_live('register-'+when)
    def test_pending_publication_cancellation_before_and_after(self):
        for when in ('before','after'):
            with self.subTest(when=when):self.retained_live('publish-pending_wait_packet-'+when)
    def test_capture_cancellation_before_and_after(self):
        for when in ('before','after'):
            with self.subTest(when=when):self.retained_live('capture-'+when)
    def test_authentication_publication_cancellation_before_and_after(self):
        for when in ('before','after'):
            with self.subTest(when=when):self.retained_live('publish-wait_handoff-'+when)
    def test_public_owner_and_fd_publication_cancellation(self):
        for key in ('wait_owner','wait_pidfd'):
            for when in ('before','after'):
                with self.subTest(key=key,when=when):self.retained_live('publish-'+key+'-'+when)
    def test_registry_transfer_cancellation_before_and_after(self):
        for when in ('before','after'):
            with self.subTest(when=when):self.retained_live('transfer-'+when)
    def test_exited_uncollected_wait_must_retain_then_detached_retry(self):
        for pending in (False,True):
            with self.subTest(pending=pending), F.frontend(launch=False) as (p,n,m,*_):
                clean(n)
                if pending:
                    cancelled_prepare(p,'capture-after')
                    with self.assertRaises(KeyboardInterrupt):p.prepare()
                else:p.prepare()
                controller_gone(n,m);node=n.ops.nodes['wait-worker'];n.ops.exit(node)
                self.assertTrue(n.ops.ready(510099));self.assertFalse(n.ops.reaped(510099))
                error=None
                try:p.retire(timeout=.03)
                except C.own.CleanupIncomplete as caught:error=str(caught)
                observations.append(dict(case='exited-uncollected',pending=pending,closed=p.closed,error=error,
                                         ready=True,reaped=node.reaped,fd_closed=510099 in n.ops.closed))
                self.assertIsNotNone(error,'readable zombie plus absent scope is not exact collection proof')
                self.assertFalse(p.closed);self.assertNotIn(510099,n.ops.closed)
                node.reaped=True;p.retire(timeout=.03);self.assertTrue(p.closed)
    def test_detached_pending_cleanup_never_publishes_authority(self):
        with F.frontend(launch=False) as (p,n,m,*_):
            clean(n);cancelled_prepare(p,'capture-after')
            with self.assertRaises(KeyboardInterrupt):p.prepare()
            controller_gone(n,m);node=n.ops.nodes['wait-worker'];n.ops.exit(node);node.reaped=True
            def forbidden(*_):raise AssertionError('named action authority must never be granted')
            m.start=m.stop=m.reset_failed=forbidden
            p.retire(timeout=.03)
            self.assertTrue(p.closed);self.assertIsNone(p.wait_owner);self.assertIsNone(p.wait_handoff)
            self.assertEqual(n.ops.closed.count(510099),1);self.assertTrue(n.ops.nodes['foreign'].live)
    def test_detached_authenticated_cleanup_has_exact_collection_positive(self):
        with F.frontend(launch=False) as (p,n,m,*_):
            clean(n);p.prepare();controller_gone(n,m)
            node=n.ops.nodes['wait-worker'];n.ops.exit(node);node.reaped=True
            self.assertEqual(n.ops.pidfd_pid(510099),-1);self.assertTrue(n.ops.reaped(510099))
            p.retire(timeout=.03);self.assertTrue(p.closed)
            self.assertEqual(n.ops.closed.count(510099),1);self.assertTrue(n.ops.nodes['foreign'].live)
    def test_collection_flag_publication_cancellation_before_and_after(self):
        for when in ('before','after'):
            with self.subTest(when=when),F.frontend(launch=False) as (p,n,m,*_):
                clean(n);cancelled_prepare(p,'capture-after')
                with self.assertRaises(KeyboardInterrupt):p.prepare()
                controller_gone(n,m);n.ops.exit(n.ops.nodes['wait-worker']);n.ops.nodes['wait-worker'].reaped=True
                cancelled_prepare(p,'publish-pending_wait_collected-'+when)
                with self.assertRaises(C.own.CleanupIncomplete):p.retire(timeout=.03)
                self.assertFalse(p.closed);self.assertNotIn(510099,n.ops.closed)
                p.retire(timeout=.03);self.assertTrue(p.closed)
                self.assertIsNone(p.wait_owner);self.assertEqual(n.ops.closed.count(510099),1)
    def test_replaced_and_ambiguous_scope_proof_stays_retryable(self):
        for case in ('replaced','absent-manager','cancel-before','cancel-after'):
            with self.subTest(case=case),F.frontend(launch=False) as (p,n,m,*_):
                clean(n);cancelled_prepare(p,'capture-after')
                with self.assertRaises(KeyboardInterrupt):p.prepare()
                record=m.scope('unused',1);controller_gone(n,m)
                node=n.ops.nodes['wait-worker'];n.ops.exit(node);node.reaped=True
                original=m.scope
                def query(*args):
                    if case=='replaced':return dict(record,InvocationID=b'3'*16)
                    if case=='absent-manager':raise ConnectionError('inert ambiguous manager')
                    if case=='cancel-after':original(*args)
                    raise KeyboardInterrupt('inert manager cancellation')
                m.scope=query
                with self.assertRaises(C.own.CleanupIncomplete):p.retire(timeout=.03)
                self.assertFalse(p.closed);self.assertNotIn(510099,n.ops.closed)
                self.assertIsNone(p.wait_owner);self.assertTrue(n.ops.nodes['foreign'].live)
                m.scope=original;p.retire(timeout=.03);self.assertTrue(p.closed)
    def test_malformed_wire_variants_retain_cleanup_obligation(self):
        for variant in ('wrong-op','wrong-token','wrong-supervisor','bool-pid','duplicate-json','bad-json','trunc','ctrunc','extra-fd','bad-ancillary'):
            with self.subTest(variant=variant),F.frontend(launch=False) as (p,n,m,client,*_):
                clean(n);cancelled_prepare(p,'capture-after')
                with self.assertRaises(KeyboardInterrupt):p.prepare()
                data,fds,bad,flags=p.pending_wait_packet;v=json.loads(data)
                if variant=='wrong-op':v['op']='launch'
                elif variant=='wrong-token':v['instance']='b'*32
                elif variant=='wrong-supervisor':v['supervisor'][0]='3'*32
                elif variant=='bool-pid':v['wait_owner'][1]=True
                elif variant=='duplicate-json':data=b'{"version":2,"version":2}'
                elif variant=='bad-json':data=b'{'
                elif variant=='trunc':flags|=socket.MSG_TRUNC
                elif variant=='ctrunc':flags|=socket.MSG_CTRUNC
                elif variant=='extra-fd':fds=(*fds,510000)
                elif variant=='bad-ancillary':bad=True
                if variant not in ('duplicate-json','bad-json'):data=json.dumps(v).encode()
                p.pending_wait_packet=(data,fds,bad,flags);controller_gone(n,m)
                n.ops.exit(n.ops.nodes['wait-worker']);n.ops.nodes['wait-worker'].reaped=True
                with self.assertRaises(C.own.CleanupIncomplete):p.retire(timeout=.03)
                self.assertFalse(p.closed);self.assertNotIn(510099,n.ops.closed);self.assertFalse(p.pending_wait_collected)
                self.assertIsNone(p.wait_owner);self.assertTrue(n.ops.nodes['foreign'].live)
    def test_close_cancellation_before_after_reuse_and_anonymous_retry(self):
        for state in ('before','after-ebadf','after-reuse','anonymous'):
            with self.subTest(state=state), F.frontend(launch=False) as (p,n,m,*_):
                clean(n);cancelled_prepare(p,'capture-after')
                with self.assertRaises(KeyboardInterrupt):p.prepare()
                controller_gone(n,m);n.ops.exit(n.ops.nodes['wait-worker']);n.ops.nodes['wait-worker'].reaped=True
                lease=p.handles.leases[510099];old_fs=lease.filesystem;calls=[];current={'value':lease.identity}
                def fstat(_):
                    if current['value'] is None:raise OSError(errno.EBADF,'inert closed')
                    a,b,c=current['value'];return types.SimpleNamespace(st_dev=a,st_ino=b,st_mode=c)
                def close(fd):
                    calls.append(fd)
                    if len(calls)==1:
                        if state=='after-ebadf':n.ops.close(fd);current['value']=None
                        elif state=='after-reuse':n.ops.close(fd);current['value']=(1,999999,0o100600)
                        cancel = getattr(C.own,'DescriptorCloseCancelled',KeyboardInterrupt) if state=='before' else KeyboardInterrupt
                        raise cancel('inert descriptor cancellation')
                    n.ops.close(fd)
                if state=='anonymous':lease.identity=(1,510099,0);current['value']=lease.identity
                lease.filesystem=types.SimpleNamespace(fstat=fstat,close=close)
                if state=='before':lease.raw=False
                with self.assertRaises(C.own.CleanupIncomplete):p.retire(timeout=.03)
                self.assertTrue(p.pending_wait_collected);self.assertFalse(p.closed)
                if state=='anonymous':
                    with self.assertRaises(C.own.CleanupIncomplete):p.retire(timeout=.03)
                    self.assertEqual(calls,[510099]);self.assertIn(510099,p.handles.fds)
                else:
                    p.retire(timeout=.03);self.assertTrue(p.closed)
                    self.assertEqual(len(calls),2 if state=='before' else 1)
                    self.assertEqual(n.ops.closed.count(510099),1)
                self.assertIsNone(p.wait_owner);self.assertTrue(n.ops.nodes['foreign'].live)


POINTS = ('register-before','register-after','publish-pending_wait_packet-before',
          'publish-pending_wait_packet-after','capture-before','capture-after',
          'publish-wait_handoff-before','publish-wait_handoff-after',
          'publish-wait_owner-before','publish-wait_owner-after',
          'publish-wait_pidfd-before','publish-wait_pidfd-after','transfer-before','transfer-after')

class FullTransactions(Transactions):
    def test_all_original_boundaries_live_zombie_collected(self):
        for point in POINTS:
            with self.subTest(point=point), F.frontend(launch=False) as (p,n,m,*_):
                clean(n);cancelled_prepare(p,point)
                with self.assertRaises(KeyboardInterrupt) as caught:p.prepare()
                self.assertIs(caught.exception.resource,p)
                controller_gone(n,m)
                for state in ('live','exited','collected'):
                    if state=='exited':n.ops.exit(n.ops.nodes['wait-worker'])
                    if state=='collected':n.ops.nodes['wait-worker'].reaped=True
                    if state!='collected':
                        with self.assertRaises(C.own.CleanupIncomplete):p.retire(timeout=.03)
                        self.assertFalse(p.closed);self.assertNotIn(510099,n.ops.closed)
                    else:
                        p.retire(timeout=.03);self.assertTrue(p.closed)
                        self.assertEqual(n.ops.closed.count(510099),1)
                self.assertTrue(n.ops.nodes['foreign'].live)
                observations.append(dict(case='full-state-matrix',point=point,states=['live','exited-uncollected','collected'],closed=p.closed))

    def test_all_original_boundaries_dead_before_first_retry(self):
        for point in POINTS:
            with self.subTest(point=point), F.frontend(launch=False) as (p,n,m,*_):
                clean(n);cancelled_prepare(p,point)
                with self.assertRaises(KeyboardInterrupt):p.prepare()
                authenticated=p.wait_handoff is not None;controller_gone(n,m)
                n.ops.exit(n.ops.nodes['wait-worker']);n.ops.nodes['wait-worker'].reaped=True
                def forbidden(*_):raise AssertionError('no numeric reacquisition or named authority')
                m.start=m.stop=m.reset_failed=forbidden;C.supervision.os.pidfd_open=forbidden
                p.retire(timeout=.03);self.assertTrue(p.closed)
                self.assertEqual(n.ops.closed.count(510099),1)
                if not authenticated:self.assertIsNone(p.wait_owner);self.assertIsNone(p.wait_handoff)
                self.assertTrue(n.ops.nodes['foreign'].live)

    def test_recvmsg_interrupted_before_or_after_delivery_stays_unresolved(self):
        for when in ('before','after'):
            with self.subTest(when=when),F.frontend(launch=False) as (p,n,m,client,*_):
                clean(n);original=client.recvmsg;calls=[]
                def cancel(*args):
                    calls.append(when)
                    if when=='after':original(*args)
                    raise KeyboardInterrupt('inert receive '+when)
                client.recvmsg=cancel
                with self.assertRaises(KeyboardInterrupt):p.prepare()
                controller_gone(n,m);n.ops.exit(n.ops.nodes['wait-worker']);n.ops.nodes['wait-worker'].reaped=True
                for _ in range(2):
                    with self.assertRaises(C.own.CleanupIncomplete):p.retire(timeout=.03)
                    self.assertFalse(p.closed);self.assertNotIn(510099,n.ops.closed)
                self.assertEqual(calls,[when]);self.assertTrue(n.ops.nodes['foreign'].live)

    def test_transaction_publication_before_and_after_is_a_retirement_obligation(self):
        for when in ('before','after'):
            with self.subTest(when=when),F.frontend(launch=False) as (p,n,m,*_):
                clean(n);cancelled_prepare(p,'publish-wait_receive-'+when)
                with self.assertRaises(KeyboardInterrupt):p.prepare()
                controller_gone(n,m);n.ops.exit(n.ops.nodes['wait-worker']);n.ops.nodes['wait-worker'].reaped=True
                with self.assertRaises(C.own.CleanupIncomplete):p.retire(timeout=.03)
                self.assertTrue(p.wait_receive_required);self.assertFalse(p.closed)
                self.assertNotIn(510099,n.ops.closed)

    def test_raw_packet_and_capture_publications_preserve_known_or_ambiguous_state(self):
        original=C.supervision.WaitReceive
        for key in ('raw','packet','captured'):
            for when in ('before','after'):
                with self.subTest(key=key,when=when),F.frontend(launch=False) as (p,n,m,*_):
                    clean(n)
                    class Cancel(original):
                        def __init__(self):super().__init__();self.armed=True
                        def __setattr__(self,name,value):
                            if name==key and getattr(self,'armed',False):
                                self.armed=False
                                if when=='after':super().__setattr__(name,value)
                                raise KeyboardInterrupt('inert journal publication')
                            super().__setattr__(name,value)
                    with H.replace(C.supervision,WaitReceive=Cancel):
                        with self.assertRaises(KeyboardInterrupt):p.prepare()
                    controller_gone(n,m);n.ops.exit(n.ops.nodes['wait-worker']);n.ops.nodes['wait-worker'].reaped=True
                    # The exact Handles journal now lets raw/packet publication
                    # cancellation resume this same received descriptor. Since
                    # this fixture proves reaping and scope absence, retirement
                    # may finish without another receive or numeric recovery.
                    p.retire(timeout=.03)
                    self.assertTrue(p.closed)
                    self.assertEqual(n.ops.closed.count(510099),1)
                    self.assertIsNone(p.wait_owner)

    def test_initial_incarnation_capture_cancellation_never_recaptures_a_number(self):
        cls=C.supervision.CheckedDescriptor;original=cls.capture
        for when in ('before','after'):
            with self.subTest(when=when),F.frontend(launch=False) as (p,n,m,*_):
                clean(n);calls=[]
                def capture(lease):
                    if lease.fd!=510099:return original(lease)
                    calls.append(lease.fd)
                    if when=='after':original(lease)
                    raise KeyboardInterrupt('inert initial capture')
                with H.replace(cls,capture=capture):
                    with self.assertRaises(KeyboardInterrupt):p.prepare()
                controller_gone(n,m);n.ops.exit(n.ops.nodes['wait-worker']);n.ops.nodes['wait-worker'].reaped=True
                if when=='after':
                    p.retire(timeout=.03);self.assertTrue(p.closed)
                else:
                    with self.assertRaises(C.own.CleanupIncomplete):p.retire(timeout=.03)
                    self.assertFalse(p.closed);self.assertNotIn(510099,n.ops.closed)
                self.assertEqual(calls,[510099])

    def test_original_received_incarnation_replacement_never_queries_or_closes_foreign(self):
        for point in ('register-before','publish-pending_wait_packet-before','capture-after'):
            with self.subTest(point=point),F.frontend(launch=False) as (p,n,m,*_):
                clean(n);cancelled_prepare(p,point)
                with self.assertRaises(KeyboardInterrupt):p.prepare()
                controller_gone(n,m);n.ops.exit(n.ops.nodes['wait-worker']);n.ops.nodes['wait-worker'].reaped=True
                fs=p.wait_receive.leases[510099].filesystem
                original=fs.fstat;fs.fstat=lambda fd:types.SimpleNamespace(st_dev=2,st_ino=999,st_mode=0o100600) if fd==510099 else original(fd)
                pid=n.ops.pidfd_pid;queries=[]
                def query(fd):
                    if fd==510099:queries.append(fd)
                    return pid(fd)
                n.ops.pidfd_pid=query
                with self.assertRaises(C.own.CleanupIncomplete):p.retire(timeout=.03)
                self.assertFalse(p.closed);self.assertNotIn(510099,n.ops.closed)
                self.assertEqual(queries,[],'foreign replacement must never be queried for original authority')
                self.assertTrue(n.ops.nodes['foreign'].live)

    def test_collection_adapter_cancellation_and_error_retain_retry(self):
        for pending in (False,True):
            for error in (KeyboardInterrupt('inert exact proof cancellation'),ValueError('inert malformed exact proof')):
                with self.subTest(pending=pending,error=type(error).__name__),F.frontend(launch=False) as (p,n,m,*_):
                    clean(n)
                    if pending:
                        cancelled_prepare(p,'register-before')
                        with self.assertRaises(KeyboardInterrupt):p.prepare()
                    else:p.prepare()
                    controller_gone(n,m);n.ops.exit(n.ops.nodes['wait-worker']);n.ops.nodes['wait-worker'].reaped=True
                    original=n.ops.reaped
                    def fail(fd):
                        if fd==510099:raise error
                        return original(fd)
                    n.ops.reaped=fail
                    with self.assertRaises(C.own.CleanupIncomplete):p.retire(timeout=.03)
                    self.assertFalse(p.closed);self.assertFalse(p.pending_wait_collected);self.assertNotIn(510099,n.ops.closed)
                    n.ops.reaped=original;p.retire(timeout=.03);self.assertTrue(p.closed)

    def test_authenticated_close_cancellation_has_no_same_attempt_retry(self):
        for state in ('before','after-ebadf','after-reuse','anonymous'):
            with self.subTest(state=state),F.frontend(launch=False) as (p,n,m,*_):
                clean(n);p.prepare();controller_gone(n,m)
                n.ops.exit(n.ops.nodes['wait-worker']);n.ops.nodes['wait-worker'].reaped=True
                lease=p.wait_handoff[2];current={'value':lease.identity};calls=[]
                def fstat(_):
                    if current['value'] is None:raise OSError(errno.EBADF,'inert closed')
                    a,b,c=current['value'];return types.SimpleNamespace(st_dev=a,st_ino=b,st_mode=c)
                def close(fd):
                    calls.append(fd)
                    if len(calls)==1:
                        if state=='after-ebadf':n.ops.close(fd);current['value']=None
                        elif state=='after-reuse':n.ops.close(fd);current['value']=(1,999999,0o100600)
                        cancel = getattr(C.own,'DescriptorCloseCancelled',KeyboardInterrupt) if state=='before' else KeyboardInterrupt
                        raise cancel('inert authenticated release')
                    n.ops.close(fd)
                if state=='anonymous':lease.identity=(1,510099,0);current['value']=lease.identity
                lease.filesystem=types.SimpleNamespace(fstat=fstat,close=close)
                if state=='before':lease.raw=False
                with self.assertRaises(C.own.CleanupIncomplete):p.retire(timeout=.03)
                self.assertEqual(calls,[510099]);self.assertTrue(p.pending_wait_collected);self.assertFalse(p.closed)
                if state=='anonymous':
                    with self.assertRaises(C.own.CleanupIncomplete):p.retire(timeout=.03)
                    self.assertEqual(calls,[510099])
                else:
                    p.retire(timeout=.03);self.assertTrue(p.closed)
                    self.assertEqual(len(calls),2 if state=='before' else 1)
                    self.assertEqual(n.ops.closed.count(510099),1)
                self.assertTrue(n.ops.nodes['foreign'].live)

with (OUT/'tests.log').open('x') as log:
    suite=unittest.TestSuite(FullTransactions(name) for name in selected_names) if selected_names else unittest.defaultTestLoader.loadTestsFromTestCase(FullTransactions)
    result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
report=dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),
            failure_names=[t.id() for t,_ in result.failures],error_names=[t.id() for t,_ in result.errors],
            observations=observations,bindings=H.BINDINGS,fixture_sha256=H.digest(fixture.read_bytes()),
            definitions_ast_sha256=H.digest(ast.dump(ast.Module(body=nodes,type_ignores=[]),include_attributes=True).encode()),
            native_effects=0,scope='independent transaction semantics, actual committed source with unchanged inert interfaces')
(OUT/'REPORT.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:report[k] for k in ('tests','failures','errors','failure_names','observations')}))
raise SystemExit(0 if result.wasSuccessful() else 1)
