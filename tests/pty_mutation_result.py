"""Structured qualification of one selected unittest; text logs are advisory.

The command entrypoint belongs to the separately authorized operational driver.
Importing this module and exercising result classification requires no product.
"""
import argparse
import importlib.util
import inspect
import json
from pathlib import Path
import sys
import unittest

SCHEMA = "pty-mutation-unittest/v1"


def selected_assertion(test, error):
    """Require the assertion in the selected method, rather than fixture helpers."""
    if not isinstance(error[1], AssertionError):
        return False
    method = getattr(test, getattr(test, "_testMethodName", ""), None)
    code = getattr(inspect.unwrap(method), "__code__", None)
    trace, last = error[2], None
    while trace is not None:
        if not trace.tb_frame.f_globals.get("__unittest"):
            last = trace.tb_frame.f_code
        trace = trace.tb_next
    return code is not None and last is code


class QualificationResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started = []
        self.assertions = []

    def startTest(self, test):
        self.started.append(test.id())
        super().startTest(test)

    def addFailure(self, test, error):
        self.assertions.append({"test": test.id(), "assertion": selected_assertion(test, error)})
        super().addFailure(test, error)

    def addSubTest(self, test, subtest, error):
        if error is not None and issubclass(error[0], test.failureException):
            self.assertions.append({"test": test.id(), "subtest": subtest.id(),
                                    "assertion": selected_assertion(test, error)})
        super().addSubTest(test, subtest, error)


def result_document(result, selected):
    return {"schema": SCHEMA, "selected_test": selected, "tests_run": result.testsRun,
            "started": result.started, "failures": result.assertions,
            "errors": [test.id() for test, _ in result.errors],
            "skipped": [test.id() for test, _ in result.skipped],
            "expected_failures": [test.id() for test, _ in result.expectedFailures],
            "unexpected_successes": [test.id() for test in result.unexpectedSuccesses]}


def qualified_detection(code, document, selected_method):
    if code != 1 or not isinstance(document, dict) or document.get("schema") != SCHEMA:
        return False
    selected = document.get("selected_test")
    if not isinstance(selected, str) or selected.rsplit(".", 1)[-1] != selected_method:
        return False
    if type(document.get("tests_run")) is not int or document["tests_run"] != 1:
        return False
    if document.get("started") != [selected]:
        return False
    for field in ("errors", "skipped", "expected_failures", "unexpected_successes"):
        if document.get(field) != []:
            return False
    failures = document.get("failures")
    return (isinstance(failures, list) and bool(failures)
            and all(isinstance(row, dict) and row.get("test") == selected and row.get("assertion") is True
                    for row in failures))


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("module", type=Path)
    parser.add_argument("selected_method")
    parser.add_argument("result", type=Path)
    parser.add_argument("--old-broker")
    args = parser.parse_args(argv)
    # Preserve stale output rather than accidentally qualifying it as this run.
    if args.result.exists():
        raise FileExistsError(args.result)
    try:
        path = args.module.resolve()
        sys.path[:0] = [str(path.parent), str(path.parent.parent)]
        spec = importlib.util.spec_from_file_location("selected_mutation_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if args.old_broker:
            module.OLD = str(Path(args.old_broker).resolve())
        tests = [test for test in cases(unittest.TestLoader().loadTestsFromModule(module))
                 if test.id().rsplit(".", 1)[-1] == args.selected_method]
        if len(tests) != 1:
            raise ValueError("expected exactly one selected test")
    except Exception as error:
        args.result.write_text(json.dumps({"schema": SCHEMA, "setup_error": type(error).__name__}) + "\n")
        raise
    result = unittest.TextTestRunner(verbosity=2, resultclass=QualificationResult).run(unittest.TestSuite(tests))
    args.result.write_text(json.dumps(result_document(result, tests[0].id()), indent=2) + "\n")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
