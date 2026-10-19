"""Check shipped source closure and manifests using small committed fixtures."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

WINDOWS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('export_source', WINDOWS / 'export-source.py')
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), '-c', 'protocol.file.allow=always',
        '-c', 'user.name=itsmygithubacct', '-c', 'user.email=itsmygithubacct@users.noreply.github.com',
        *args], stderr=subprocess.DEVNULL).decode().strip()


def repository(path):
    path.mkdir()
    git(path, 'init', '-q')
    (path / 'LICENSE').write_text('fixture license\n')
    git(path, 'add', '.')
    git(path, 'commit', '-qm', 'Fixture')
    return path


class PackagingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_nested_manifest_files_are_hashed(self):
        package = self.root / 'kilix'
        (package / 'nested').mkdir(parents=True)
        (package / 'nested/manifest.json').write_text('{"third_party": true}')
        subprocess.run(['python3', str(WINDOWS / 'package-manifest.py'), str(package)], check=True)
        files = json.loads((package / 'manifest.json').read_text())['files']
        self.assertEqual(list(files), ['nested/manifest.json'])
        self.assertEqual(files['nested/manifest.json']['sha256'], hashlib.sha256((package / 'nested/manifest.json').read_bytes()).hexdigest())

    def test_manifest_rejects_symlinks(self):
        package = self.root / 'kilix'
        package.mkdir()
        (package / 'link').symlink_to('/nonexistent')
        result = subprocess.run(['python3', str(WINDOWS / 'package-manifest.py'), str(package)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'cannot contain symlinks', result.stderr)

    def bootstrap(self, expected):
        archive = self.root / 'bundle with spaces.tar.gz'
        archive.write_bytes(b'fixture bundle')
        tools = self.root / 'bin'
        tools.mkdir()
        # Only interpreter execution is stubbed. Hash verification and shell argv are real.
        python = tools / 'python3'
        python.write_text('#!/bin/sh\nif [ "$1" = -c ]; then exit 0; fi\nprintf "%s\\n" "$@" > "$HOME/args"\n')
        python.chmod(0o755)
        (tools / 'id').write_text('#!/bin/sh\necho 1000\n')
        (tools / 'id').chmod(0o755)
        environment = dict(os.environ, HOME=str(self.root), PATH=str(tools) + os.pathsep + os.environ['PATH'])
        digest = hashlib.sha256(archive.read_bytes()).hexdigest() if expected else '0' * 64
        return subprocess.run(['sh', str(WINDOWS / 'wsl-bootstrap.sh'), str(archive), digest],
                              env=environment, capture_output=True)

    def test_bootstrap_checks_hash_before_interpreter_and_preserves_argv(self):
        result = self.bootstrap(True)
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = (self.root / 'args').read_text().splitlines()
        self.assertEqual(arguments[0], '-B')
        self.assertEqual(arguments[1], str(WINDOWS / 'wsl/setup.py'))
        self.assertEqual(arguments[2], str(self.root / 'bundle with spaces.tar.gz'))

    def test_bootstrap_rejects_hash_before_running_interpreter(self):
        result = self.bootstrap(False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'hash mismatch', result.stderr)
        self.assertFalse((self.root / 'args').exists())

    def fixture(self):
        dependency = repository(self.root / 'dependency')
        content = repository(self.root / 'content')
        git(content, 'submodule', 'add', '-q', str(dependency), 'license')
        git(content, 'commit', '-qam', 'Pin license')
        engine = repository(self.root / 'engine')
        transcript = repository(self.root / 'transcript')
        host = repository(self.root / 'host')
        (host / 'windows').mkdir()
        (host / 'windows/stage-commands.py').write_text('# fixture\n')
        for repo, name in [(engine, 'src'), (content, 'third_party/kilix-content'),
                           (transcript, 'third_party/kilix-transcript-clean')]:
            git(host, 'submodule', 'add', '-q', str(repo), name)
        git(host, 'submodule', 'update', '--init', '--recursive')
        git(host, 'add', '.')
        git(host, 'commit', '-qm', 'Pin packaged source closure')
        return host

    def test_export_includes_recursive_licenses_pins_and_asset_checksums(self):
        host = self.fixture()
        output = self.root / 'output'
        output.mkdir()
        for name in ['kilix-0.2.2-windows-x64-dev.zip', 'companion.tar.gz', 'signing.json', 'toolchain-packages.txt']:
            (output / name).write_bytes(b'fixture')
        exporter.export(output, host)
        with tarfile.open(output / 'kilix-0.2.2-windows-source.tar.gz') as archive:
            names = archive.getnames()
        for name in ['src/LICENSE', 'third_party/kilix-content/license/LICENSE', 'third_party/kilix-transcript-clean/LICENSE']:
            self.assertIn(name, names)
        revisions = json.loads((output / 'source-revisions.json').read_text())
        self.assertEqual(revisions['third_party/kilix-content/license'], git(host / 'third_party/kilix-content/license', 'rev-parse', 'HEAD'))
        for line in (output / 'SHA256SUMS').read_text().splitlines():
            digest, name = line.split('  ')
            self.assertEqual(digest, hashlib.sha256((output / name).read_bytes()).hexdigest())
        self.assertIn('  signing.json', (output / 'SHA256SUMS').read_text())

    def test_export_refuses_dirty_or_wrong_revision_dependencies(self):
        host = self.fixture()
        nested = host / 'third_party/kilix-content/license'
        (nested / 'LICENSE').write_text('modified')
        with self.assertRaisesRegex(ValueError, 'clean checkout'):
            exporter.source_closure(host)
        git(nested, 'add', '.')
        git(nested, 'commit', '-qm', 'Different commit')
        with self.assertRaisesRegex(ValueError, 'committed gitlink'):
            exporter.source_closure(host)


if __name__ == '__main__':
    unittest.main()
