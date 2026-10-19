"""Launch catalog applications through their existing public entrypoints."""
import os
from pathlib import Path
import runpy
import re
import subprocess
import sys


def dashboard(source, args):
    script = source / 'tmux_browse.py'
    sys.path.insert(0, str(source))
    sys.argv = [str(script), *args]
    upstream = runpy.run_path(str(script), run_name='_catalog_dashboard')
    if args[0] in ('serve', 'start'):
        ttyd = upstream['ttyd']
        binary = ttyd.config.ttyd_executable()
        version = subprocess.run([binary, '--version'], capture_output=True, text=True, timeout=5)
        # Ubuntu 22.04's ttyd 1.6 is writable by default. 1.7 introduced -W
        # and changed that default. Preserve the upstream requested policy
        # on either version without replacing the system binary.
        if re.search(r'\b1\.6\.\d+\b', version.stdout + version.stderr):
            original = ttyd._spawn_ttyd
            def spawn(name, port, argv_tail, *positional, **keywords):
                compatible = argv_tail[1:] if argv_tail[:1] == ['-W'] else argv_tail
                return original(name, port, compatible,
                                *positional, **keywords)
            ttyd._spawn_ttyd = spawn
    return upstream['main'](args)


def main(name, source, args):
    source = Path(source).resolve()
    args = list(args)
    os.environ.setdefault('KITTYFB_TRANSPORT', 'inline')
    if name == 'python-sound-generator':
        sys.path.insert(0, str(source))
        sys.argv = ['soundgen', *args]
        runpy.run_module('python_sound_generator', run_name='__main__')
    elif name == 'kilix-amp':
        binary = source / 'kilix-amp'
        if args[:1] == ['--desktop']:
            if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
                raise ValueError('The SDL editor needs WSLg or X11. Launch without --desktop for the terminal player.')
            return subprocess.call([str(binary), *args[1:]])
        if args[:1] == ['--backend']:
            return subprocess.call([str(binary), '--headless', *args[1:]])
        # The upstream Music UI supports an explicit backend. This prevents
        # its Linux-desktop automatic installer from selecting a second copy.
        os.environ['KILIX_AMP'] = str(binary)
        script = source / 'third_party/kilix-tui-utils/tools/music/main.py'
        sys.argv = [str(script), *args]
        runpy.run_path(str(script), run_name='__main__')
    elif name == 'tmux-browse':
        args = args or ['serve']
        if args[0] in ('serve', 'start') and not any(
                value == '--bind' or value.startswith('--bind=') for value in args):
            args = [args[0], '--bind', '127.0.0.1', *args[1:]]
        if args[0] == 'serve' and '--no-federation' not in args:
            args += ['--no-federation']
        return dashboard(source, args)
    else:
        from catalog_store import find
        spec = find(name, 'apps')
        return subprocess.call([str(source / spec['executable']), *args])
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main(sys.argv[1], sys.argv[2], sys.argv[3:]))
    except (OSError, ValueError) as error:
        print('kilix: ' + str(error), file=sys.stderr)
        raise SystemExit(2)
