#!/usr/bin/env python3
"""Stage the shared command source closure in a portable Windows package."""
from pathlib import Path
import shutil
import sys

SOURCE = Path(__file__).resolve().parent


def copy_tree(source, destination):
    shutil.copytree(source, destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('.git', '__pycache__', '*.pyc'))


def stage(destination):
    shared = destination / 'wsl-bin'
    config = shared / 'config'
    config.mkdir(parents=True, exist_ok=True)
    for name in ('remote.py', 'tmux_control.py', 'content_models.py', 'kilix_clock_settings.py',
                 'terminal_video.py', 'terminal_video_errors.lua'):
        shutil.copy2(SOURCE.parent / 'config' / name, config / name)
    for source in (SOURCE.parent / 'config').glob('agent_*.py'):
        shutil.copy2(source, config / source.name)
    for name in ('kilix_sdk', 'kilix_tmux'):
        copy_tree(SOURCE.parent / 'config' / name, config / name)
    shutil.copy2(SOURCE.parent / 'kilix-settings', shared / 'kilix-settings')
    (shared / 'scripts').mkdir(exist_ok=True)
    shutil.copy2(SOURCE.parent / 'scripts/transcript-view.py', shared / 'scripts/transcript-view.py')
    for name in ('kilix-content', 'kilix-transcript-clean'):
        source = SOURCE.parent / 'third_party' / name
        if not (source / 'LICENSE').is_file() or not (source / 'src').is_dir():
            raise ValueError(f'Initialize the pinned third_party/{name} submodule before packaging')
        target = shared / 'third_party' / name
        copy_tree(source / 'src', target / 'src')
        for filename in ('LICENSE', 'NOTICE', 'README.md', 'VERSION'):
            if (source / filename).is_file():
                shutil.copy2(source / filename, target / filename)
        if (source / 'third_party').is_dir():
            copy_tree(source / 'third_party', target / 'third_party')
    (destination / 'wsl').mkdir(exist_ok=True)
    for pattern in ('*.py', '*.json'):
        for source in (SOURCE / 'wsl').glob(pattern):
            shutil.copy2(source, destination / 'wsl' / source.name)
    for name in ('native-commands.py', 'desktop_transport.py', 'desktop.ps1',
                 'desktop-native.cs', 'desktop-dispatch.ps1'):
        shutil.copy2(SOURCE / name, destination / name)


if __name__ == '__main__':
    stage(Path(sys.argv[1]))
