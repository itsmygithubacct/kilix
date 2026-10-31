"""Sizer recommendations and licence metadata stay bound to evaluated models."""
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
import urllib.error
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
import system_llm as llm


class SystemLLMTests(unittest.TestCase):
    def test_missing_backend_reports_the_prerequisite_without_starting_a_pull(self):
        opener = Mock()
        cause = urllib.error.URLError(ConnectionRefusedError(111, 'Connection refused'))
        opener.open.side_effect = cause
        for binary, action in ((None, 'Install and start'), ('/usr/bin/ollama', 'Start the local')):
            with self.subTest(binary=binary), \
                    patch.object(llm.urllib.request, 'build_opener', return_value=opener), \
                    patch.object(llm.shutil, 'which', return_value=binary), \
                    patch.object(llm.subprocess, 'call') as pull:
                with self.assertRaisesRegex(ValueError, action) as caught:
                    llm.acquire('qwen3:8b', consented=True)
                self.assertIs(caught.exception.__cause__, cause)
                self.assertIn('127.0.0.1:11434', str(caught.exception))
                self.assertIn('kilix wizard finish', str(caught.exception))
                pull.assert_not_called()

    def test_existing_matching_model_does_not_need_a_cli_or_a_pull(self):
        name = 'qwen3:8b'
        _label, size, _parameters, digest = llm.MODELS[name]
        records = {name: {'name': name, 'size': size, 'digest': digest}}
        with patch.object(llm, 'installed', return_value=records), \
                patch.object(llm.shutil, 'which', return_value=None), \
                patch.object(llm.subprocess, 'call') as pull:
            llm.acquire(name, consented=True)
            ready, note = llm.available_models()
        self.assertEqual(ready, {name})
        self.assertIn('already installed', note)
        pull.assert_not_called()

    def test_missing_cli_is_explained_when_the_server_has_no_matching_model(self):
        with patch.object(llm, 'installed', return_value={}), \
                patch.object(llm.shutil, 'which', return_value=None), \
                patch.object(llm.subprocess, 'call') as pull:
            with self.assertRaisesRegex(ValueError, 'Ollama is not installed'):
                llm.acquire('qwen3:8b', consented=True)
            self.assertEqual(llm.available_models()[0], set())
        pull.assert_not_called()

    def test_pull_and_readiness_stay_bound_to_the_pinned_model_identity(self):
        name = 'qwen3:8b'
        _label, size, _parameters, digest = llm.MODELS[name]
        records = {name: {'name': name, 'size': size, 'digest': digest}}
        with patch.object(llm, 'installed', side_effect=[{}, records]), \
                patch.object(llm.shutil, 'which', return_value='/fixture/ollama'), \
                patch.object(llm.subprocess, 'call', return_value=0) as pull:
            llm.acquire(name, consented=True)
        self.assertEqual(pull.call_args.args[0], ['/fixture/ollama', 'pull', name])
        self.assertEqual(pull.call_args.kwargs['env']['OLLAMA_HOST'], 'http://127.0.0.1:11434')
        records[name]['digest'] = 'different'
        with patch.object(llm, 'installed', return_value=records), \
                patch.object(llm.shutil, 'which', return_value='/fixture/ollama'), \
                patch.object(llm.subprocess, 'call') as pull:
            with self.assertRaisesRegex(ValueError, 'left unchanged'):
                llm.acquire(name, consented=True)
            self.assertNotIn(name, llm.available_models()[0])
        pull.assert_not_called()

    def test_invalid_inventory_never_claims_backend_readiness(self):
        for payload in (b'not json', b'{}', b'{"models":{}}', b'{"models":[null]}',
                        b'{"models":[{"name":"x"},{"name":"x"}]}'):
            with self.subTest(payload=payload):
                opener = Mock()
                opener.open.return_value = io.BytesIO(payload)
                with patch.object(llm.urllib.request, 'build_opener', return_value=opener):
                    ready, message = llm.available_models()
                self.assertEqual(ready, set())
                self.assertIn('invalid model inventory', message)

    def recommend(self, mutate=lambda report: None):
        def run(_args, *, input, stdout, **_kwargs):
            request = json.loads(input)
            rows = [{**row, 'verdict': 'estimated-fit', 'required_ram_bytes': 1,
                     'avatar_budget_bytes': 2} for row in request['models']]
            report = dict(schema='plebian.models.avatar-chat-sizing/v1-development',
                request_sha256=hashlib.sha256(input).hexdigest(), resource_source='live',
                context=8192, selected_model=None, qualification_eligible=False, candidates=rows)
            mutate(report)
            stdout.write(json.dumps(report).encode())
            return Mock(returncode=0)
        with patch.object(llm, 'sizer_executable', return_value='/sizer'), \
                patch.object(llm.subprocess, 'run', side_effect=run):
            return llm.recommend(llm.catalog())

    def test_largest_fitting_tier_is_default(self):
        def limit(report):
            report['candidates'][-1]['verdict'] = 'does-not-fit'
        rows, chosen, _ = self.recommend(limit)
        self.assertEqual(chosen, 'qwen3:8b')
        self.assertEqual(len(rows), 4)

    def test_stale_or_changed_report_never_preselects(self):
        mutations = [lambda r: r.update(request_sha256='0' * 64),
                     lambda r: r.update(resource_source='fixture'),
                     lambda r: r['candidates'][0].update(model_bytes=1),
                     lambda r: r['candidates'][0].update(required_ram_bytes=3)]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                rows, chosen, _ = self.recommend(mutate)
                self.assertEqual(rows, {})
                self.assertIsNone(chosen)

    def test_only_pinned_manifest_licence_layers_are_read(self):
        payload = b'Apache License Version 2.0, January 2004\nFixture terms.'
        digest = hashlib.sha256(payload).hexdigest()
        manifest = json.dumps({'layers': [
            {'mediaType': 'application/vnd.ollama.image.model', 'digest': 'sha256:weights'},
            {'mediaType': 'application/vnd.ollama.image.license', 'digest': 'sha256:' + digest}]}).encode()
        name = 'fixture:1'
        pin = ('Fixture', 1, 1, hashlib.sha256(manifest).hexdigest())
        for content, succeeds in ((payload, True), (b'changed terms', False)):
            with patch.dict(llm.MODELS, {name: pin}), patch.object(llm.urllib.request, 'urlopen',
                    side_effect=[io.BytesIO(manifest), io.BytesIO(content)]) as fetch:
                if succeeds:
                    self.assertEqual(llm.license_texts([name]), {name: [('apache-2.0', payload)]})
                else:
                    with self.assertRaisesRegex(ValueError, 'differs'):
                        llm.license_texts([name])
                self.assertEqual(fetch.call_count, 2)
                self.assertTrue(fetch.call_args.args[0].endswith('/blobs/sha256:' + digest))


if __name__ == '__main__': unittest.main()
