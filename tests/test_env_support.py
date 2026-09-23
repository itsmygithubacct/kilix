"""Checks on the environment a test hands to a child process.

These exist because Kilix's suite reported 13 failures when run inside a
running Kilix session and none when run outside one. The tests were reading the
machine. A regression here invalidates results elsewhere rather than merely
failing on its own.

The capability is **no raw environment reaches a child**: a child gets
``sandbox_env()``'s output, or the process environment at one of a named,
reviewed set of places, and nowhere else. It is checked in two ways. A real
child is started with a live-looking session planted in this process and is
shown to receive none of it. And every place in ``tests/`` that reads the
*whole* process environment -- copies, spreads, iterates or passes it, in any
spelling -- is found by walking the syntax tree, not by searching for a few
strings, and must be exactly the named set. This replaced a bare budget of one
module that counted matches of three spellings: a needle-free respelling of
the permitted module took it to zero, and a real new offender then fitted in
the spare slot with the suite green (M0-KILIX-VERIFY F3).
"""

import ast
import collections
import json
import os
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _env_support import STACK_PREFIXES, sandbox_env  # noqa: E402

TESTS_DIR = pathlib.Path(__file__).resolve().parent

#: Every statement in tests/ that reads the whole process environment, keyed by
#: module, enclosing function and the statement's exact text, with the reason
#: it is allowed. The key is the statement, so a respelling of a permitted site
#: is a visible change here rather than a free slot for a new one.
PERMITTED_WHOLE_ENVIRONMENT_READS = {
    ("_env_support.py", "sandbox_env",
     "env = {key: value for key, value in os.environ.items() "
     "if not key.startswith(STACK_PREFIXES)}"):
        "the sanitiser itself: it reads the environment in order to strip it",
    ("test_laptop_verb.py", "LaptopVerbTests.test_status_reports_every_profile_state",
     "result = subprocess.run([sys.executable, str(ROOT / 'config' / 'laptop.py'), "
     "'status'], capture_output=True, text=True, env=dict(os.environ))"):
        "os.environ is this module's fixture: setUp sets the sandbox INTO it, "
        "with cleanup; converting it was tried and reverted, and the suite caught it",
    ("test_laptop_verb.py", "LaptopVerbTests.test_help_carries_the_probe_token",
     "result = subprocess.run([sys.executable, str(ROOT / 'config' / 'laptop.py'), "
     "'help'], capture_output=True, text=True, env=dict(os.environ))"):
        "as above",
    ("test_laptop_verb.py", "LaptopVerbTests.test_usage_errors_exit_two",
     "result = subprocess.run([sys.executable, str(ROOT / 'config' / 'laptop.py'), "
     "*argv], capture_output=True, text=True, env=dict(os.environ))"):
        "as above",
}

_ENVIRONMENT_NAMES = frozenset(("environ", "environb"))
# A use of one key, or an in-process edit, reads no whole environment.
_PER_KEY_METHODS = frozenset(("get", "setdefault", "pop", "update", "clear",
                              "__contains__", "__getitem__", "__setitem__",
                              "__delitem__"))


def _names_bound_to_the_environment(tree):
    """``from os import environ as E`` makes ``E`` the environment too."""
    names = set(_ENVIRONMENT_NAMES)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ("os", "posix", "nt"):
            names.update(alias.asname or alias.name for alias in node.names
                         if alias.name in _ENVIRONMENT_NAMES)
    return names


def _is_the_environment(node, names):
    if isinstance(node, ast.Attribute):
        return node.attr in _ENVIRONMENT_NAMES
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id == "getattr" and len(node.args) >= 2 \
            and isinstance(node.args[1], ast.Constant) \
            and node.args[1].value in _ENVIRONMENT_NAMES:
        return True
    return isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
        and node.slice.value in _ENVIRONMENT_NAMES


def _reads_it_whole(node, parent):
    if isinstance(parent, ast.Subscript) and parent.value is node:
        return False                                   # os.environ["KEY"]
    if isinstance(parent, ast.Attribute) and parent.value is node \
            and parent.attr in _PER_KEY_METHODS:
        return False                                   # os.environ.get("KEY")
    if isinstance(parent, ast.Compare) and node in parent.comparators \
            and all(isinstance(op, (ast.In, ast.NotIn)) for op in parent.ops):
        return False                                   # "KEY" in os.environ
    if isinstance(parent, ast.Call) and parent.args and parent.args[0] is node \
            and isinstance(parent.func, ast.Attribute) and parent.func.attr == "dict" \
            and ast.unparse(parent.func.value).split(".")[-1] == "patch":
        return False                                   # mock.patch.dict(os.environ, ...)
    return not isinstance(parent, (ast.Delete, ast.alias, ast.ImportFrom))


def whole_environment_reads(source, module="<snippet>"):
    """(module, enclosing function, statement) for every whole-environment read."""
    tree = ast.parse(source)
    names = _names_bound_to_the_environment(tree)
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    found = []
    for node in ast.walk(tree):
        if not _is_the_environment(node, names) or node not in parents:
            continue
        parent = parents[node]
        if isinstance(parent, ast.Attribute) and _is_the_environment(parent, names):
            continue                                   # the inner half of os.environ.environ
        if not _reads_it_whole(node, parent):
            continue
        statement, scope, cursor = None, [], node
        while cursor in parents:
            cursor = parents[cursor]
            if statement is None and isinstance(cursor, ast.stmt):
                statement = cursor
            if isinstance(cursor, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                scope.append(cursor.name)
        text = ast.unparse(statement) if statement is not None else ast.unparse(node)
        found.append((module, ".".join(reversed(scope)) or "<module>",
                      text.splitlines()[0] if isinstance(statement, (ast.For, ast.While,
                      ast.If, ast.With, ast.Try, ast.FunctionDef)) else text))
    return found


class SandboxEnvTests(unittest.TestCase):
    def test_a_live_session_cannot_reach_the_child(self):
        # Paths under /nonexistent, not a plausible /home/<name>: the
        # publication hygiene gate treats any /home/<someone> string as a
        # personal path leak, and a fixture is not worth an allowlist entry.
        live = {
            "KILIX_SESSION_HOME": "/nonexistent/live/kilix/session",
            "KILIX95_STORAGE_HOME": "/nonexistent/live",
            "GPU_TERMINAL_HOME": "/nonexistent/live",
            "KITTY_WINDOW_ID": "7",
        }
        with mock.patch.dict(os.environ, live):
            env = sandbox_env(PATH="/usr/bin:/bin")
        for key in live:
            self.assertNotIn(key, env, key)
        self.assertEqual(env["PATH"], "/usr/bin:/bin")

    def test_the_control_an_unrelated_name_survives(self):
        # Without this, "strips the session" and "returns almost nothing" are
        # the same observation.
        env = sandbox_env()
        self.assertIn("PATH", env)

    def test_overrides_win_over_stripping(self):
        # The usual case: a test names a variable whose whole family was just
        # removed, in order to point the child at its sandbox instead.
        env = sandbox_env(KILIX_STORAGE_HOME="/sandbox/storage")
        self.assertEqual(env["KILIX_STORAGE_HOME"], "/sandbox/storage")

    def test_the_prefix_family_covers_the_95_variables(self):
        # KILIX carries no trailing underscore precisely so KILIX95_* is caught
        # by it. Spelled out because an "obvious" tidy-up to "KILIX_" would
        # silently reopen the hole.
        self.assertTrue("KILIX95_STORAGE_HOME".startswith(STACK_PREFIXES))

    def test_a_real_child_given_sandbox_env_sees_none_of_the_session(self):
        # The capability itself, in a real child process rather than a dict:
        # one planted variable per stack prefix, plus an unrelated control.
        planted = {f"{prefix}_PLANTED_CANARY": "/nonexistent/live"
                   for prefix in STACK_PREFIXES}
        planted["UNRELATED_PLANTED_CANARY"] = "kept"
        probe = ("import json, os; print(json.dumps(sorted(k for k in os.environ "
                 "if k.endswith('_PLANTED_CANARY'))))")

        def child(env):
            result = subprocess.run([sys.executable, "-c", probe], env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        with mock.patch.dict(os.environ, planted):
            sanitised = child(sandbox_env())
            inherited = child(None)
        self.assertEqual(sanitised, ["UNRELATED_PLANTED_CANARY"])
        # The control: a child that inherits this process sees every plant,
        # so the empty result above is the sanitiser and not a blind probe.
        self.assertEqual(inherited, sorted(planted))


class WholeEnvironmentReadTests(unittest.TestCase):
    def test_the_scanner_sees_every_spelling_of_a_raw_copy(self):
        raw = (
            "env = dict(os.environ)",
            "env = dict(os.environ, KILIX_PLANTED='1')",
            "env = os.environ.copy()",
            "env = {**os.environ}",
            "env = {key: value for key, value in os.environ.items()}",
            "env = os.environ | {'A': '1'}",
            "env = copy.copy(os.environ)",
            "env = dict(**os.environ)",
            "env = dict(os.environ.items())",
            "subprocess.run(['true'], env=os.environ)",
            "alias = os.environ",
            "env = dict(getattr(os, 'environ'))",
            "env = dict(vars(os)['environ'])",
            "env = os.environb.copy()",
            "import os as system\nenv = dict(system.environ)",
            "from os import environ as E\nenv = dict(E)",
            "for key in os.environ:\n    pass",
            "env = {k: os.environ[k] for k in list(os.environ)}",
        )
        for source in raw:
            with self.subTest(source=source):
                self.assertEqual(len(whole_environment_reads(source)), 1, source)
        clean = (
            "value = os.environ['HOME']",
            "value = os.environ.get('HOME')",
            "present = 'HOME' in os.environ",
            "absent = 'HOME' not in os.environ",
            "os.environ.pop('HOME', None)",
            "os.environ.update(HOME='/x')",
            "os.environ['HOME'] = '/x'",
            "del os.environ['HOME']",
            "with mock.patch.dict(os.environ, {'HOME': '/x'}):\n    pass",
            "with patch.dict(os.environ, {'HOME': '/x'}, clear=True):\n    pass",
            "env = sandbox_env(HOME='/x')",
            "env = {k: v for k, v in sandbox_env().items() if k != 'COLUMNS'}",
        )
        for source in clean:
            with self.subTest(source=source):
                self.assertEqual(whole_environment_reads(source), [], source)

    def test_no_raw_environment_reaches_a_child_outside_the_named_sites(self):
        found = collections.Counter()
        for path in sorted(TESTS_DIR.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            found.update(whole_environment_reads(path.read_text(), path.name))
        repeated = sorted(key for key, count in found.items() if count > 1)
        unexpected = sorted(set(found) - set(PERMITTED_WHOLE_ENVIRONMENT_READS))
        missing = sorted(set(PERMITTED_WHOLE_ENVIRONMENT_READS) - set(found))

        def lines(keys):
            return "".join(f"\n  {module} {scope}: {text}" for module, scope, text in keys)

        self.assertEqual(
            (unexpected, repeated), ([], []),
            "a test reads the whole process environment where no child may get it; "
            "build the child environment with sandbox_env() from _env_support:"
            + lines(unexpected + repeated))
        self.assertEqual(
            missing, [],
            "a permitted site changed or went away; if it was converted to "
            "sandbox_env(), delete its entry, and if it was respelled, the new "
            "spelling needs the same review the old one had:" + lines(missing))
        for key, reason in PERMITTED_WHOLE_ENVIRONMENT_READS.items():
            self.assertTrue(reason.strip(), key)


if __name__ == "__main__":
    unittest.main()
