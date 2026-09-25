"""Compile immutable source and reuse only a complete recorded input population."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import sys
import tempfile
import time

from multiplexer_build_io import BuildIO, check_source, read_file, source_files
from multiplexer_build_guards import (BuildBoundaryChanged, DirectoryEvents, DirectoryHistory, NamedLock,
                                     search_identity, search_roots)
from multiplexer_search import SearchAdmission

FLAGS = ('CFLAGS', 'CPPFLAGS', 'LDFLAGS', 'LDLIBS')
SCHEMA = 'kilix.multiplexer.build/v4'


def linker_flags(token):
    """Only linker options without unreported input files are supported."""
    options = token[4:].split(',')
    simple = {'-t', '--trace', '--as-needed', '--no-as-needed', '--gc-sections',
              '--no-gc-sections', '--no-undefined', '--enable-new-dtags',
              '--disable-new-dtags', '-s', '--strip-all'}
    valued = {'-rpath-link', '-rpath', '-z', '--hash-style', '-soname', '--soname', '--wrap'}
    index = 0
    while index < len(options):
        option = options[index]
        if option in simple or option.startswith(('--build-id', '-O')):
            index += 1
        elif option in valued and index + 1 < len(options) and options[index + 1]:
            index += 2
        else:
            raise ValueError('unsupported linker override: ' + option)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def environment(home):
    """Unlisted compiler/loader/make variables never reach a build subprocess."""
    result = dict(PATH='/usr/bin:/bin', HOME=str(home), TMPDIR=str(home / 'tmp'),
                  LANG='C', LC_ALL='C', SOURCE_DATE_EPOCH='0', PYTHONDONTWRITEBYTECODE='1',
                  GIT_NO_REPLACE_OBJECTS='1', GIT_NO_LAZY_FETCH='1',
                  GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
                  GIT_CONFIG_SYSTEM='/dev/null', GIT_TERMINAL_PROMPT='0')
    for key in (*FLAGS, 'PKG_CONFIG_PATH', 'PKG_CONFIG_LIBDIR'):
        if key not in os.environ:
            continue
        value = os.environ[key]
        if len(value) > 8192 or any(ch in value for ch in '\n\r\0$`'):
            raise ValueError('unsupported build flag syntax: ' + key)
        if key in FLAGS:
            for token in shlex.split(value):
                if token.startswith('-Wl,'):
                    linker_flags(token)
                if (not token.startswith('-') and not token.startswith('/')) or token.startswith(
                        ('@', '-B', '--sysroot', '-isysroot', '-specs', '--specs', '-fplugin',
                         '-wrapper', '-M', '-o', '-X', '-T', '-Wp,', '-Wa,', '-flto',
                         '-fprofile', '-fauto-profile', '-fmodules', '-include-pch', '-x')):
                    raise ValueError('unsupported compiler override: ' + token)
        result[key] = value
    return result


def file_identity(path, io):
    """Bind bytes, resolved name and every ancestor identity; no content is copied."""
    requested = Path(path)
    resolved = requested.resolve(strict=True)
    value = read_file(resolved, 128 * 1024**2, io.check)
    info = resolved.stat()
    entry = requested.lstat()
    if requested.resolve(strict=True) != resolved:
        raise ValueError('compiler dependency link changed during inspection')
    parents = []
    for parent in (requested.parent, *requested.parent.parents):
        found = parent.lstat()
        parents.append([str(parent), found.st_dev, found.st_ino, found.st_ctime_ns,
                        os.readlink(parent) if stat.S_ISLNK(found.st_mode) else None])
    return dict(path=str(requested), resolved=str(resolved), bytes=len(value), sha256=sha(value),
                device=info.st_dev, inode=info.st_ino, mtime_ns=info.st_mtime_ns,
                ctime_ns=info.st_ctime_ns, entry_ctime_ns=entry.st_ctime_ns,
                entry_inode=entry.st_ino, parents=parents)


def tool_plan(io, env):
    selected = os.environ.get('CC', '/usr/bin/cc')
    if len(shlex.split(selected)) != 1:
        raise ValueError('CC must select one provisioned system compiler')
    name = shutil.which(selected, path=env['PATH'])
    if name is None:
        raise ValueError('C compiler is absent')
    compiler = Path(name).resolve(strict=True)
    if not compiler.is_relative_to('/usr/bin') or compiler.stat().st_uid != 0:
        raise ValueError('CC must select a provisioned compiler under /usr/bin')
    def text(argv):
        return io.run(argv, env)[1].decode().strip()
    # The supported toolchain is GCC, whose dependency/trace formats are checked below.
    version = text([str(compiler), '--version'])
    if 'Free Software Foundation' not in version:
        raise ValueError('this build identity requires the provisioned GCC toolchain')
    tools = {str(compiler), '/usr/bin/ar', '/usr/bin/make', '/usr/bin/pkg-config', '/usr/bin/readelf', '/usr/bin/ldd',
             str(Path('/usr/bin/python3').resolve(strict=True))}
    for role in ('cc1', 'as', 'ld'):
        candidate = text([str(compiler), '-print-prog-name=' + role])
        found = candidate if candidate.startswith('/') else shutil.which(candidate, path=env['PATH'])
        if found is None:
            raise ValueError('provisioned compiler component is absent: ' + role)
        tools.add(str(Path(found).resolve(strict=True)))
    specs = text([str(compiler), '-print-file-name=specs'])
    if specs != 'specs':
        tools.add(str(Path(specs).resolve(strict=True)))
    runtime = set()
    for path in sorted(tools):
        # ldd is itself a trusted system script; executable tool dependencies
        # matter even when the compiler driver bytes remain unchanged.
        if read_file(Path(path).resolve(strict=True), 128 * 1024**2, io.check)[:4] != b'\x7fELF':
            continue
        listing = text(['/usr/bin/ldd', path])
        if 'not found' in listing:
            raise ValueError('provisioned compiler runtime dependency is absent')
        for line in listing.splitlines():
            name = line.split('=>', 1)[-1].strip().split(' ', 1)[0]
            if name.startswith('/'):
                runtime.add(str(Path(name).resolve(strict=True)))
    tools.update(runtime)
    return compiler, dict(version=version, files={path: file_identity(path, io) for path in sorted(tools)})


def artifact_files(root, io):
    files = {}
    total = 0
    for path in sorted(root.rglob('*')):
        io.check()
        if path.is_symlink():
            raise ValueError('build output may not contain links')
        if path.is_file():
            value = read_file(path, 16 * 1024**2, io.check)
            total += len(value)
            if total > 32 * 1024**2 or len(files) >= 1024:
                raise ValueError('build output population exceeds bound')
            files[str(path.relative_to(root))] = dict(bytes=len(value), sha256=sha(value))
    return files


def dependency_names(output, source, trace, io):
    """GCC -MD reports system headers; the linker trace reports actual link inputs."""
    names = set()
    depfiles = list(output.rglob('*.d'))
    if not depfiles:
        raise ValueError('compiler did not report its consumed headers')
    for object_file in output.rglob('*.o'):
        if not object_file.with_suffix('.d').is_file():
            raise ValueError('compiler omitted an object dependency record')
    for path in depfiles:
        raw = read_file(path, 1024**2, io.check).decode().replace('\\\n', ' ')
        _target, colon, rest = raw.partition(':')
        if not colon:
            raise ValueError('invalid compiler dependency record')
        # GCC escapes spaces with backslashes; shlex handles that without shell execution.
        for value in shlex.split(rest):
            item = Path(value)
            names.add(item if item.is_absolute() else source / item)
    linked = 0
    for line in trace.decode().splitlines():
        value = line.strip()
        if value.startswith('/') and Path(value).is_file():
            names.add(Path(value))
            linked += 1
    if not linked:
        raise ValueError('linker did not report its consumed inputs')
    external = set()
    generation = source.parent
    for path in names:
        path = Path(os.path.abspath(path))
        if Path(str(path) + '.gch').exists() or Path(str(path) + '.pch').exists():
            raise ValueError('precompiled headers are outside the supported dependency record')
        if not path.is_relative_to(generation):
            external.add(path)
    if not external or len(external) > 4096:
        raise ValueError('external dependency population is unavailable or exceeds bound')
    return sorted(external)


def source_snapshot(root, files):
    root.mkdir(mode=0o700)
    for name, (mode, value) in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        path.chmod(0o500 if mode & 0o100 else 0o400)
        os.utime(path, ns=(0, 0))
    for directory in sorted((p for p in root.rglob('*') if p.is_dir()), reverse=True):
        directory.chmod(0o500)
    root.chmod(0o500)


def remove_generation(path):
    if path is not None and path.is_dir() and not path.is_symlink():
        for directory, _children, _files in os.walk(path):
            os.chmod(directory, 0o700)
        shutil.rmtree(path)


def publish(generation, base, directory, record, io):
    """Restore a live boundary refusal while retaining the directory flock.

    This is not a crash-atomic three-file transaction. An I/O failure or process
    crash can still interrupt replacement; reuse independently checks the pair
    against its stamp. A detected name/history loss, cancellation or deadline
    restores only this transaction's replaced entries, never a foreign inode.
    """
    names = ('kmx-serve', 'kmx-attach', 'build-identity')
    backups = {}
    for name in names:
        destination = base / name
        backup = generation / ('previous-' + name)
        if os.path.lexists(destination):
            value = read_file(destination, check=io.check)
            mode = destination.stat().st_mode & 0o777
            backup.write_bytes(value)
            backup.chmod(mode)
            backups[name] = backup
        else:
            backups[name] = None
        temp = generation / name
        temp.write_bytes(canonical(record) if name == 'build-identity' else
                         read_file(generation / 'out' / name, check=io.check))
        temp.chmod(0o600 if name == 'build-identity' else 0o700)
    replaced = []
    try:
        for name in names:
            io.check()
            temp = generation / name
            info = temp.stat()
            os.replace(temp, name, dst_dir_fd=directory)
            replaced.append((name, (info.st_dev, info.st_ino)))
        io.check()
    except (BuildBoundaryChanged, InterruptedError, TimeoutError):
        # No other conforming publisher can acquire this directory's flock,
        # including after .build.lock replacement. Cleanup does not call the
        # now-failed boundary/deadline check and never releases either guard.
        for name, expected in reversed(replaced):
            visible = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if (visible.st_dev, visible.st_ino) != expected:
                raise BuildBoundaryChanged('publication entry changed; refusing to overwrite foreign output')
            if backups[name] is None:
                os.unlink(name, dir_fd=directory)
            else:
                os.replace(backups[name], name, dst_dir_fd=directory)
        raise
    finally:
        for backup in backups.values():
            if backup is not None:
                backup.unlink(missing_ok=True)


def compiler_logs(root, *, read=False):
    """Bound the independent stderr population during execution and inspection."""
    files = sorted(root.iterdir())
    if len(files) > 512:
        raise ValueError('compiler diagnostic population exceeds bound')
    total = 0
    blocks = []
    for path in files:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('unsafe compiler diagnostic file')
        total += info.st_size
        if total > 4 * 1024**2:
            raise ValueError('compiler diagnostics exceed bound')
        if read:
            blocks.append(read_file(path, 4 * 1024**2))
    return b'\n'.join(blocks)


def build(args, package_plan):
    io = BuildIO(args.timeout)
    io.supervise()
    base = Path(args.build_dir)
    if not base.is_absolute() or base != base.resolve(strict=True):
        raise ValueError('build directory must be existing and canonical')
    info = base.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('build directory must be private and owned')
    directory = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    lock = None
    owner = None
    history = None
    driver_history = None
    generation = None
    generation_identity = None
    generation_fd = None
    stage_events = None
    search_admission = None
    search_endpoint = '-'
    published = False
    try:
        lock = os.open('.build.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                       0o600, dir_fd=directory)
        info = os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or info.st_mode & 0o077:
            raise ValueError('unsafe build lock')
        owner = NamedLock(base, directory, lock)
        io.boundary_check = owner.check
        io.guard = (directory, lock)
        for guard in io.guard:
            while True:
                io.check()
                try:
                    fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(.025)
        io.check()
        generation = Path(tempfile.mkdtemp(prefix='generation-', dir=base))
        found = generation.stat()
        generation_identity = (found.st_dev, found.st_ino)
        generation_fd = os.open(generation, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        held = os.fstat(generation_fd)
        if (held.st_dev, held.st_ino) != generation_identity:
            raise BuildBoundaryChanged('private build generation changed during allocation')
        stage_events = DirectoryEvents(os.fsencode(generation.name))
        stage_events.watch(base)
        (generation / 'tmp').mkdir()
        diagnostics = generation / 'tmp/compiler'
        diagnostics.mkdir(mode=0o700)
        discovery_logs = generation / 'tmp/search'
        discovery_logs.mkdir(mode=0o700)
        def compiler_boundary():
            owner.check()
            try:
                stage_events.check()
                current = generation.lstat()
                if (current.st_dev, current.st_ino) != generation_identity:
                    raise BuildBoundaryChanged('private build generation identity changed')
            except (OSError, BuildBoundaryChanged) as exc:
                raise BuildBoundaryChanged('private build generation changed') from exc
            compiler_logs(diagnostics)
            compiler_logs(discovery_logs)
            if driver_history is not None:
                driver_history.check()
            if search_admission is not None:
                search_admission.check()
        io.boundary_check = compiler_boundary
        env = environment(generation)
        source = Path(args.source).resolve(strict=True)
        tree, files = source_files(source, args.commit, io, env)
        compiler, tools = tool_plan(io, env)
        def package():
            def query(*flags):
                return io.run(['/usr/bin/pkg-config', *flags], env)[1].decode().strip()
            query('--exists', 'samplerate', 'openssl', 'kilix-encodec')
            prefix = query('--define-prefix', '--variable=prefix', 'kilix-encodec')
            cflags = query('--define-prefix', '--cflags', 'kilix-encodec')
            libs = query('--define-prefix', '--libs', 'kilix-encodec')
            return package_plan(prefix, cflags, libs)
        native = package()
        diagnostic_helper = Path(__file__).with_name('multiplexer_compiler_exec.py').resolve(strict=True)
        helper_bytes = read_file(diagnostic_helper, check=io.check)
        driver = generation / 'compiler'
        driver.mkdir(mode=0o700)
        diagnostic_helper = driver / 'exec.py'
        diagnostic_helper.write_bytes(helper_bytes)
        diagnostic_helper.chmod(0o400)
        search_helpers = {}
        for name in ('multiplexer_search.py', 'multiplexer_build_guards.py'):
            raw = read_file(Path(__file__).with_name(name), check=io.check)
            (driver / name).write_bytes(raw)
            (driver / name).chmod(0o400)
            search_helpers[name] = sha(raw)
        driver.chmod(0o500)
        driver_history = DirectoryHistory(driver, io.check)
        inputs = dict(source_commit=args.commit, source_tree=tree, tools=tools, package=native,
                      compiler_diagnostic_helper=sha(helper_bytes),
                      compiler_search_helpers=search_helpers,
                      flags={key: env.get(key) for key in FLAGS}, compiler=str(compiler),
                      pkg_config={key: env.get(key) for key in ('PKG_CONFIG_PATH', 'PKG_CONFIG_LIBDIR')}, ENCODEC=1)
        signature = sha(canonical(inputs))
        stamp = base / 'build-identity'
        old = None
        if os.path.lexists(stamp):
            raw = read_file(stamp, 4 * 1024**2, io.check)
            try:
                old = json.loads(raw)
            except (ValueError, UnicodeError):
                pass  # Legacy identity requires a fresh build, never reuse.
        def make(root):
            return ['/usr/bin/make', '--silent', '--no-print-directory', '-C', str(root / 'source'),
                    'BUILD_DIR=' + str(root / 'out'), 'ENCODEC=1',
                    'CC=' + shlex.join(['/usr/bin/python3', str(diagnostic_helper), str(compiler),
                                       str(diagnostics), str(search_endpoint), '-MD', '-Wp,-v', '-Wl,-t']), 'AR=/usr/bin/ar',
                    'ENCODEC_CFLAGS=' + native['cflags'], 'ENCODEC_LIBS=' + native['libs']]
        def binaries(root):
            for name in ('kmx-serve', 'kmx-attach'):
                path = root / name
                if not path.is_file() or path.is_symlink() or path.stat().st_uid != os.geteuid() or not path.stat().st_mode & 0o100:
                    raise ValueError('native build did not produce safe executables')
                dynamic = io.run(['/usr/bin/readelf', '-d', str(path)], env)[1]
                symbols = io.run(['/usr/bin/readelf', '--dyn-syms', '--wide', str(path)], env)[1]
                if b'Shared library: [libkilix-encodec.so.0]' not in dynamic or b'kenc_installed_assets_open' not in symbols:
                    raise ValueError('selected multiplexer source did not link installed EnCodec admission; update the host-selected source closure')
        reusable = False
        if isinstance(old, dict) and old.get('schema') == SCHEMA and old.get('signature') == signature and old.get('cacheable') is True:
            old_name = old.get('generation', '')
            if re.fullmatch(r'generation-[a-zA-Z0-9_-]+', old_name):
                previous = base / old_name
                try:
                    assert previous.is_dir() and not previous.is_symlink()
                    for name, (_mode, value) in files.items():
                        assert read_file(previous / 'source' / name, check=io.check) == value
                    assert artifact_files(previous / 'out', io) == old['artifacts']
                    assert search_identity(old['search_roots'], io.check) == old['search_directories']
                    assert all(file_identity(path, io) == row for path, row in old['dependencies'].items())
                    assert all(sha(read_file(base / name, check=io.check)) == old['artifacts'][name]['sha256'] for name in ('kmx-serve', 'kmx-attach'))
                    code, _trace = io.run(make(previous) + ['--question', 'all'], env, allowed=(0, 1, 2))
                    reusable = code == 0
                except (AssertionError, KeyError, OSError, ValueError):
                    reusable = False
        if reusable:
            binaries(base)
            check_source(source, args.commit, files, io, env)
            if package() != native or tool_plan(io, env)[1] != tools:
                raise ValueError('build inputs changed during reuse')
            if any(file_identity(path, io) != row for path, row in old['dependencies'].items()):
                raise ValueError('external compiler dependency changed during reuse')
            if search_identity(old['search_roots'], io.check) != old['search_directories']:
                raise ValueError('compiler include search changed during reuse')
            io.check()
            return str(base / ('kmx-' + args.print_kind)) if args.print_kind else ''
        snapshot = generation / 'source'
        source_snapshot(snapshot, files)
        history = DirectoryHistory(snapshot, io.check)
        def check_boundaries():
            compiler_boundary()
            history.check()
        (generation / 'out').mkdir()
        search_endpoint = Path('/proc/self/fd') / str(directory) / generation.name / 'tmp/search.sock'
        # Admission's traversal must check the original budget without
        # recursively entering its own request handler.
        next_search_guard_check = 0
        def search_check():
            nonlocal next_search_guard_check
            if io.stopped:
                raise InterruptedError('multiplexer build interrupted')
            now = time.monotonic()
            if now >= io.deadline:
                raise TimeoutError('multiplexer build deadline exceeded')
            # Every entry checks cancellation/deadline. Drain retained guard
            # events at the same 25ms cadence as command polling, rather than
            # resolving every ancestor for every unrelated system-header name.
            # Full boundary checks and search-history drains still bracket
            # compilation and each publication replacement.
            if now >= next_search_guard_check:
                owner.check()
                driver_history.check()
                next_search_guard_check = now + .025
        search_admission = SearchAdmission(search_endpoint, snapshot, diagnostics, search_check)
        started = time.time_ns()
        # A failed freshness check cannot select a default/disabled recipe.
        io.run(make(generation) + ['--question', 'all'], env, allowed=(0, 1, 2))
        check_boundaries()
        print('kilix: building kilix-multiplexer with shared EnCodec', file=sys.stderr)
        code, trace = io.run(make(generation) + ['-j2', '-B', 'all'], env, allowed=(0, 1, 2))
        trace += b'\n' + compiler_logs(diagnostics, read=True)
        if len(trace) > 4 * 1024**2:
            raise ValueError('combined build command output exceeds its bound')
        if code:
            raise ValueError(f'build command failed (make all, exit {code}): '
                             + trace[-4000:].decode('utf-8', 'replace'))
        search_admission.finish(trace)
        io.boundary_check = check_boundaries
        io.check()
        binaries(generation / 'out')
        deps = {str(path): file_identity(path, io) for path in dependency_names(generation / 'out', snapshot, trace, io)}
        if any(max(row['ctime_ns'], row['entry_ctime_ns']) > started for row in deps.values()):
            raise ValueError('external compiler dependency changed during compilation')
        cacheable = all(parent[3] <= started for row in deps.values() for parent in row['parents'])
        roots = search_roots(trace, snapshot)
        search = search_identity(roots, search_check)
        if any(row is not None and max(row['entry'][-1], row.get('target', [0])[-1]) > started
               for row in search.values()):
            cacheable = False
        check_source(source, args.commit, files, io, env)
        for name, (_mode, value) in files.items():
            path = snapshot / name
            if read_file(path, check=io.check) != value or path.stat().st_ctime_ns > started:
                raise ValueError('private source snapshot changed during compilation')
        if package() != native or tool_plan(io, env)[1] != tools:
            raise ValueError('build inputs changed during compilation')
        for path, row in deps.items():
            current = file_identity(path, io)
            if ({key: value for key, value in current.items() if key != 'parents'} !=
                    {key: value for key, value in row.items() if key != 'parents'}
                    or [[*entry[:3], entry[4]] for entry in current['parents']] !=
                    [[*entry[:3], entry[4]] for entry in row['parents']]):
                raise ValueError('external compiler dependency changed before publication: ' + path)
            # Unrelated sibling creation can update an ancestor's ctime. The
            # fresh output may be returned, but that uncertain history cannot
            # authorize later reuse of this generation.
            if current['parents'] != row['parents']:
                cacheable = False
        artifacts = artifact_files(generation / 'out', io)
        search_admission.finish(trace)
        if search_identity(roots, search_check) != search:
            raise ValueError('compiler include search changed before publication')
        record = dict(schema=SCHEMA, signature=signature, inputs=inputs, generation=generation.name,
                      artifacts=artifacts, dependencies=deps, cacheable=cacheable,
                      search_roots=roots, search_directories=search)
        io.check()
        # Complete generations never consume previously compiled mutable objects.
        publish(Path('/proc/self/fd') / str(generation_fd), base, directory, record, io)
        published = True
        if isinstance(old, dict) and re.fullmatch(r'generation-[a-zA-Z0-9_-]+', old.get('generation', '')):
            remove_generation(base / old['generation'])
        return str(base / ('kmx-' + args.print_kind)) if args.print_kind else ''
    finally:
        io.reap()
        io.boundary_check = lambda: None
        if history is not None:
            history.close()
        if driver_history is not None:
            driver_history.close()
        if owner is not None:
            owner.close()
        if stage_events is not None:
            stage_events.close()
        if search_admission is not None:
            search_admission.close()
        if not published:
            # Use the held directory even after a visible base-name change;
            # never clean a replacement generation belonging to another inode.
            if generation is not None:
                owned = Path('/proc/self/fd') / str(directory) / generation.name
                try:
                    found = owned.lstat()
                except FileNotFoundError:
                    pass
                else:
                    if (found.st_dev, found.st_ino) == generation_identity:
                        remove_generation(owned)
        if lock is not None:
            os.close(lock)
        if generation_fd is not None:
            os.close(generation_fd)
        os.close(directory)
