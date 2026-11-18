"""Update the installed coding client using its existing installation method."""
import json
import os
from pathlib import Path
import shutil
import subprocess


def codex_npm_package(command):
    """Recognize the active global npm package, including a symlinked launcher."""
    target = Path(command).resolve()
    for directory in target.parents:
        if directory.name != 'codex' or directory.parent.name != '@openai':
            continue
        modules = directory.parent.parent
        if modules.name != 'node_modules' or modules.parent.name != 'lib':
            continue
        try:
            manifest = json.loads((directory/'package.json').read_text())
        except (OSError, ValueError):
            continue
        if manifest.get('name') == '@openai/codex':
            return directory, modules.parent.parent
    return None


def update(agent, command):
    package = codex_npm_package(command) if agent['id'] == 'codex' else None
    if package:
        directory, prefix = package
        npm = shutil.which('npm')
        if not npm:
            print('npm is required to update this npm installation.')
            return 1
        elevated = os.geteuid() != 0 and not os.access(directory.parent.parent, os.W_OK)
        elevation = ['sudo', '--'] if elevated else []
        # Constant shell program; all paths and package names are separate argv.
        argv = [*elevation, 'sh', '-c', 'umask 022; exec "$@"', 'kilix-agent-update',
                npm, 'install', '--global', '--prefix', str(prefix), '@openai/codex@latest']
        print('Updating Codex in its existing npm prefix: '+str(prefix), flush=True)
        result = subprocess.run(argv, check=False).returncode
        if result:
            return result
        # Match the standalone installer: a restrictive caller umask must not
        # leave the CLI readable only by the account that installed it.
        result = subprocess.run([*elevation, 'chmod', '-R', 'go+rX', '--', str(directory)],
                                check=False).returncode
    else:
        argv = [command, *agent['update'][1:]]
        print('Updating '+agent['label']+': '+' '.join(argv), flush=True)
        result = subprocess.run(argv, check=False).returncode
    if result:
        return result
    return subprocess.run([command, '--version'], check=False).returncode
