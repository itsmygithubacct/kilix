"""Run the retained inert requirements in a disposable interpreter.

The historical review drivers also replay old baselines and mutations. Normal
discovery runs their current-source assertions, keeping their native-operation
denials out of the interpreter running the rest of the test suite.
"""
import ast
import os
from pathlib import Path
import sys
import unittest


def main():
    root = Path(__file__).resolve().parents[2]
    name, output = sys.argv[1], Path(sys.argv[2])
    source = root / 'tests/inert' / (name + '.py')
    pleb = Path(os.environ['KILIX_PLEB_TEST_SOURCE'])
    distro = Path(os.environ['KILIX_OS_TEST_SOURCE'])
    if name == 'bus_review_inert':
        sys.argv = [str(source), str(root), str(root), str(output)]
    else:
        sys.argv = [str(source), str(root), str(root), str(pleb), str(distro), str(output)]
    tree = ast.parse(source.read_bytes(), filename=str(source))
    definitions = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in ('result', 'original')
                or isinstance(target, ast.Tuple) and any(
                    isinstance(item, ast.Name) and item.id == 'result'
                    for item in target.elts)
                for target in node.targets):
            break
        definitions.append(node)
    namespace = {'__name__': name, '__file__': str(source)}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(source), 'exec'), namespace)
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for value in namespace.values():
        if isinstance(value, type) and issubclass(value, unittest.TestCase):
            names = loader.getTestCaseNames(value)
            # The retained review already excludes this historical assertion:
            # it predates automatic installation of the supervised platform.
            names = [item for item in names
                     if item != 'test_unprovisioned_default_launch_refuses_before_birth']
            suite.addTests(value(item) for item in names)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.testsRun:
        raise RuntimeError('inert driver discovered no requirements')
    raise SystemExit(not result.wasSuccessful())


if __name__ == '__main__':
    main()
