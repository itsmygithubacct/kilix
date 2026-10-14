"""Independent actual SDK prepare/retire branches; exclusively frozen memory adapters."""
import ast, hashlib, json, sys, unittest
from pathlib import Path
ROOT, OLD, PLEB, OS_ROOT, OUT = map(Path, sys.argv[1:6])
fixture = ROOT / 'tests/test_bus_loss_inert.py'
tree = ast.parse(fixture.read_bytes(), filename=str(fixture))
selected = []
for node in tree.body:
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'result' for t in node.targets):
        break
    selected.append(node)
ns = {'__name__': 'independent_fix2_definitions', '__file__': str(fixture)}
selected_methods = sys.argv[6:]
sys.argv = sys.argv[:6]
exec(compile(ast.Module(body=selected, type_ignores=[]), str(fixture), 'exec'), ns)
H, CURRENT, F = ns['H'], ns['CURRENT'], ns['LossTests']()
observations = []

def clean_prelaunch(n):
    # The inherited reusable family model reserves descendants. This control
    # sends no launch and represents no caller births before prepare.
    for key,node in list(n.ops.nodes.items()):
        if node.instance == 'A' and node.scope in ('outer','inner'):
            del n.ops.nodes[key]

class ReviewControls(unittest.TestCase):
    def normal(self, live_wait):
        with F.frontend(launch=False) as (p, n, manager, client, server):
            clean_prelaunch(n)
            p.prepare()
            n.ops.exit(n.ops.nodes['platform-root'])
            # Abrupt controller loss can be collected by PID1 while its child
            # survives in the deliberately separate wait scope.
            manager.unit = lambda *_args: None
            if not live_wait:
                n.ops.exit(n.ops.nodes['wait-worker'])
                n.ops.nodes['wait-worker'].reaped = True
            error = None
            try:
                p.retire(timeout=.03)
            except CURRENT.own.CleanupIncomplete as caught:
                error = str(caught)
            row = dict(case='complete-handshake-' + str(live_wait), error=error,
                       controller_garbage_collected=True, closed=p.closed, wait_alive=n.ops.nodes['wait-worker'].live,
                       wait_owner=p.wait_owner, fd_retained=510099 not in n.ops.closed,
                       foreign_survived=n.ops.nodes['foreign'].live,
                       numeric_closes=list(n.ops.closed))
            observations.append(row)
            if live_wait:
                self.assertIsNotNone(error)
                self.assertFalse(p.closed)
                self.assertTrue(row['fd_retained'])
            else:
                self.assertIsNone(error)
                self.assertTrue(p.closed)
                self.assertEqual(n.ops.closed.count(510099), 1)
            self.assertTrue(row['foreign_survived'])

    def test_complete_handshake_retains_live_wait_owner(self):
        self.normal(True)

    def test_complete_handshake_collects_dead_wait_owner(self):
        self.normal(False)

    def interrupted(self, point):
        with F.frontend(launch=False) as (p, n, manager, client, server):
            clean_prelaunch(n)
            if point == 'received-fd-captured':
                capture = p.handles.capture
                def cancel(fds):
                    capture(fds)
                    if 510099 in fds:
                        raise KeyboardInterrupt('inert cancellation after exact receive capture')
                p.handles.capture = cancel
            elif point == 'authenticated-handoff-retained':
                class CancelPublication(CURRENT.supervision.SupervisedPlatform):
                    def __setattr__(self, key, value):
                        if key == 'wait_owner' and getattr(self, '_cancel_owner', False):
                            self._cancel_owner = False
                            raise KeyboardInterrupt('inert cancellation before owner publication')
                        super().__setattr__(key, value)
                p.__class__ = CancelPublication
                p._cancel_owner = True
            elif point == 'scope-authentication':
                scope = manager.scope
                def cancel(name, deadline):
                    scope(name, deadline)
                    raise KeyboardInterrupt('inert cancellation after manager lookup')
                manager.scope = cancel
            else:
                raise AssertionError(point)
            with self.assertRaises(KeyboardInterrupt) as caught:
                p.prepare()
            self.assertIs(caught.exception.resource, p)
            self.assertIn(510099, p.handles.fds)
            self.assertIsNone(p.wait_owner)
            n.ops.exit(n.ops.nodes['platform-root'])
            # Abrupt controller loss can be collected by PID1 while its child
            # survives in the deliberately separate wait scope.
            manager.unit = lambda *_args: None
            error = None
            try:
                p.retire(timeout=.03)
            except CURRENT.own.CleanupIncomplete as caught:
                error = str(caught)
            row = dict(case=point, error=error, controller_garbage_collected=True, closed=p.closed,
                       wait_alive=n.ops.nodes['wait-worker'].live,
                       fd_retained=510099 not in n.ops.closed,
                       handoff_retained=p.wait_handoff is not None,
                       foreign_survived=n.ops.nodes['foreign'].live,
                       numeric_closes=list(n.ops.closed))
            observations.append(row)
            self.assertFalse(p.closed, 'SDK declared retirement while its separately placed wait owner remained alive')
            self.assertIsNotNone(error)
            self.assertTrue(row['fd_retained'])

    def test_capture_cancellation_keeps_separate_wait_owner_obligation(self):
        self.interrupted('received-fd-captured')

    def test_publication_cancellation_keeps_separate_wait_owner_obligation(self):
        self.interrupted('authenticated-handoff-retained')

    def test_authentication_cancellation_keeps_separate_wait_owner_obligation(self):
        self.interrupted('scope-authentication')

class RetryControls(ReviewControls):
    def retry_case(self, point, die_before_retry):
        with F.frontend(launch=False) as (p, n, manager, client, server):
            clean_prelaunch(n)
            if point == 'capture':
                original = p.handles.capture
                def cancel(fds):
                    original(fds)
                    if 510099 in fds:
                        p.handles.capture = original
                        raise KeyboardInterrupt('one-shot capture cancellation')
                p.handles.capture = cancel
            else:
                original = manager.scope
                def cancel(*args):
                    manager.scope = original
                    original(*args)
                    raise KeyboardInterrupt('one-shot authentication cancellation')
                manager.scope = cancel
            with self.assertRaises(KeyboardInterrupt):
                p.prepare()
            n.ops.exit(n.ops.nodes['platform-root'])
            manager.unit = lambda *_args: None
            if not die_before_retry:
                with self.assertRaises(CURRENT.own.CleanupIncomplete):
                    p.retire(timeout=.03)
                self.assertFalse(p.closed)
                self.assertNotIn(510099, n.ops.closed)
            n.ops.exit(n.ops.nodes['wait-worker'])
            n.ops.nodes['wait-worker'].reaped = True
            error = None
            try:
                p.retire(timeout=.03)
            except CURRENT.own.CleanupIncomplete as caught:
                error = str(caught)
            observations.append(dict(case='retry-'+point, die_before_retry=die_before_retry, error=error, closed=p.closed))
            self.assertIsNone(error, 'exact dead received wait process and absent authenticated manager scope must eventually retire')
            self.assertTrue(p.closed)
            if die_before_retry:
                self.assertIsNone(p.wait_owner)
                self.assertIsNone(p.wait_handoff)
            self.assertEqual(n.ops.closed.count(510099), 1)
            self.assertTrue(n.ops.nodes['foreign'].live)
    def test_retry_authenticate_while_live_then_collect(self):
        for point in ('capture','authentication'):
            with self.subTest(point=point):self.retry_case(point, False)
    def test_dead_before_first_retry_can_collect(self):
        for point in ('capture','authentication'):
            with self.subTest(point=point):self.retry_case(point, True)

    def test_pending_adverse_proofs_preserve_ownership(self):
        for case in ('foreign-pid', 'live', 'scope-present', 'scope-replaced', 'malformed'):
            with self.subTest(case=case), F.frontend(launch=False) as (p, n, manager, client, server):
                clean_prelaunch(n)
                original = p.handles.capture
                def cancel(fds):
                    original(fds)
                    if 510099 in fds:
                        p.handles.capture = original
                        raise KeyboardInterrupt('one-shot retained capture')
                p.handles.capture = cancel
                with self.assertRaises(KeyboardInterrupt):p.prepare()
                record = manager.scope('unused', 1)
                n.ops.exit(n.ops.nodes['platform-root'])
                manager.unit = lambda *_args: None
                if case != 'live':n.ops.exit(n.ops.nodes['wait-worker'])
                if case == 'foreign-pid':
                    original_pid = n.ops.pidfd_pid
                    n.ops.pidfd_pid = lambda fd: 999999 if fd == 510099 else original_pid(fd)
                elif case in ('scope-present', 'scope-replaced'):
                    if case == 'scope-replaced':record['InvocationID'] = b'3'*16
                    manager.scope = lambda *_args: record
                elif case == 'malformed':
                    data, fds, _, flags = p.pending_wait_packet
                    p.pending_wait_packet = (data, fds, True, flags)
                with self.assertRaises(CURRENT.own.CleanupIncomplete):p.retire(timeout=.03)
                self.assertFalse(p.closed)
                self.assertFalse(p.pending_wait_collected)
                self.assertNotIn(510099, n.ops.closed)
                self.assertTrue(n.ops.nodes['foreign'].live)

with (OUT/'tests.log').open('x') as log:
    suite = unittest.TestSuite([ReviewControls(name) for name in selected_methods]) if selected_methods else unittest.defaultTestLoader.loadTestsFromTestCase(RetryControls)
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
report = dict(tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
              failure_names=[t.id() for t, _ in result.failures], observations=observations,
              bindings=H.BINDINGS, fixture_sha256=hashlib.sha256(fixture.read_bytes()).hexdigest(),
              definitions_ast_sha256=hashlib.sha256(ast.dump(ast.Module(body=selected,type_ignores=[]),include_attributes=True).encode()).hexdigest(),
              native_effects=0, scope='real prepare/retire source, exact retained frozen memory adapters; no native lifetime claims')
(OUT/'REPORT.json').write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps({k: report[k] for k in ('tests','failures','errors','failure_names','observations')}))
raise SystemExit(0 if result.wasSuccessful() else 1)
