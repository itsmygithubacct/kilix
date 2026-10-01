"""Sizer recommendations and licence metadata stay bound to evaluated models."""
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
import system_llm as llm


class SystemLLMTests(unittest.TestCase):
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
