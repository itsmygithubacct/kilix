"""Per-user, atomic catalog state; importing and listing never install anything."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import tarfile
import tempfile

from catalog_sources import HERE, digest, download, materialize, revisions
import catalog_build
from catalog_runtime import interpreter, python_argv


def default_root(runtime=None):
    if (runtime or ('native' if os.name == 'nt' else 'wsl')) == 'native':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'Kilix/catalog'
    return Path.home() / '.local/share/kilix-wsl'


def read_state(root):
    path = root / 'catalog-state.json'
    if not path.exists():
        return {'schema': 1, 'disabled': [], 'installed': {}}
    data = json.loads(path.read_text())
    if (not isinstance(data, dict) or data.get('schema') != 1 or not isinstance(data.get('disabled'), list)
            or not isinstance(data.get('installed'), dict)):
        raise ValueError('Invalid catalog state; retained unchanged: ' + str(path))
    return data


def write_state(root, data):
    fd, name = tempfile.mkstemp(prefix='.catalog-', dir=root)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, root / 'catalog-state.json')
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def locked(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.catalog.lock').open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0)
            if not stream.read(1):
                stream.write(b'\0')
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def entries(kind=None):
    data = []
    for category in ('app', 'game'):
        if kind and kind != category + 's':
            continue
        data += json.loads((HERE / (category + '-catalog.json')).read_text())
    return data


def find(name, kind=None):
    for spec in entries(kind):
        if name in [spec['name'], *spec.get('aliases', [])]:
            return spec
    raise ValueError('Unknown catalog name: ' + name)


def prefix(spec):
    return spec['source'].split('/')[0] if spec['recipe'] == 'game' else spec['source']


class Catalog:
    def __init__(self, root=None, install=None, runtime=None):
        self.runtime = runtime or ('native' if os.name == 'nt' else 'wsl')
        self.root = (Path(root) if root is not None else default_root(self.runtime)).expanduser().resolve()
        self.install = Path(install).expanduser().resolve() if install is not None else (
            (self.root / 'current').resolve() if (self.root / 'current').is_symlink() else self.root)
        self.python = interpreter(self.install)

    def enabled(self, spec):
        value = spec['name'] not in read_state(self.root)['disabled']
        if self.runtime == 'wsl' and spec['category'] == 'game':
            import catalog_settings
            return catalog_settings.enabled(spec, value)
        return value

    def generation(self, spec):
        record = read_state(self.root)['installed'].get(spec['name'])
        if record:
            if not isinstance(record, dict) or not isinstance(record.get('generation'), str):
                raise ValueError('Invalid catalog installation record')
            relative = Path(record['generation'])
            if relative.is_absolute() or '..' in relative.parts or relative.parts[:1] != ('catalog',):
                raise ValueError('Invalid catalog generation path')
            return self.root / relative
        if spec.get('recipe') == 'game' and (self.install / 'bin' / spec['name']).is_file():
            return self.install
        if spec.get('source') and (self.install / spec['source'] / spec.get('executable', '')).is_file():
            return self.install
        return None

    def status(self, spec):
        result = dict(spec, enabled=self.enabled(spec), installed=False, ready=False, runtime=self.runtime,
                      supported=self.runtime in spec['runtime'])
        if not result['supported']:
            result.update(status='unsupported', reason=spec.get('unsupported') or spec.get('native_reason') or 'Unavailable on this runtime')
            return result
        if spec['recipe'] == 'companion':
            present = (self.install / 'needle/needle_cli.py').is_file()
            result.update(installed=present, ready=present and result['enabled'],
                          status='ready' if present and result['enabled'] else ('disabled' if present else 'not-installed'),
                          reason=spec['description'] if result['enabled'] else 'Installed; enable to launch')
            return result
        if spec['recipe'] == 'builtin' and os.name != 'nt':
            result.update(status='unsupported', supported=False, reason='Requires native Windows')
            return result
        generation = self.generation(spec)
        present = spec['recipe'] == 'builtin'
        if generation:
            path = generation / ('bin/' + spec['name'] if spec['recipe'] == 'game'
                                 else spec['source'] + '/' + spec['executable'])
            present = path.is_file() and (spec['recipe'] in ('python', 'voice') or os.access(path, os.X_OK))
        result['installed'] = present
        missing = [exe for exe in spec.get('requirements', []) if not shutil.which(exe)]
        if not present:
            result.update(status='not-installed', reason='Run kilix ' + spec['category'] + 's install ' + spec['name'])
            missing_build = catalog_build.missing_build_dependencies(spec)
            result['source_installable'] = not missing_build
            result['missing_build_dependencies'] = missing_build
            if missing_build:
                result['reason'] += '; source build needs: ' + ', '.join(missing_build)
                if spec.get('build_note'):
                    result['reason'] += '. ' + spec['build_note']
        elif not result['enabled']:
            result.update(status='disabled', reason='Installed; enable to launch')
        elif missing:
            result.update(status='missing-dependencies', reason='Install runtime commands: ' + ', '.join(missing))
        else:
            result.update(status='ready', ready=True, reason=spec.get('description', 'Installed and enabled'))
        if generation and (generation / 'catalog-install.json').is_file():
            receipt = json.loads((generation / 'catalog-install.json').read_text())
            if not isinstance(receipt, dict) or not isinstance(receipt.get('revisions'), dict):
                raise ValueError('Invalid installed catalog manifest: ' + spec['name'])
            result['installed_revisions'] = receipt['revisions']
            result['update_available'] = (receipt['revisions'] != revisions([prefix(spec)]) or
                                          receipt.get('dependencies', {}) != catalog_build.dependency_pins(spec))
        return result

    def set_enabled(self, spec, enabled):
        with locked(self.root):
            state = read_state(self.root)
            disabled = set(state['disabled'])
            shared = False
            if self.runtime == 'wsl' and spec['category'] == 'game':
                import catalog_settings
                shared = catalog_settings.set_enabled(spec, enabled)
            disabled.discard(spec['name']) if shared or enabled else disabled.add(spec['name'])
            state['disabled'] = sorted(disabled)
            write_state(self.root, state)

    def install_item(self, spec, binary=None, sha256=None, source_bundle=None):
        if sys.version_info < (3, 12):
            raise ValueError('Catalog installation needs the managed Python 3.12+ interpreter')
        if self.runtime not in spec['runtime']:
            raise ValueError(self.status(spec)['reason'])
        if spec['recipe'] == 'builtin':
            raise ValueError('This uses installed Windows capabilities; update Windows or its registered application')
        if spec['recipe'] == 'companion':
            raise ValueError('Use companion setup to install/update Needle; existing models and receipts are retained')
        if (binary or source_bundle) and not sha256:
            raise ValueError('An archive requires --sha256 from a trusted publisher')
        if sha256 and not (binary or source_bundle):
            raise ValueError('--sha256 requires --binary or --source-bundle')
        if not binary:
            catalog_build.require_build_dependencies(spec)
        with locked(self.root):
            store = self.root / 'catalog'
            store.mkdir(exist_ok=True)
            generation = Path(tempfile.mkdtemp(prefix=spec['name'] + '-', dir=store))
            cache = self.root / 'cache'
            cache.mkdir(exist_ok=True)
            activated = False
            try:
                pins = revisions([prefix(spec)])
                archive = binary or source_bundle
                if archive:
                    if str(archive).startswith('https://'):
                        path = cache / (sha256.lower() + '.tar.gz')
                        download(str(archive), path, sha256)
                    else:
                        path = Path(archive)
                    if digest(path) != sha256.lower():
                        raise ValueError('Archive SHA256 mismatch')
                    with tarfile.open(path) as bundle:
                        if binary:
                            bundle.extractall(generation, filter='data')
                        else:
                            # Never import models, receipts, or unrelated apps.
                            selected = [m for m in bundle.getmembers()
                                        if m.name == prefix(spec) or m.name.startswith(prefix(spec) + '/')]
                            bundle.extractall(generation, members=selected, filter='data')
                    if binary:
                        record = json.loads((generation / 'catalog-install.json').read_text())
                        if (not isinstance(record, dict) or record.get('schema') != 1 or record.get('name') != spec['name']
                                or record.get('runtime') != self.runtime
                                or record.get('architecture') != platform.machine().lower()
                                or record.get('revisions') != pins
                                or record.get('dependencies', {}) != catalog_build.dependency_pins(spec)):
                            raise ValueError('Binary bundle does not match this catalog, platform and source revision')
                    else:
                        with tarfile.open(path) as bundle:
                            try:
                                member = bundle.getmember('source-revisions.json')
                            except KeyError as error:
                                raise ValueError('Source bundle has no revision manifest') from error
                            if not member.isfile():
                                raise ValueError('Source revision manifest must be a regular file')
                            record = json.load(bundle.extractfile(member))
                        if not isinstance(record, dict) or any(record.get(key) != value for key, value in pins.items()):
                            raise ValueError('Source bundle does not match reviewed revisions')
                else:
                    materialize([prefix(spec)], generation, cache)
                if not binary:
                    catalog_build.build(spec, generation, cache, python=self.python)
                elif spec['recipe'] == 'game':
                    # Regenerate relocatable launchers, never rebuild imported binaries.
                    from setup import write_game_launchers
                    write_game_launchers(generation, self.python, [spec])
                catalog_build.smoke(spec, generation, python=self.python)
                catalog_build.manifest(spec, generation, self.runtime, pins)
                state = read_state(self.root)
                state['installed'][spec['name']] = {'generation': generation.relative_to(self.root).as_posix()}
                write_state(self.root, state)
                activated = True
            finally:
                if not activated:
                    shutil.rmtree(generation)
        return generation

    def argv(self, spec):
        status = self.status(spec)
        if not status['ready']:
            raise ValueError(spec['name'] + ': ' + status['reason'])
        if spec['recipe'] == 'builtin':
            return python_argv(HERE / 'catalog_native.py', [spec['name']], python=self.python)
        if spec['recipe'] == 'companion':
            return python_argv(HERE / 'needle-run.py', [str(self.install / 'needle/needle_cli.py')], python=self.python)
        return catalog_build.command(spec, self.generation(spec), python=self.python)

    def export(self, spec, output):
        generation = self.generation(spec)
        if generation is None or not (generation / 'catalog-install.json').is_file():
            raise ValueError('Install this item with the catalog before exporting its binary bundle')
        output = Path(output).resolve()
        if output.is_relative_to(generation.resolve()):
            raise ValueError('Export outside the installed generation')
        output.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.export-', dir=output.parent)
        os.close(fd)
        try:
            with tarfile.open(temporary, 'w:gz') as bundle:
                def public_metadata(member):
                    member.uid = member.gid = 0
                    member.uname = member.gname = ''
                    member.pax_headers = {}
                    return member
                for child in sorted(generation.iterdir()):
                    # bin/python3 belongs to the receiving machine.
                    if child.name == 'bin':
                        continue
                    bundle.add(child, arcname=child.name, filter=public_metadata)
            os.replace(temporary, output)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        print(digest(output), output)
