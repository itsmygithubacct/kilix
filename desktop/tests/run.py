#!/usr/bin/env python3
"""Run every test_*.py in this dir, each in its own subprocess.

Usage: python3 desktop/tests/run.py [name-substring ...]
Prints PASS/FAIL per file (captured output shown on failure); exits nonzero
on any failure. Each subprocess gets a throwaway KILIX_DESKTOP_DIR so no
test can touch the real desktop dir even without the harness.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    names = sorted(f for f in os.listdir(HERE)
                   if f.startswith("test_") and f.endswith(".py"))
    if sys.argv[1:]:
        names = [n for n in names if any(a in n for a in sys.argv[1:])]
    failed = []
    for name in names:
        env = dict(os.environ)
        env["KILIX_DESKTOP_DIR"] = tempfile.mkdtemp(prefix="kilix95-test-")
        env["KILIX_STORAGE_HOME"] = tempfile.mkdtemp(prefix="kilix-storage-")
        t0 = time.time()
        try:
            p = subprocess.run([sys.executable, os.path.join(HERE, name)],
                               capture_output=True, text=True, env=env,
                               timeout=30)
            ok, out = p.returncode == 0, p.stdout + p.stderr
        except subprocess.TimeoutExpired as e:
            ok = False
            out = ((e.stdout or b"").decode("utf-8", "replace")
                   + (e.stderr or b"").decode("utf-8", "replace")
                   + "\n[timeout after 30s]")
        finally:
            shutil.rmtree(env["KILIX_DESKTOP_DIR"], ignore_errors=True)
            shutil.rmtree(env["KILIX_STORAGE_HOME"], ignore_errors=True)
        print(f"{'PASS' if ok else 'FAIL'}  {name}  ({time.time() - t0:.1f}s)")
        if not ok:
            failed.append(name)
            if out.strip():
                print("  " + out.strip().replace("\n", "\n  "))
    print(f"{len(names) - len(failed)}/{len(names)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
