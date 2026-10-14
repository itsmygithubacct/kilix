"""Small, explicit workflow contracts and offline artifact checks.

Acceptance data belongs to the operator, outside the agent workspace. Generated
Python runs in a networkless mount/PID namespace with only fixture files visible.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path, PurePosixPath
import re
import resource
import signal
import subprocess
import tempfile

SCOPES = {
    "file-summary": "structured-facts-v1",
    "github-actions": "offline-workflow-v1",
    "bug-fix": "python-apply-v1",
    "test-writing": "python-unittest-mutants-v1",
}


def exact(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("invalid " + label + " fields")


def relative(value):
    if (not isinstance(value, str) or not value or len(value) > 200 or
            "\\" in value or any(ord(c) < 32 for c in value)):
        raise ValueError("invalid workspace path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(x in ("", ".", "..") for x in value.split("/")):
        raise ValueError("workspace paths must be relative without dot components")
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))


def validate(task, acceptance, workflow, scope):
    exact(task, ("schema", "workflow", "scope", "brief", "inputs", "output",
                 "acceptance_file", "acceptance_sha256"), "task")
    if (task["schema"] != "kilix.agent-task/v1" or task["workflow"] != workflow or
            task["scope"] != scope or SCOPES.get(workflow) != scope):
        raise ValueError("task is outside the registered workflow scope")
    relative(task["brief"]); relative(task["output"])
    inputs = task["inputs"]
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= 32:
        raise ValueError("task requires 1–32 protected input files")
    for name in inputs:
        relative(name)
    if len(set(inputs)) != len(inputs) or task["brief"] not in inputs or task["output"] in inputs:
        raise ValueError("brief must be protected and output must be separately editable")
    canonical(acceptance)
    if workflow == "file-summary":
        exact(acceptance, ("facts", "sources"), "summary acceptance")
        if (not isinstance(acceptance["facts"], dict) or not acceptance["facts"] or
                not isinstance(acceptance["sources"], dict) or set(acceptance["sources"]) != set(acceptance["facts"])):
            raise ValueError("summary requires facts and a matching citation map")
        for citation in acceptance["sources"].values():
            exact(citation, ("file", "line"), "citation")
            if citation["file"] not in inputs or type(citation["line"]) is not int or citation["line"] < 1:
                raise ValueError("citations require a protected file and a positive line number")
    elif workflow == "github-actions":
        exact(acceptance, ("workflow",), "CI acceptance")
        if not isinstance(acceptance["workflow"], dict) or not acceptance["workflow"]:
            raise ValueError("CI acceptance requires a workflow mapping")
    elif workflow == "bug-fix":
        exact(acceptance, ("vectors",), "Python acceptance")
        if task["output"] != "engine.py" or not isinstance(acceptance["vectors"], list) or not 1 <= len(acceptance["vectors"]) <= 200:
            raise ValueError("Python repair requires engine.py and 1–200 vectors")
        for vector in acceptance["vectors"]:
            exact(vector, ("args", "expected"), "test vector")
            if not isinstance(vector["args"], list):
                raise ValueError("vector args must be a positional argument list")
    else:
        exact(acceptance, ("reference", "mutants"), "test acceptance")
        if task["output"] != "tests/test_engine.py" or "engine.py" not in inputs:
            raise ValueError("test writing requires protected engine.py and tests/test_engine.py")
        if (not isinstance(acceptance["reference"], str) or
                not isinstance(acceptance["mutants"], list) or len(acceptance["mutants"]) != 3 or
                not all(isinstance(s, str) and s != acceptance["reference"] for s in acceptance["mutants"]) or
                len(set(acceptance["mutants"])) != 3):
            raise ValueError("test acceptance requires a reference and three distinct mutants")
        for source in [acceptance["reference"], *acceptance["mutants"]]:
            compile(source, "engine.py", "exec")


def isolated_python(files, source):
    """Bounded subprocess; no host home, network, agent credentials or acceptance files."""
    with tempfile.TemporaryDirectory(prefix="kilix-verify-") as directory:
        root = Path(directory)
        for name, text in files.items():
            target = root / relative(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        argv = ["/usr/bin/bwrap", "--die-with-parent", "--new-session", "--unshare-all", "--clearenv"]
        for name in ("/usr", "/lib", "/lib64"):
            if Path(name).exists():
                argv += ["--ro-bind", name, name]
        argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--ro-bind", str(root), "/work",
                 "--chdir", "/work", "--setenv", "HOME", "/tmp", "--setenv", "PATH", "/usr/bin:/bin",
                 "--", "/usr/bin/python3", "-I", "-B", "-c", source]
        def limits():
            resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
            resource.setrlimit(resource.RLIMIT_FSIZE, (1024**2, 1024**2))
            resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                       start_new_session=True, preexec_fn=limits, env={"PATH": "/usr/bin:/bin"})
            timeout = False
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                timeout = True
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            stderr.seek(0)
            error = stderr.read(65536).decode(errors="replace")
            return {"exit_code": process.returncode, "timeout": timeout, "stderr": error}


def test_scope(source):
    tree=ast.parse(source)
    forbidden={'subprocess','pathlib','inspect','socket','urllib','importlib','requests','http','ctypes'}
    aliases={a.asname or a.name:a.name for n in ast.walk(tree) if isinstance(n,ast.Import) for a in n.names}
    def dotted(node):
        if isinstance(node,ast.Name):return aliases.get(node.id,node.id)
        if isinstance(node,ast.Attribute):return dotted(node.value)+'.'+node.attr
        return ''
    allowed={'os.path','os.path.dirname','os.path.abspath','os.path.join','os.path.normpath',
             'sys.path','sys.path.insert','sys.path.append'}
    for n in ast.walk(tree):
        if isinstance(n,ast.Import) and any(a.name.split('.')[0] in forbidden for a in n.names):return False
        if isinstance(n,ast.ImportFrom):
            module=n.module or ''
            if module.split('.')[0] in forbidden:return False
            if module in ('os','sys') and any(a.name!='path' for a in n.names):return False
        if isinstance(n,ast.Attribute):
            name=dotted(n)
            if name.startswith(('os.','sys.')) and name not in allowed:return False
            if n.attr in ('__code__','__dict__','__globals__','__closure__','__subclasses__'):return False
            if name.startswith('engine.') and n.attr.startswith('__'):return False
        if isinstance(n,ast.Call) and dotted(n.func) in {'open','__import__','eval','exec','compile','time.sleep','getattr','globals','locals'}:return False
    return True


def verify(task, acceptance, output, inputs):
    workflow = task["workflow"]
    if workflow == "file-summary":
        from agent_workflows import decode_json
        actual = decode_json(output)
        return {"facts_and_citations": canonical(actual) == canonical(acceptance)}
    if workflow == "github-actions":
        import yaml
        class Unique(yaml.SafeLoader):
            pass
        def mapping(loader, node, deep=False):
            result = {}
            for key_node, value_node in node.value:
                key = loader.construct_object(key_node, deep=deep)
                if key in result:
                    raise ValueError("duplicate YAML key")
                result[key] = loader.construct_object(value_node, deep=deep)
            return result
        Unique.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
        actual = yaml.load(output, Loader=Unique)
        if isinstance(actual, dict) and True in actual:
            if "on" in actual:
                raise ValueError("ambiguous on key")
            actual["on"] = actual.pop(True)
        return {"workflow_contract": canonical(actual) == canonical(acceptance["workflow"])}
    if workflow == "bug-fix":
        source = '''import sys,copy,json
sys.path.insert(0,'/work')
import engine
for vector in json.loads(VECTORS):
    args=copy.deepcopy(vector['args']); original=copy.deepcopy(args)
    actual=engine.apply(*args)
    assert actual==vector['expected']
    assert args==original, 'caller input mutated'
'''.replace("VECTORS", repr(json.dumps(acceptance["vectors"])))
        result = isolated_python({"engine.py": output}, source)
        return {"regressions_and_input_preservation": result["exit_code"] == 0 and not result["timeout"]}
    if not test_scope(output):
        return {"behavioral_test_scope": False}
    # Test runner exit/import errors do not count as killed behavioral mutants.
    source = "import sys,unittest; sys.path.insert(0,'/work'); r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover('/work/tests')); sys.exit(0 if r.wasSuccessful() else 1)"
    checks = {}
    for i, implementation in enumerate([acceptance["reference"], *acceptance["mutants"]]):
        result = isolated_python({"engine.py": implementation, "tests/test_engine.py": output}, source)
        error = result["stderr"]
        ran = bool(re.search(r"Ran [1-9]\d* tests?\b", error))
        behavioral_failure = bool(re.search(r"FAILED \(failures=[1-9]", error)) and "ERROR:" not in error
        checks["baseline" if i == 0 else "mutant-" + str(i)] = bool(
            ran and not result["timeout"] and (result["exit_code"] == 0 if i == 0 else result["exit_code"] == 1 and behavioral_failure))
    return checks
