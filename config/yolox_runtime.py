"""Bind a locally prepared cut and wrapper to their exact source inputs.

This is an integrity check for installed bytes, not an inference qualification.
The content authority separately checks the model manifest and agreement.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def digest(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing regular runtime file: {path}")
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def source_revision(source):
    """Name a clean checkout, without claiming a revision for supplied files.

    The installer explicitly supports trusted directories without Git. Their
    tool bytes are still bound below; a null revision makes that weaker source
    provenance visible. A Git checkout must be clean before it can be named by
    its commit, including when KEEP_EXISTING_CHECKOUT is requested.
    """
    if not (source / ".git").exists():
        return None
    def git(*args):
        result = subprocess.run(["git", "--no-optional-locks", "-C", str(source), *args],
                                capture_output=True, text=True, check=False)
        if result.returncode:
            raise ValueError("could not identify runtime source checkout")
        return result.stdout.strip()
    revision = git("rev-parse", "--verify", "HEAD^{commit}")
    if git("status", "--porcelain", "--untracked-files=normal"):
        raise ValueError("runtime source has uncommitted changes; commit them before installation")
    return revision


def snapshot(runtime, source, model, size):
    revision = source_revision(source)
    export = f"{model}.onnx"
    expected = [line.split()[0] for line in (source / "models/SHA256SUMS").read_text().splitlines()
                if len(line.split()) == 2 and line.split()[1] == export]
    actual = digest(runtime / "models" / export)
    if len(expected) != 1 or expected[0] != actual:
        raise ValueError("runtime export differs from the module checksum list")
    files = {
        "export": actual,
        "cut": digest(runtime / "models" / f"{model}_{size}.onnx"),
        "cut_tool": digest(source / "tools/kilix-yolox-cut"),
        "detector_tool": digest(source / "tools/kilix-yolox-detect"),
        "wrapper": digest(runtime / "bin/kilix-yolox-detect"),
        "checksums": digest(source / "models/SHA256SUMS"),
    }
    return {"schema": "kilix.yolox-runtime/v2", "runtime": str(runtime),
            "source": str(source), "source_revision": revision,
            "model": model, "size": size, "sha256": files}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "record"))
    parser.add_argument("runtime", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("model")
    parser.add_argument("size", type=int)
    args = parser.parse_args(argv)
    stamp = args.runtime / "runtime.json"
    try:
        binding = snapshot(args.runtime, args.source, args.model, args.size)
        if args.action == "check":
            if stamp.is_symlink() or json.loads(stamp.read_text()) != binding:
                raise ValueError("runtime binding is absent or differs from installed inputs")
        else:
            fd, temporary = tempfile.mkstemp(prefix=".yolox-binding-", dir=args.runtime)
            try:
                with os.fdopen(fd, "w") as output:
                    os.fchmod(output.fileno(), 0o600)
                    json.dump(binding, output, sort_keys=True)
                    output.write("\n")
                os.replace(temporary, stamp)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return 0
    except (OSError, ValueError) as error:
        parser.exit(1, f"kilix yolox: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
