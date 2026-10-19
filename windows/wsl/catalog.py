#!/usr/bin/env python3
"""Public application/game catalog for WSL and native Windows."""
import argparse
import json
import os
import subprocess
import sys
import tarfile

from catalog_store import Catalog, entries, find


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main(args=None, kind='apps', root=None, install=None, runtime=None, execute=None):
    args = list(sys.argv[1:] if args is None else args)
    # Module CLI and host adapters share the same entrypoint.
    while args[:1] in (['--runtime'], ['--root'], ['--installation']):
        if len(args) < 2:
            raise ValueError(args[0] + ' needs a value')
        flag, value, args = args[0], args[1], args[2:]
        if flag == '--runtime':
            if value not in ('native', 'wsl'):
                raise ValueError('--runtime needs native or wsl')
            runtime = value
        elif flag == '--root':
            root = value
        else:
            install = value
    if args[:1] in (['apps'], ['games']):
        kind, args = args[0], args[1:]
    catalog = Catalog(root=root, install=install, runtime=runtime)
    if args[:1] == ['help']:
        args = ['--help', *args[1:]]
    # Retain the original `apps --json` spelling.
    if not args or args[:1] == ['--json']:
        args = ['list', *args]
    parser = Parser(prog='kilix ' + kind,
                    description='Install, manage and launch public ' + kind + '. No implicit model downloads.')
    actions = parser.add_subparsers(dest='action', required=True)
    listing = actions.add_parser('list', help='List availability; games default to installed/enabled names')
    listing.add_argument('--json', action='store_true')
    listing.add_argument('--all', action='store_true', help='Include every entry and readiness details')
    info = actions.add_parser('info', help='Show runtime, source, prerequisites and readiness')
    info.add_argument('name')
    for action in ('install', 'update'):
        command = actions.add_parser(action, help='Build the reviewed pin or import a verified binary bundle')
        command.add_argument('name')
        archive = command.add_mutually_exclusive_group()
        archive.add_argument('--binary', help='Binary bundle path or HTTPS URL')
        archive.add_argument('--source-bundle', help='Pinned companion source bundle path or HTTPS URL')
        command.add_argument('--sha256', help='Archive digest verified with its publisher')
    for action in ('enable', 'disable', 'check'):
        command = actions.add_parser(action)
        command.add_argument('name')
    for action in ('launch', 'run', 'play'):
        command = actions.add_parser(action, help='Run an enabled, installed item in this terminal')
        command.add_argument('name')
        command.add_argument('arguments', nargs=argparse.REMAINDER)
    choose = actions.add_parser('choose', help='Interactive catalog with install/update and launch actions')
    choose.add_argument('--gui', action='store_true', help='Use the Tk graphical chooser (requires tkinter and a display)')
    export = actions.add_parser('export', help='Export a built item, source and notices for the same platform')
    export.add_argument('name')
    export.add_argument('output')
    try:
        options = parser.parse_args(args)
    except SystemExit as error:
        if error.code == 0:
            return 0
        raise
    if options.action in ('install', 'update', 'export', 'choose') and sys.version_info < (3, 12):
        if catalog.python == sys.executable:
            raise ValueError('Catalog installation needs Python 3.12+; run companion setup first')
        return subprocess.call([catalog.python, __file__, '--runtime', catalog.runtime,
                                '--root', str(catalog.root), '--installation', str(catalog.install), kind, *args])
    if options.action == 'list':
        rows = [catalog.status(spec) for spec in entries(kind)]
        if kind == 'games' and not options.all:
            result = sorted(row['name'] for row in rows if row['ready'])
            print(json.dumps(result) if options.json else '\n'.join(result))
        elif options.json:
            print(json.dumps(rows, indent=2))
        else:
            for row in rows:
                print(f"{row['name']:<26} {row['runtime']:<7} {row['status']:<22} {row['reason']}")
        return 0
    if options.action == 'choose':
        from catalog_chooser import graphical, terminal
        return (graphical if options.gui else terminal)(catalog, kind, execute=execute)
    spec = find(options.name, kind)
    if options.action == 'info':
        print(json.dumps(catalog.status(spec), indent=2))
    elif options.action in ('install', 'update'):
        catalog.install_item(spec, binary=options.binary, sha256=options.sha256, source_bundle=options.source_bundle)
        print(spec['name'] + ' installed; previous versions and enable/disable choice retained.')
    elif options.action in ('enable', 'disable'):
        catalog.set_enabled(spec, options.action == 'enable')
        print(spec['name'] + ' ' + options.action + 'd')
    elif options.action == 'check':
        argv = catalog.argv(spec)
        if spec['recipe'] == 'companion':
            subprocess.run(argv + ['--help'], check=True, stdout=subprocess.DEVNULL, timeout=30)
        elif spec['recipe'] != 'builtin':
            from catalog_build import smoke
            smoke(spec, catalog.generation(spec), python=catalog.python)
        print(spec['name'] + ': ready')
    elif options.action == 'export':
        catalog.export(spec, options.output)
    else:
        argv = catalog.argv(spec) + options.arguments
        if execute:
            return execute(argv)
        return subprocess.call(argv, env=dict(os.environ, KITTYFB_TRANSPORT='inline'))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, tarfile.TarError, subprocess.SubprocessError) as error:
        print('kilix catalog: ' + str(error), file=sys.stderr)
        raise SystemExit(2)
