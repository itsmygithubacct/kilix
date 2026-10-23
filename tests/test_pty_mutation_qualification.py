"""Pure unittest classification and fake-filesystem proxy cleanup controls."""
import ast
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BROKER = ROOT / "third_party" / "kitty-pty-broker"
SELECTED = "test_guard"


def qualifier():
    path = ROOT / "tests/pty_mutation_result.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("pure_qualification", path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def cleanup():
    tree = ast.parse((BROKER / "tests/test_selector.py").read_bytes())
    function = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "restore_proxy_files"), None)
    if function is None:
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "RealBroker")
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                      and n.name == "test_revalidates_startup_facts_after_census_before_dispatch")
        final = next(n.finalbody for n in method.body if isinstance(n, ast.Try) and n.finalbody)
        # Execute only the existing file-restoration statements, never the test.
        body = [n for n in final if any(isinstance(x, ast.Name) and x.id in ("path", "moved", "metadata")
                                      for x in ast.walk(n))]
        function = ast.FunctionDef(name="restore_proxy_files", args=ast.arguments(
            posonlyargs=[], args=[ast.arg(arg=x) for x in ("path", "moved", "metadata", "original")],
            kwonlyargs=[], kw_defaults=[], defaults=[]), body=body, decorator_list=[])
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
                 "<actual-pure-proxy-cleanup>", "exec"), namespace)
    return namespace["restore_proxy_files"]


class MemoryPath:
    def __init__(self, files, name, *, parent_exists=True, fault=None):
        self.files, self.name = files, name
        self.parent = self
        self.parent_exists, self.fault = parent_exists, fault
    def stat(self):
        if self.fault == "stat":
            raise PermissionError("unrelated parent stat fault")
        if not self.parent_exists:
            raise FileNotFoundError("ended session directory")
        return object()
    def unlink(self, missing_ok=False):
        if self.fault == "unlink":
            raise PermissionError("unrelated unlink fault")
        if self.name not in self.files:
            if not missing_ok:
                raise FileNotFoundError(self.name)
        else:
            del self.files[self.name]
    def rename(self, target):
        if self.name not in self.files:
            raise FileNotFoundError(self.name)
        target.files[target.name] = self.files.pop(self.name)
    def write_bytes(self, raw):
        if self.fault == "write":
            raise PermissionError("unrelated metadata write fault")
        self.files[self.name] = raw


def run_fixture(mode):
    class InertGuard(unittest.TestCase):
        def setUp(self):
            if mode == "setup_error":
                raise RuntimeError("fixture setup failed")
            if mode == "setup_assertion":
                self.fail("fixture setup assertion")
        def tearDown(self):
            if mode == "teardown_error":
                raise FileNotFoundError("fixture cleanup failed")
            if mode == "cleanup_assertion":
                self.fail("fixture cleanup assertion")
        def test_guard(self):
            if mode == "helper_assertion":
                def fixture_check():
                    self.fail("fixture helper assertion")
                fixture_check()
            if mode == "skip":
                self.skipTest("no qualification")
            if mode == "mixed_error":
                try:
                    self.assertEqual((0, True), (3, False))
                finally:
                    raise FileNotFoundError("fixture cleanup failed")
            if mode in ("assertion", "teardown_error"):
                self.assertEqual((0, True), (3, False))
            if mode == "runtime_error":
                raise RuntimeError("fixture runtime failed")
    case = InertGuard(SELECTED)
    output = io.StringIO()
    module = qualifier()
    options = {"resultclass": module.QualificationResult} if module else {}
    result = unittest.TextTestRunner(stream=output, verbosity=2, **options).run(unittest.TestSuite([case]))
    document = module.result_document(result, case.id()) if module else None
    return result, document, output.getvalue(), module


def detected(code, document, log, module, test=SELECTED):
    if module:
        return module.qualified_detection(code, document, test)
    tree = ast.parse((ROOT / "tests/mutate_pty_selector.py").read_bytes())
    expression = next(n.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "killed" for t in n.targets))
    return eval(compile(ast.Expression(expression), "<old-actual-detection-predicate>", "eval"),
                {"code": code, "content": log, "test": test})


class MutationQualification(unittest.TestCase):
    def test_clean_selected_assertion_is_detected(self):
        _, doc, log, module = run_fixture("assertion")
        self.assertTrue(detected(1, doc, log, module))

    def test_mixed_assertion_cleanup_error_is_unqualified(self):
        result, doc, log, module = run_fixture("mixed_error")
        self.assertIn("AssertionError", log)
        self.assertIn("FAILED (errors=1)", log)
        self.assertEqual((len(result.failures), len(result.errors)), (0, 1))
        self.assertFalse(detected(1, doc, log, module))

    def test_passing_setup_runtime_skip_and_wrong_selected_controls(self):
        for mode in ("passing", "setup_error", "runtime_error", "teardown_error", "skip",
                     "setup_assertion", "cleanup_assertion", "helper_assertion"):
            result, doc, log, module = run_fixture(mode)
            with self.subTest(mode=mode):
                self.assertFalse(detected(0 if result.wasSuccessful() else 1, doc, log, module))
        _, doc, log, module = run_fixture("assertion")
        self.assertFalse(detected(1, doc, log, module, test="test_other"))
        self.assertFalse(detected(0, doc, log, module))

    def test_missing_malformed_and_mismatched_structured_results_are_unqualified(self):
        _, doc, log, module = run_fixture("assertion")
        for bad in (None, {}, [], {"schema": "other"}):
            with self.subTest(document=bad):
                self.assertFalse(detected(1, bad, log, module))
        if module:
            for field, value in (("tests_run", 0), ("tests_run", 2), ("skipped", ["test_guard"]),
                                 ("errors", ["test_guard"]), ("expected_failures", ["test_guard"]),
                                 ("unexpected_successes", ["test_guard"]), ("started", ["another_test"])):
                bad = {**doc, field: value}
                with self.subTest(field=field):
                    self.assertFalse(module.qualified_detection(1, bad, SELECTED))

    def test_proxy_cleanup_restores_existing_target_with_memory_files(self):
        files = {"socket": b"proxy", "original": b"receiver", "metadata": b"changed"}
        cleanup()(MemoryPath(files, "socket"), MemoryPath(files, "original"), MemoryPath(files, "metadata"), b"original")
        self.assertEqual(files, {"socket": b"receiver", "metadata": b"original"})

    def test_proxy_cleanup_tolerates_only_missing_ended_session_directory(self):
        files = {}
        try:
            cleanup()(MemoryPath(files, "socket", parent_exists=False),
                      MemoryPath(files, "original", parent_exists=False),
                      MemoryPath(files, "metadata", parent_exists=False), b"original")
        except FileNotFoundError:
            self.fail("cleanup lost the original guard assertion after an ended fixture")
        self.assertFalse(files)

    def test_proxy_cleanup_does_not_swallow_unrelated_filesystem_errors(self):
        for fault in ("unlink", "write"):
            files = {"socket": b"proxy", "original": b"receiver"}
            with self.subTest(fault=fault), self.assertRaises(PermissionError):
                cleanup()(MemoryPath(files, "socket", fault=fault), MemoryPath(files, "original"),
                          MemoryPath(files, "metadata", fault=fault), b"original")
        for parent_fault in (None, "stat"):
            expected = FileNotFoundError if parent_fault is None else PermissionError
            with self.subTest(parent_fault=parent_fault), self.assertRaises(expected):
                cleanup()(MemoryPath({}, "socket", fault=parent_fault), MemoryPath({}, "original"),
                          MemoryPath({}, "metadata"), b"original")

    def test_selected_runner_structured_results_with_pure_temporary_modules(self):
        module = qualifier()
        self.assertIsNotNone(module)
        with tempfile.TemporaryDirectory(prefix="pure-selected-unittest-") as directory:
            root = Path(directory)
            for mode in ("assertion", "mixed_error", "skip", "passing"):
                path = root / (mode + ".py")
                body = ("self.assertEqual((0, True), (3, False))" if mode == "assertion" else
                        "self.skipTest('pure skip')" if mode == "skip" else "pass")
                if mode == "mixed_error":
                    body = "try:\n            self.assertEqual((0, True), (3, False))\n        finally:\n            raise FileNotFoundError('pure cleanup error')"
                path.write_text("import unittest\nclass Inert(unittest.TestCase):\n    def test_guard(self):\n        " + body + "\n")
                result = root / (mode + ".json")
                with self.subTest(mode=mode):
                    code = module.main([str(path), SELECTED, str(result)])
                    document = json.loads(result.read_text())
                    self.assertEqual(module.qualified_detection(code, document, SELECTED), mode == "assertion")
                    self.assertEqual(document["tests_run"], 1)
                    if mode == "mixed_error":
                        self.assertEqual((len(document["failures"]), len(document["errors"])), (0, 1))
                    with self.assertRaises(FileExistsError):
                        module.main([str(path), SELECTED, str(result)])

    def test_expected_unexpected_success_subtest_and_nonassertion_result_controls(self):
        module = qualifier()
        self.assertIsNotNone(module)
        class Pure(unittest.TestCase):
            @unittest.expectedFailure
            def test_expected(self):
                self.fail("expected failure")
            @unittest.expectedFailure
            def test_unexpected(self):
                pass
            def test_subtest(self):
                with self.subTest(value=7):
                    self.assertEqual(7, 8)
        for method, qualifies in (("test_expected", False), ("test_unexpected", False), ("test_subtest", True)):
            case = Pure(method)
            result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=module.QualificationResult).run(case)
            with self.subTest(method=method):
                self.assertEqual(module.qualified_detection(1, module.result_document(result, case.id()), method), qualifies)
        class NonAssertion(unittest.TestCase):
            failureException = RuntimeError
            def test_guard(self):
                raise RuntimeError("an ERROR must not be recast as a guard assertion")
        case = NonAssertion(SELECTED)
        result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=module.QualificationResult).run(case)
        self.assertEqual(len(result.failures), 1)
        self.assertFalse(module.qualified_detection(1, module.result_document(result, case.id()), SELECTED))

    def test_selected_runner_binds_old_fixture_and_driver_rejects_stale_result(self):
        module = qualifier()
        with tempfile.TemporaryDirectory(prefix="pure-old-binding-") as directory:
            root = Path(directory)
            path = root / "test_memory.py"
            path.write_text("import unittest\nOLD = None\nclass Memory(unittest.TestCase):\n"
                            "    def test_guard(self):\n        if not OLD: self.skipTest('binding absent')\n"
                            "        self.assertIsNone(OLD)\n")
            result = root / "result.json"
            code = module.main([str(path), SELECTED, str(result), "--old-broker", str(root / "never-executed")])
            document = json.loads(result.read_text())
            self.assertTrue(module.qualified_detection(code, document, SELECTED))
            self.assertEqual(document["skipped"], [])
        tree = ast.parse((ROOT / "tests/mutate_pty_selector.py").read_bytes())
        guards = [node for node in ast.walk(tree) if isinstance(node, ast.If)
                  and ast.unparse(node.test) == "structured.exists()"]
        self.assertEqual(len(guards), 1)
        self.assertIsInstance(guards[0].body[0], ast.Raise)
        broker = ast.parse((BROKER / "tests/test_selector.py").read_bytes())
        old_test = next(node for node in ast.walk(broker) if isinstance(node, ast.FunctionDef)
                        and node.name == "test_older_receiver_refuses_required_attachment_without_fallback")
        self.assertEqual(old_test.decorator_list, [])
        self.assertEqual(ast.unparse(old_test.body[0].test), "not OLD")
