"""Interpreter argv shared by packaged Windows Python and WSL installations."""
from pathlib import Path
import sys


def python_argv(script, arguments=(), python=None):
    executable = str(python or sys.executable)
    if Path(executable).name.lower() in ('kitty.exe', 'kitty-console.exe'):
        # The native host embeds CPython; its launcher accepts +runpy code,
        # not Python's ordinary `python script.py` convention.
        return [executable, '+runpy',
                'import runpy,sys;sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name="__main__")',
                str(script), *arguments]
    return [executable, str(script), *arguments]


def interpreter(installation):
    managed = installation / 'bin/python3'
    return str(managed.resolve()) if managed.is_file() else sys.executable
