"""Reviewer-selected contract cases. All candidate effects use inert adapters."""
import ast
import errno
import hashlib
import importlib.util
import json
from pathlib import Path
import signal
import sys
import types
import unittest

source_root, base_root, output = map(Path, sys.argv[1:])
output.mkdir(parents=True, exist_ok=False)
spec = importlib.util.spec_from_file_location('retained_inert', source_root/'tests/test_bus_ownership_inert.py')
f = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = f
spec.loader.exec_module(f)
f.install_denials()
assert f.os.pidfd_open is f.deny and f.subprocess.Popen is f.deny and f.ctypes.CDLL is f.deny
source = f.load_source(source_root, 'reviewer_source')
observations = {}


class Cases(unittest.TestCase):
    source = source

    def bus(self, state, model):
        helper = f.Candidate('test_normal_owner_requires_ack_and_proof')
        helper.source = self.source
        return helper.bus_object(state, model)

    def test_branching_deep_late_adoption(self):
        for depth in (12, 24, 64):
            with self.subTest(depth=depth):
                model = f.Model()
                model.chain(depth)
                model.node('branch', 'chain-5')
                model.node('foreign', 'sdk', instance='B')
                model.adoption_delay = .45
                drain = self.source.own.DescendantDrain(f.Boundary(model), model)
                drain.run(deadline=1.5)
                self.assertEqual(model.survivors(), set())
                self.assertEqual(set(model.waited), set(model.nodes)-{'foreign'})
                self.assertTrue(model.nodes['foreign'].live)

    def test_final_adoption_probe(self):
        class Late(f.Model):
            def empty_children(self):
                answer = super().empty_children()
                if answer and not hasattr(self, 'added'):
                    self.added = True
                    self.node('late-after-proof')
                return answer
        model = Late()
        drain = self.source.own.DescendantDrain(f.Boundary(model), model)
        drain.run(deadline=1.5)
        self.assertFalse(model.survivors())
        self.assertIn('late-after-proof', model.waited)

    def test_budget_retry_keeps_original_registry(self):
        model = f.Model()
        model.chain(12)
        model.costs[('acquire', 1)] = 2
        drain = self.source.own.DescendantDrain(f.Boundary(model), model)
        with self.assertRaises(self.source.own.CleanupIncomplete) as caught:
            drain.run(deadline=1.5)
        self.assertIs(caught.exception.resource, drain)
        self.assertTrue(drain.owned)
        owned = dict(drain.owned)
        drain.run(deadline=model.now()+1.5)
        self.assertFalse(drain.owned)
        self.assertTrue(all(fd in model.closed for fd in owned.values()))

    def test_receipt_cannot_cover_live_primary(self):
        state, model, boundary = f.sdk(self.source, primary_live=True)
        with self.assertRaises(self.source.own.CleanupIncomplete):
            state.finish()
        self.assertFalse(model.nodes['primary'].live)
        self.assertEqual(state.finish()['source'], 'sdk-recovery')

    def test_recovery_is_not_adopted_child_wait_certificate(self):
        state, model, boundary = f.sdk(self.source, status=-9, child_live=True)
        with self.assertRaises(self.source.own.CleanupIncomplete):
            state.finish()
        receipt = state.finish()
        self.assertEqual(receipt['source'], 'sdk-recovery')
        self.assertIn('owner_fault', receipt)
        self.assertNotIn('adoption_drained', receipt)
        self.assertFalse(model.survivors())
        self.assertTrue(any(not n.reaped for n in model.nodes.values() if n.scope=='inner'))

    def test_duplicate_ack_keys_and_identity_types(self):
        own = self.source.own
        identity = (11, 101, 11, 102)
        good = own.completion('A', types.SimpleNamespace(identity=identity), True, complete=True)
        payload = json.dumps(good).encode()
        self.assertEqual(own.validate_completion(payload, 'A', identity, True), good)
        variants = [b'{"version":1,'+payload[1:]]
        for key, value in [('boundary',[11,101,11,999]),('boundary',[True,101,11,102]),
                           ('bound',False),('version',True),('pending',['late']),('state','incomplete')]:
            data = dict(good); data[key]=value
            variants.append(json.dumps(data).encode())
        for data in variants:
            with self.subTest(data=data), self.assertRaises(ValueError):
                own.validate_completion(data, 'A', identity, True)

    def test_gate_late_close_invalidates_each_authority(self):
        state, model, _b = f.sdk(self.source)
        bus = self.bus(state, model)
        bus.app_fd, bus.gate_write = f.FD('gate-read'), f.FD('gate-write')
        calls = []
        def close(fd):
            calls.append(fd)
            raise self.source.own.DescriptorReleased('inert late close')
        bus.ops = types.SimpleNamespace(close=close)
        with self.assertRaises(self.source.own.CleanupIncomplete): bus.close_gate()
        self.assertIsNone(bus.app_fd)
        self.assertIsNone(bus.gate_write)
        bus.close_gate()
        self.assertEqual(len(calls),2)

    def test_unprovisioned_default_launch_refuses_before_birth(self):
        value,_calls=f.session(self.source)
        value._app_bus=None
        value.environment=lambda env:dict(env or {})
        births=[]
        value.supervisor.spawn=lambda *_a,**_kw:births.append('unexpected')
        namespace=value.launch_app.__func__.__globals__
        previous={key:namespace.get(key) for key in ('shutil','PrivateBus','os')}
        existing={key for key in previous if key in namespace}
        namespace['shutil']=types.SimpleNamespace(which=lambda _name:'/inert/dbus-daemon')
        namespace['PrivateBus']=self.source.bus.PrivateBus
        namespace['os']=types.SimpleNamespace(path=types.SimpleNamespace(basename=lambda value:value))
        try:
            with f.replace(self.source.bus,
                    threading=types.SimpleNamespace(RLock=f.DummyLock,Event=f.DummyEvent),
                    secrets=types.SimpleNamespace(token_hex=lambda _n:'A'),
                    LinuxProcesses=lambda:f.Model()):
                with self.assertRaisesRegex(RuntimeError,'CGROUP_ROOT delegation'):
                    value.launch_app(['inert-primary'],isolate_bus=True,clipboard=False)
        finally:
            for key,old in previous.items():
                if key in existing:namespace[key]=old
                else:namespace.pop(key,None)
        self.assertEqual(births,[])
        observations['default-launch']=dict(refused=True,process_births=len(births),kernel_effects=0)

    def create_boundary(self, mode):
        own = self.source.own
        live = {f.FD('parent')}
        calls, removed = [], []
        def open_file(name, *_a, **_kw):
            fd = f.FD(name); live.add(fd); return fd
        def close(fd):
            calls.append(fd.identity)
            if mode=='rollback' and fd==f.FD('leaf'):
                raise PermissionError('inert pre-operation close failure')
            if fd not in live:
                raise OSError('inert already released descriptor')
            live.remove(fd)
            if mode=='late' and fd==f.FD('cgroup.type'):
                raise InterruptedError('inert Linux EINTR after release')
        def read(fd, *_a):
            if mode=='rollback' and fd==f.FD('cgroup.type'):
                raise ValueError('inert read failure')
            return b'domain\n' if fd==f.FD('cgroup.type') else b'populated 0\n'
        fake_os = types.SimpleNamespace(O_RDONLY=0,O_DIRECTORY=1,O_NOFOLLOW=2,O_CLOEXEC=4,O_WRONLY=8,
             SEEK_SET=0,mkdir=lambda *_a,**_kw:None,open=open_file,close=close,read=read,
             lseek=lambda *_a:None,fstat=lambda _fd:types.SimpleNamespace(st_dev=11,st_ino=101),
             stat=lambda *_a,**_kw:types.SimpleNamespace(st_dev=11,st_ino=101),
             rmdir=lambda n,**_kw:removed.append(n))
        boundary = error = None
        original = own.CgroupBoundary.__dict__['_filesystem']
        try:
            own.CgroupBoundary._filesystem = staticmethod(lambda _fd:None)
            def classified_close(fd):
                # The review models a pre-operation refusal as PermissionError
                # and Linux's post-release EINTR as InterruptedError. Apply that
                # model at the production closer boundary, not raw os.close:
                # real Linux OSError from close invalidates the fd number.
                try:
                    close(fd)
                except InterruptedError as problem:
                    raise own.DescriptorReleased(str(problem)) from problem
            with f.replace(own, os=fake_os, close_descriptor=classified_close):
                try:
                    boundary = own.CgroupBoundary.create_at(f.FD('parent'),'leaf')
                    if mode=='normal': boundary.retire()
                except Exception as problem:
                    error=problem
        finally:
            own.CgroupBoundary._filesystem=original
        observations['boundary-'+mode] = dict(calls=calls, live=sorted(fd.identity for fd in live),
            removed=removed,error=str(error),retained_resource=bool(getattr(error,'resource',None)))
        return calls, live, removed, error

    def test_boundary_normal_null_control(self):
        calls, live, removed, error = self.create_boundary('normal')
        self.assertIsNone(error)
        self.assertEqual(live,set())
        self.assertEqual(calls.count('cgroup.type'),1)

    def test_boundary_late_close_does_not_retry_descriptor_number(self):
        calls, live, removed, error = self.create_boundary('late')
        self.assertEqual(calls.count('cgroup.type'),1,
                         'construction retried a descriptor already released by Linux close')
        self.assertNotIn(f.FD('parent'),live)

    def test_boundary_rollback_retains_failed_authority(self):
        calls, live, removed, error = self.create_boundary('rollback')
        self.assertTrue(getattr(error,'resource',None),
                        'failed constructor dropped all authority for remaining parent/leaf handles')

    def test_watcher_failed_release_remains_retryable(self):
        state, model, _b = f.sdk(self.source)
        bus = self.bus(state,model)
        duplicate = f.FD('cleanup-owner',1)
        model.failures[('fd-close',1)] = PermissionError('inert pre-operation watcher FD close failure')
        with self.assertRaises(PermissionError): bus._watch_owner(duplicate)
        bus._watcher=types.SimpleNamespace(join=lambda **_kw:None,is_alive=lambda:False)
        bus.close()
        observations['watcher'] = dict(closed=bus.closed,duplicate_retired=duplicate in model.closed)
        self.assertIn(duplicate,model.closed,'close accepted while failed watcher authority was dropped')

    def test_private_bus_reentrant_join_is_coherent(self):
        state, model, _b = f.sdk(self.source)
        bus = self.bus(state,model)
        class Watcher:
            entered=False
            def join(self,**_kw):
                if not self.entered:
                    self.entered=True
                    bus.close()
            def is_alive(self): return False
        bus._watcher=Watcher()
        error=None
        try: bus.close()
        except Exception as problem: error=problem
        observations['join-reentry']=dict(error=str(error),closed=bus.closed)
        self.assertIsNone(error,'close reread a watcher cleared by concurrent/reentrant close')

    def test_xapp_reentrant_cleanup_does_not_repeat_resource(self):
        value,calls=f.session(self.source)
        class Reenter(f.Resource):
            entered=False
            def close(self):
                if not self.entered:
                    self.entered=True
                    value.close()
                super().close()
        value._app_bus=Reenter('bus',calls)
        value.close()
        observations['xapp-reentry']=dict(bus_close_calls=calls.count('bus'),closed=value._closed)
        self.assertEqual(calls.count('bus'),1,'cleanup callback repeated an in-progress resource close')

    def test_construction_release_faults_keep_only_live_authority(self):
        own = self.source.own
        calls=[]
        failed=True
        def close(fd):
            calls.append(fd)
            if fd==f.FD('kind'):
                raise own.DescriptorReleased('inert late release')
            if failed and fd==f.FD('leaf'):
                raise PermissionError('inert pre-operation release')
        fs=types.SimpleNamespace(stat=lambda *_a,**_kw:types.SimpleNamespace(st_dev=11,st_ino=101),
                                 fstat=lambda _fd:types.SimpleNamespace(st_dev=11,st_ino=101),
                                 rmdir=lambda *_a,**_kw:None)
        with f.replace(own,os=fs,close_descriptor=close):
            allocation = own.CgroupConstruction(f.FD('parent'), 'leaf')
            allocation.created, allocation.identity = True, (11,101)
            allocation.opened = [f.FD('leaf'),f.FD('kind'),f.FD('events')]
            with self.assertRaises(own.CleanupIncomplete) as raised: allocation.retire()
            self.assertIs(raised.exception.resource,allocation)
            self.assertEqual(allocation.opened,[f.FD('leaf')])
            self.assertIsNone(allocation.parent)
            self.assertIn(f.FD('events'),calls)
            failed=False
            allocation.retire()
            allocation.retire()
        self.assertEqual(calls.count(f.FD('kind')),1)
        self.assertEqual(calls.count(f.FD('parent')),1)
        self.assertEqual(calls.count(f.FD('leaf')),2)

    def test_unremoved_leaf_keeps_parent_and_releases_independent_handles(self):
        own=self.source.own
        closed=[]
        fail=True
        def remove(*_a,**_kw):
            if fail: raise PermissionError('inert leaf removal refusal')
        fs=types.SimpleNamespace(stat=lambda *_a,**_kw:types.SimpleNamespace(st_dev=11,st_ino=101),
                                 fstat=lambda _fd:types.SimpleNamespace(st_dev=11,st_ino=101),rmdir=remove)
        with f.replace(own,os=fs,close_descriptor=closed.append):
            allocation=own.CgroupConstruction(f.FD('parent'),'leaf')
            allocation.created,allocation.identity=True,(11,101)
            allocation.opened=[f.FD('kind'),f.FD('directory')]
            with self.assertRaises(own.CleanupIncomplete): allocation.retire()
            self.assertEqual(allocation.opened,[])
            self.assertTrue(allocation.created)
            self.assertEqual(allocation.parent,f.FD('parent'))
            self.assertNotIn(f.FD('parent'),closed)
            fail=False
            allocation.retire()
        self.assertFalse(allocation.created)
        self.assertIsNone(allocation.parent)
        self.assertEqual(len(closed),3)

    def test_partial_inner_and_outer_retirement_stay_owned(self):
        own=self.source.own
        calls=[]
        class Partial:
            directory=f.FD('outer-directory')
            def __init__(self,name): self.name,self.fail=name,True
            def retire(self):
                calls.append(self.name)
                if self.fail: raise PermissionError('inert '+self.name+' retirement')
        inner,outer=Partial('inner'),Partial('outer')
        def create(_parent,name):
            if name=='activated': raise own.CleanupIncomplete('inner-construction',resource=inner)
            return outer
        old=own.CgroupBoundary.__dict__['create_at']
        try:
            own.CgroupBoundary.create_at=staticmethod(create)
            with f.replace(own,os=types.SimpleNamespace(dup=lambda _fd:f.FD('inner-parent'))):
                with self.assertRaises(own.CleanupIncomplete) as raised:
                    own.InstanceBoundary.create_at(f.FD('parent'),'A')
        finally: own.CgroupBoundary.create_at=old
        retained=raised.exception.resource
        self.assertEqual(retained.resources,[inner,outer])
        self.assertEqual(calls,['inner','outer'])
        inner.fail=False
        with self.assertRaises(own.CleanupIncomplete): retained.retire()
        self.assertEqual(retained.resources,[outer])
        outer.fail=False
        retained.retire()
        before=list(calls)
        retained.retire()
        self.assertEqual(calls,before)

    def test_background_watcher_fault_is_reported_even_after_release_retry(self):
        state,model,_b=f.sdk(self.source)
        bus=self.bus(state,model)
        duplicate=f.FD('cleanup-owner',1)
        bus._watcher=types.SimpleNamespace(join=lambda **_kw:None,is_alive=lambda:False)
        model.failures[('fd-close',1)]=PermissionError('inert pre-operation release')
        bus._watch_owner(duplicate)
        self.assertEqual(bus._watch_fd,duplicate)
        with self.assertRaises(self.source.own.CleanupIncomplete) as raised: bus.close()
        self.assertIn('inert pre-operation release',str(raised.exception))
        self.assertFalse(bus.closed)
        self.assertIn(duplicate,model.closed)
        self.assertIsNone(bus._watch_fd)
        receipt=bus.close()
        self.assertTrue(bus.closed)
        self.assertIn('inert pre-operation release',receipt['watcher_release_errors'][0])
        self.assertEqual(bus.watcher_release_errors,receipt['watcher_release_errors'])

    def test_late_watcher_release_is_reported_without_descriptor_retry(self):
        state,model,_b=f.sdk(self.source)
        bus=self.bus(state,model)
        duplicate=f.FD('cleanup-owner',1)
        calls=[]
        original=model.close
        def close(fd):
            calls.append(fd)
            original(fd)
            if fd==duplicate: raise self.source.own.DescriptorReleased('inert watcher released late')
        model.close=close
        bus._watcher=types.SimpleNamespace(join=lambda **_kw:None,is_alive=lambda:False)
        bus._watch_owner(duplicate)
        self.assertIsNone(bus._watch_fd)
        with self.assertRaises(self.source.own.CleanupIncomplete): bus.close()
        bus.close()
        self.assertEqual(calls.count(duplicate),1)
        self.assertTrue(bus.closed)
        self.assertTrue(bus.watcher_release_errors)

    def test_watcher_start_failure_keeps_its_duplicate_for_retry(self):
        state,model,_b=f.sdk(self.source)
        bus=self.bus(state,model)
        bus.instance='A'
        duplicate=f.FD('cleanup-owner',1)
        class Unstarted:
            ident=None
            def __init__(self,**_kw): pass
            def start(self): raise RuntimeError('inert thread startup failure')
            def is_alive(self):return False
        model.failures[('fd-close',1)]=PermissionError('inert duplicate release refusal')
        with f.replace(self.source.bus,os=types.SimpleNamespace(dup=lambda _fd:duplicate),
                       threading=types.SimpleNamespace(Thread=Unstarted)):
            with self.assertRaises(self.source.own.CleanupIncomplete): bus._start_watcher()
        self.assertEqual(bus._watch_fd,duplicate)
        self.assertIsNone(bus._watcher)
        bus.close()
        self.assertIn(duplicate,model.closed)
        self.assertTrue(bus.closed)

    def test_cancelled_watcher_start_keeps_thread_and_fd_until_joined(self):
        state,model,_b=f.sdk(self.source)
        bus=self.bus(state,model)
        bus.instance='A'
        duplicate=f.FD('cleanup-owner',1)
        class Cancelled(BaseException):pass
        class Starting:
            ident=None
            def __init__(self,*,target,args,**_kw):self.target,self.args,self.joined=target,args,False
            def start(self):raise Cancelled('inert cancellation during thread startup')
            def is_alive(self):return False
            def join(self,**_kw):self.target(*self.args);self.joined=True
        with f.replace(self.source.bus,os=types.SimpleNamespace(dup=lambda _fd:duplicate),
                       threading=types.SimpleNamespace(Thread=Starting)):
            with self.assertRaises(Cancelled):bus._start_watcher()
        retained=bus._watcher
        self.assertIsNotNone(retained)
        self.assertEqual(bus._watch_fd,duplicate)
        self.assertNotIn(duplicate,model.closed)
        bus.close()
        self.assertTrue(retained.joined and bus.closed)
        self.assertEqual(model.closed.count(duplicate),1)

    def test_reentrant_bus_close_reports_pending_until_one_finalizer_finishes(self):
        state,model,_b=f.sdk(self.source)
        bus=self.bus(state,model)
        seen=[]
        class Watcher:
            def join(self,**_kw): seen.append((bus.close(),bus.closed))
            def is_alive(self): return False
        bus._watcher=Watcher()
        bus.close()
        self.assertEqual(seen,[({'state':'incomplete','phase':'close-in-progress'},False)])
        self.assertTrue(bus.closed)
        self.assertIsNone(bus._finalizer)

    def test_other_bus_closer_gets_truthful_pending_and_can_retry(self):
        state,model,_b=f.sdk(self.source)
        bus=self.bus(state,model)
        bus._finalizer='other-inert-caller'
        before=list(model.closed)
        with self.assertRaises(self.source.own.CleanupIncomplete) as raised: bus.close()
        self.assertEqual(raised.exception.phase,'close-in-progress')
        self.assertFalse(bus.closed)
        self.assertEqual(model.closed,before)
        bus._finalizer=None
        bus.close()
        self.assertTrue(bus.closed)

    def test_xapp_every_callback_reentry_sees_pending_without_repeat(self):
        value,calls=f.session(self.source)
        seen=[]
        class Reenter(f.Resource):
            def close(self):
                seen.append((value.close(),value._closed))
                super().close()
        for attr,name in (('_app_bus','bus'),('_capture_publication','publication'),
                          ('capture','capture'),('xd','X-connection')):
            setattr(value,attr,Reenter(name,calls))
        value.close()
        self.assertTrue(value._closed)
        self.assertEqual(len(seen),4)
        self.assertTrue(all(result['state']=='incomplete' and not closed for result,closed in seen))
        for name in ('bus','publication','capture','X-connection'):
            self.assertEqual(calls.count(name),1)
        self.assertIsNone(value._cleanup_active)
        self.assertFalse(value._cleanup_steps)

    def test_xapp_reentry_then_failure_keeps_resource_and_continues(self):
        value,calls=f.session(self.source)
        class Reenter(f.Resource):
            def close(self):
                value.close()
                super().close()
        bus=value._app_bus=Reenter('bus',calls,True)
        with self.assertRaises(self.source.xapp.SessionCleanupError): value.close()
        self.assertIs(value._app_bus,bus)
        self.assertFalse(value._closed)
        self.assertIsNone(value._cleanup_active)
        self.assertIn('supervisor',calls)
        completed=list(calls)
        bus.fail=False
        value.close()
        self.assertEqual(calls,completed+['bus'])
        self.assertTrue(value._closed)

    def test_other_xapp_closer_cannot_claim_success(self):
        value,calls=f.session(self.source)
        value._cleanup_active='other-inert-caller'
        with self.assertRaises(self.source.xapp.SessionCleanupError): value.close()
        self.assertFalse(value._closed)
        self.assertEqual(calls,[])
        value._cleanup_active=None
        value.close()
        self.assertTrue(value._closed)

    def test_xapp_retains_a_resource_whose_reentrant_close_is_pending(self):
        value,calls=f.session(self.source)
        state,model,_b=f.sdk(self.source)
        bus=value._app_bus=self.bus(state,model)
        bus._finalizer=self.source.bus.get_ident()
        with self.assertRaises(self.source.xapp.SessionCleanupError):value.close()
        self.assertFalse(value._closed)
        self.assertIs(value._app_bus,bus)
        self.assertNotIn('bus',value._cleanup_done)
        self.assertIn('supervisor',calls)
        bus._finalizer=None
        value.close()
        self.assertTrue(value._closed and bus.closed)
        self.assertIsNone(value._app_bus)


# The prior D1 assertion intentionally expects default delegation to be
# missing. Preserve it above and in the exact frozen reproduction, but replace
# its execution with the platform suite's automatic normal-route contracts.
original = Path(__file__).resolve().parents[3] / 'evidence/REVIEW152-independent_inert.py'
original_tree = ast.parse(original.read_text())
current_tree = ast.parse(Path(__file__).read_text())
def assertions(tree):
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Cases')
    return {n.name: ast.dump(n, include_attributes=False) for n in cls.body
            if isinstance(n, ast.FunctionDef) and n.name.startswith('test_')}
original_assertions, retained_assertions = assertions(original_tree), assertions(current_tree)
assert all(retained_assertions[k] == v for k, v in original_assertions.items())
names = [n for n in unittest.defaultTestLoader.getTestCaseNames(Cases)
         if n != 'test_unprovisioned_default_launch_refuses_before_birth']
result,log=f.run_suite(Cases,source,output/'independent.log',names)
mutants=[]
for name,file,old,new,test in [
    ('omit-primary-proof','bus_ownership.py','and self.ops.ready(self.primary)','and True','test_receipt_cannot_cover_live_primary'),
    ('omit-final-adoption','bus_ownership.py','if not ops.children() and ops.empty_children() and ops.now() < deadline:',
        'if ops.now() < deadline:','test_final_adoption_probe'),
    ('retry-released-construction-fd','bus_ownership.py','release(fd, self.closer, lambda: self.opened.remove(fd))',
        'release(fd, self.closer, lambda: None)','test_boundary_late_close_does_not_retry_descriptor_number'),
    ('drop-construction-retry-authority','bus_ownership.py','resource=allocation) from error',
        'resource=None) from error','test_boundary_rollback_retains_failed_authority'),
    ('forget-failed-watcher-fd','private_bus.py','self._watch_errors.append(error)\n                    if synchronous:',
        'self._watch_errors.append(error)\n                    self._watch_fd = None\n                    if synchronous:',
        'test_watcher_failed_release_remains_retryable'),
    ('erase-watcher-release-diagnostic','private_bus.py','unreported = self._watch_errors[self._watch_errors_reported:]',
        'unreported = []','test_background_watcher_fault_is_reported_even_after_release_retry'),
    ('permit-overlapping-bus-finalizers','private_bus.py','if self._finalizer is not None:',
        'if False:','test_other_bus_closer_gets_truthful_pending_and_can_retry'),
    ('permit-xapp-cleanup-overlap','xapp.py','if self._cleanup_active is not None:',
        'if False:','test_other_xapp_closer_cannot_claim_success'),
    ('forget-failed-xapp-step','xapp.py','if value is not None:\n                    result = value.close()',
        'if value is not None:\n                    self._cleanup_done.add(name)\n                    result = value.close()',
        'test_xapp_reentry_then_failure_keeps_resource_and_continues'),
    ('accept-pending-resource-as-closed','xapp.py','if isinstance(result, dict) and result.get("state") == "incomplete":',
        'if False:','test_xapp_retains_a_resource_whose_reentrant_close_is_pending')]:
    dest=output/'mutants'/name/'config/kilix_sdk'
    dest.mkdir(parents=True)
    for filename in ('bus_ownership.py','private_bus.py','bus_platform.py','bus_supervision.py','bus_supervisor.py','process_pin.py','xapp.py'):
        (dest/filename).write_bytes((source_root/'config/kilix_sdk'/filename).read_bytes())
    path=dest/file
    data=path.read_text()
    assert data.count(old)==1
    path.write_text(data.replace(old,new))
    mutant=f.load_source(dest.parents[1],'reviewer_mutant_'+name.replace('-','_'))
    null,null_log=f.run_suite(Cases,source,output/(name+'-null.log'),[test])
    changed,changed_log=f.run_suite(Cases,mutant,output/(name+'.log'),[test])
    assert null.testsRun==1 and null.wasSuccessful()
    assert changed.testsRun==1 and len(changed.failures)==1 and not changed.errors
    mutants.append(dict(name=name,test=test,file=file,old=old,new=new,
                        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                        failures=len(changed.failures),errors=len(changed.errors)))
report=dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),
            failure_names=[t.id() for t,_ in result.failures],observations=observations,
            mutants=mutants,bindings=f.BINDINGS,
            runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            fixture_library_sha256=hashlib.sha256((source_root/'tests/test_bus_ownership_inert.py').read_bytes()).hexdigest(),
            reviewer_assertions={k: hashlib.sha256(v.encode()).hexdigest() for k,v in original_assertions.items()},
            adaptation='All original test ASTs unchanged; R1 typed closer adapter; D1 superseded by automatic platform route tests; mutant delivery includes bus_platform.py',
            scope='inert source contracts; no native execution')
(output/'REPORT.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:report[k] for k in ('tests','failures','errors','mutants')}))
raise SystemExit(0 if result.wasSuccessful() else 1)
