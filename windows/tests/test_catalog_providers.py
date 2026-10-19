"""Offline adapter checks and opt-in tests against installed public sources.

Functional fixtures: KILIX_PROVIDER_FIXTURE_ROOT points to a generation with
apps/<name> installed by catalog_providers.build; KILIX_PROVIDER_PYTHON names
that generation's managed interpreter. No test downloads models or sources.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
import zlib

SCRIPT = Path(__file__).resolve().parents[1] / 'wsl/catalog_providers.py'
SPEC = importlib.util.spec_from_file_location('catalog_providers_tested', SCRIPT)
providers = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(providers)


class AdapterTests(unittest.TestCase):
    def test_launch_is_isolated_and_literal(self):
        arguments = ['', 'a b', 'café 日本語', '$HOME;$(false)', '--help']
        spec = {'name': 'kilix-ollama', 'source': 'apps/kilix-ollama', 'launch_args': arguments}
        argv = providers.command(spec, Path('/owned generation'), python='/managed python')
        self.assertEqual(argv[:4], ['/managed python', '-I', '-S', '-B'])
        self.assertEqual(argv[7], '/owned generation/apps/kilix-ollama')
        self.assertEqual(argv[8:], ['--', *arguments])
        self.assertEqual(providers.command(spec, Path('/owned generation'), probe=True)[-1], '--')

    def test_capabilities_do_not_imply_inference(self):
        for name in providers.APPLICATIONS:
            with self.subTest(name=name):
                status = providers.capabilities(name)
                self.assertTrue(status['ready'])
                self.assertEqual(status['status'], 'limited')
                self.assertFalse(status['inference_ready'])
                self.assertFalse(status['model_downloads_on_launch'])
                self.assertTrue(status['missing_artifacts'])

    def test_failed_probe_never_becomes_ready(self):
        spec = {'name': 'kilix-ollama', 'source': 'apps/kilix-ollama'}
        for result in (subprocess.CompletedProcess([], 2, '', 'missing library'),
                       subprocess.CompletedProcess([], 0, 'banner\n{}', ''),
                       subprocess.CompletedProcess([], 0, '[]', ''),
                       subprocess.CompletedProcess([], 0, '{"schema":1,"name":"wrong"}', '')):
            with mock.patch.object(providers.subprocess, 'run', return_value=result):
                status = providers.readiness(spec, Path('/owned'))
            self.assertFalse(status['ready'])
            self.assertFalse(status['inference_ready'])

    def test_model_setup_preserves_supplied_directory_and_root(self):
        args = ['--root', '/owned store', 'install', 'bonsai-8b', '--from', '/owned $input;--help']
        authority = mock.Mock()
        authority.main.return_value = 3
        with mock.patch.object(providers, '_content_cli', return_value=authority):
            self.assertEqual(providers.model_setup(args), 3)
        authority.main.assert_called_once_with(args)

    def test_dependency_selection_excludes_optional_model_engines(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / 'runtime').mkdir()
            package = ('[[package]]\nname = "{name}"\nversion = "1.0"\n'
                       'source = {{registry = "https://pypi.org/simple"}}\n'
                       'wheels = [{{hash = "sha256:{digest}"}}]\n')
            body = ''.join(package.format(name=name, digest='a' * 64)
                           for name in ('pymupdf', 'pymupdf4llm', 'torch', 'docling'))
            (source / 'runtime/uv.lock').write_text(body)
            requirements = providers._locked_requirements(source, 'kilix-pdf-conversion')
            self.assertIn('pymupdf==1.0 --hash=sha256:', requirements)
            self.assertNotIn('torch', requirements)
            self.assertNotIn('docling', requirements)
            (source / 'runtime/uv.lock').write_text(body.replace('"https://pypi.org/simple"', '"https://example.invalid"'))
            with self.assertRaisesRegex(ValueError, 'locked public index'):
                providers._locked_requirements(source, 'kilix-pdf-conversion')

    def test_runtime_marker_refuses_changed_interpreter(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / providers.MARKER).write_text(json.dumps(
                {'schema': 1, 'name': 'kilix-ollama', 'python': [0, 0]}))
            with self.assertRaisesRegex(ValueError, 'reinstall'):
                providers._activate('kilix-ollama', source)

    def test_ollama_inference_requires_receipt_and_exact_runtime_manifest(self):
        calls = []
        entry = types.SimpleNamespace(display_tag='granite4.1:3b', manifest_digest='sha256:' + 'a' * 64)
        catalog = types.ModuleType('kilix_ollama.catalog')
        catalog.find_entry = lambda model: entry
        errors = types.ModuleType('kilix_ollama.errors')
        errors.RuntimeUnavailable = ValueError

        class Client:
            digest = entry.manifest_digest

            def require_installed(self, model):
                calls.append('installed')
                return {'digest': self.digest}

            def _stream(self, **kwargs):
                calls.append(('stream', kwargs))
                return 'owned-fixture-response'

        with mock.patch.dict(sys.modules, {'kilix_ollama.catalog': catalog, 'kilix_ollama.errors': errors}):
            client = providers._verified_ollama_client(Client)()
            request = dict(route='/api/generate', model=entry.display_tag,
                           payload={'prompt': '$HOME; --help'}, on_delta=None)
            with mock.patch.object(providers, '_verify_model', side_effect=ValueError('receipt missing')):
                with self.assertRaisesRegex(ValueError, 'receipt missing'):
                    client._stream(**request)
            self.assertEqual(calls, [])
            with mock.patch.object(providers, '_verify_model', return_value=types.SimpleNamespace(manifest_sha256='b' * 64)):
                with self.assertRaisesRegex(ValueError, 'Content model differs'):
                    client._stream(**request)
            self.assertEqual(calls, [])
            with mock.patch.object(providers, '_verify_model', return_value=types.SimpleNamespace(manifest_sha256='a' * 64)) as verify:
                client.digest = 'sha256:' + 'b' * 64
                with self.assertRaisesRegex(ValueError, 'manifest'):
                    client._stream(**request)
                self.assertEqual(calls, ['installed'])
                client.digest = entry.manifest_digest
                self.assertEqual(client._stream(**request), 'owned-fixture-response')
                verify.assert_called_with('granite4.1-3b')
                self.assertEqual(calls[-1], ('stream', request))


@unittest.skipUnless(os.environ.get('KILIX_PROVIDER_FIXTURE_ROOT'), 'installed public provider fixtures not selected')
class PublicSourceTests(unittest.TestCase):
    def setUp(self):
        self.generation = Path(os.environ['KILIX_PROVIDER_FIXTURE_ROOT']).resolve()
        self.python = os.environ.get('KILIX_PROVIDER_PYTHON', sys.executable)
        self.temporary = tempfile.TemporaryDirectory(prefix='kilix-provider-owned-')
        self.addCleanup(self.temporary.cleanup)
        self.owned = Path(self.temporary.name)
        self.home = self.owned / 'home'
        self.home.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(('KILIX_', 'KITTY_', 'GPU_TERMINAL_', 'BONSAI_', 'OLLAMA_'))}
        self.env.update(HOME=str(self.home), XDG_DATA_HOME=str(self.home / 'data'),
                        XDG_STATE_HOME=str(self.home / 'state'), XDG_CACHE_HOME=str(self.home / 'cache'),
                        XDG_CONFIG_HOME=str(self.home / 'config'), XDG_RUNTIME_DIR=str(self.home / 'run'),
                        PYTHONDONTWRITEBYTECODE='1')

    def source(self, name):
        return self.generation / 'apps' / name

    def invoke(self, name, *args, expected=0):
        spec = {'name': name, 'source': 'apps/' + name}
        result = subprocess.run(providers.command(spec, self.generation, python=self.python) + list(args),
                                capture_output=True, text=True, encoding='utf-8', timeout=45,
                                env=self.env, cwd=self.owned)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def python_code(self, name, code, *args):
        source = self.source(name)
        bootstrap = ('import sys; sys.path[:0] = ' + repr([str(source / 'src'), str(source / providers.DEPENDENCIES),
                                                        str(source / 'tests')]) + '; ')
        result = subprocess.run([self.python, '-I', '-S', '-B', '-c', bootstrap + code, *map(str, args)],
                                capture_output=True, text=True, encoding='utf-8', timeout=30, env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_offline_capabilities_help_and_models_leave_home_untouched(self):
        for name in providers.APPLICATIONS:
            with self.subTest(name=name):
                result = json.loads(self.invoke(name, '--capabilities').stdout)
                self.assertTrue(result['ready'])
                self.assertFalse(result['inference_ready'])
                self.assertIn('usage:', self.invoke(name, '--help').stdout.lower())
        result = json.loads(self.invoke('kilix-bonsai', 'model-setup', 'list').stdout)
        self.assertTrue(any(item['id'] == 'bonsai-8b' for item in result['models']))
        self.assertEqual(list(self.home.iterdir()), [])

    def test_pdf_conversion_produces_owned_markdown_and_no_model_runtime(self):
        name = 'kilix-pdf-conversion'
        pdf = self.owned / 'owned café --help.pdf'
        self.python_code(name, 'import pymupdf; d=pymupdf.open(); p=d.new_page(); '
                         'p.insert_text((72,72), "Owned PDF conversion fixture.\\n" * 12); '
                         'd.save(sys.argv[1]); d.close()', pdf)
        inspection = json.loads(self.invoke(name, 'inspect', str(pdf), '--json').stdout)
        self.assertIn('page', json.dumps(inspection).lower())
        report = json.loads(self.invoke(name, 'convert', str(pdf), '--profile', 'fast',
                                        '--output-root', str(self.owned / 'out'), '--no-visual-pages', '--json').stdout)
        canonical = Path(report['run_dir']) / 'canonical.md'
        self.assertTrue(canonical.is_relative_to(self.owned))
        self.assertIn('Owned PDF conversion fixture', canonical.read_text())
        doctor = json.loads(self.invoke(name, 'doctor', '--json').stdout)
        self.assertFalse(doctor['optional']['docling']['available'])
        self.assertFalse(doctor['optional']['marker']['available'])

    def test_image_project_edits_commit_and_verify_without_renderer(self):
        name = 'kilix-image-shop'
        compatibility = self.owned / 'compatibility.json'
        compatibility.write_text(self.python_code(name, 'from domain_fixtures import compatibility; '
                                                 'sys.stdout.write(compatibility().canonical_bytes().decode())'))
        project = self.owned / 'owned project'
        initial = json.loads(self.invoke(name, '--output', 'json', 'project', 'create', str(project),
                                         str(compatibility), '--width', '64', '--height', '48').stdout)['result']
        self.invoke(name, '--output', 'json', 'edit', 'adjustment', str(project),
                    'exposure', '--parameter', 'stops=0.5', '--name', 'Literal $HOME; --help')
        verified = json.loads(self.invoke(name, '--output', 'json', 'project', 'verify', str(project)).stdout)
        info = json.loads(self.invoke(name, '--output', 'json', 'project', 'info', str(project)).stdout)['result']
        self.assertNotEqual(initial['revisionId'], info['revisionId'])
        self.assertEqual(info['layerCount'], 1)
        self.assertEqual(verified['command'], 'project.verify')
        self.assertIn('Literal $HOME; --help', self.invoke(name, 'project', 'layers', str(project)).stdout)
        self.assertFalse(json.loads(self.invoke(name, '--capabilities').stdout)['capabilities']['pixel-rendering'])

    def test_background_decoder_inspects_owned_pixels_and_refuses_bad_input(self):
        name = 'kilix-background-remover'
        image = self.owned / 'owned café.png'
        def chunk(kind, payload):
            return struct.pack('!I', len(payload)) + kind + payload + struct.pack('!I', zlib.crc32(kind + payload))
        body = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 3, 2, 8, 6, 0, 0, 0))
        body += chunk(b'IDAT', zlib.compress((b'\0' + bytes([64, 128, 192, 255]) * 3) * 2)) + chunk(b'IEND', b'')
        image.write_bytes(body)
        report = json.loads(self.invoke(name, 'inspect', str(image)).stdout)
        self.assertEqual((report['width'], report['height']), (3, 2))
        self.assertEqual(report['sha256'], hashlib.sha256(body).hexdigest())
        image.write_text('not an image')
        rejected = self.invoke(name, 'inspect', str(image), expected=2)
        self.assertNotIn('Traceback', rejected.stderr)
        rejected = self.invoke(name, 'image', str(image), '--output-dir', str(self.owned), '--reference-profile', expected=2)
        self.assertIn('unqualified reference profile', rejected.stderr)

    def test_bonsai_variants_use_shared_model_plans_without_download(self):
        name = 'kilix-bonsai'
        listing = self.invoke(name, 'list').stdout
        self.assertIn('bonsai-8b', listing)
        for (model, variant), asset in providers.BONSAI_ASSETS.items():
            with self.subTest(asset=asset):
                report = self.invoke(name, 'pull', model, '--variant', variant, '--dry-run').stdout
                self.assertIn(asset, report)
        expected = self.home / '.local/share/kilix-wsl/content/assets/bonsai-8b'
        self.assertEqual(self.invoke(name, 'path', 'bonsai-8b').stdout.strip(), str(expected))
        rejected = self.invoke(name, 'model-setup', 'verify', 'bonsai-8b', expected=1)
        self.assertIn('receipt', rejected.stderr)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_ollama_metadata_conversations_and_missing_service(self):
        name = 'kilix-ollama'
        report = json.loads(self.invoke(name, 'library', '--json').stdout)
        self.assertIn('granite4.1:3b', json.dumps(report))
        policy = json.loads(self.invoke(name, 'policy', '--json').stdout)
        self.assertNotIn('/api/pull', json.dumps(policy.get('routes', {})))
        unknown = self.invoke(name, 'pull', 'unknown-owned-fixture', expected=2)
        self.assertNotIn('Traceback', unknown.stderr)
        self.invoke(name, 'conversations', 'list', '--json')
        missing = subprocess.run(providers.command({'name': name, 'source': 'apps/' + name}, self.generation,
                                                   python=self.python) + ['ask', 'granite4.1:3b', 'Owned fixture'],
                                 capture_output=True, text=True, timeout=15, env=self.env, cwd=self.owned)
        self.assertNotEqual(missing.returncode, 0)
        self.assertNotIn('Traceback', missing.stderr)
        self.assertFalse((self.home / 'run').exists())


if __name__ == '__main__':
    unittest.main()
