import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from thumbwork.models import ModelRegistry, verify_profile, public_profile
from thumbwork.llm_client import LLMResult

class ModelsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.registry = ModelRegistry(self.temp.name)
        colors=patch('thumbwork.models.secrets.SystemRandom.sample',return_value=['red','blue'])
        colors.start();self.addCleanup(colors.stop)
        self.profile = {'base_url':'https://example.invalid/gateway/v1/', 'api_key':'secret-key', 'model_id':'qwen', 'thinking_mode':'default'}

    def verify(self, data, profile=None, status=200):
        response = Mock(status_code=status, ok=status == 200)
        response.json.return_value = data
        client = Mock(); client.invoke.side_effect = [LLMResult('red blue'), LLMResult('blue red')]
        with patch('thumbwork.models.requests.get', return_value=response) as get, patch('thumbwork.models.client_for_profile', return_value=client):
            result = verify_profile(profile or self.profile)
        self.assertEqual(get.call_args.args[0], 'https://example.invalid/gateway/v1/models')
        response.close.assert_called_once()
        return result

    def test_discovery_and_private_registry(self):
        result = self.verify({'data':[{'id':'qwen', 'max_model_len':32768}]})
        self.assertEqual(result['context_source'], 'server_reported')
        self.registry.save('office', result)
        self.assertEqual(self.registry.get('office')['context_window'], 32768)
        self.assertNotIn('secret-key', json.dumps(public_profile(result)))
        if os.name == 'posix':
            self.assertEqual(self.registry.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.registry.path.parent.stat().st_mode & 0o777, 0o700)
        with self.assertRaisesRegex(ValueError, 'already exists'): self.registry.save('office', result)
        self.registry.save('office', result, replace=True)

    def test_missing_metadata_requires_explicit_limit(self):
        with self.assertRaisesRegex(ValueError, 'context-window'): self.verify({}, status=404)
        result = self.verify({}, dict(self.profile, context_window=16384), status=404)
        self.assertEqual(result['context_source'], 'user_supplied')

    def test_default_persists_and_explicit_model_overrides_it(self):
        profile = dict(self.profile, context_window=32768)
        self.registry.save('office', profile)
        self.registry.save('local', dict(profile, model_id='local-model'))
        self.registry.set_default('office')
        reopened = ModelRegistry(self.temp.name)
        self.assertEqual(reopened.resolve_name(), 'office')
        self.assertEqual(reopened.resolve_name('local'), 'local')
        reopened.save('office', dict(profile, context_window=16384), replace=True)
        self.assertEqual(reopened.resolve_name(), 'office')
        self.assertEqual(reopened.get('office')['context_window'], 16384)
        with self.assertRaisesRegex(ValueError, 'Unknown model profile'):
            reopened.set_default('missing')
        self.assertEqual(reopened.resolve_name(), 'office')
        self.assertEqual(reopened.get('local')['model_id'], 'local-model')
        if os.name == 'posix':
            self.assertEqual(reopened.path.stat().st_mode & 0o777, 0o600)

    def test_legacy_configuration_requires_default_only_when_model_omitted(self):
        self.registry.save('office', dict(self.profile, context_window=32768))
        self.assertNotIn('default_model', self.registry.read())
        self.assertEqual(self.registry.resolve_name('office'), 'office')
        with self.assertRaisesRegex(ValueError, 'models default NAME'):
            self.registry.resolve_name()

    def test_stale_default_does_not_fall_back_to_another_profile(self):
        self.registry.save('office', dict(self.profile, context_window=32768))
        data = self.registry.read(); data['default_model'] = 'removed'
        self.registry.path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'Unknown model profile: removed'):
            self.registry.resolve_name()
        self.assertEqual(self.registry.resolve_name('office'), 'office')

    def test_access_errors_and_wrong_model(self):
        with self.assertRaisesRegex(ValueError, 'authentication'): self.verify({}, status=401)
        with self.assertRaisesRegex(ValueError, 'not listed'): self.verify({'data':[{'id':'other'}]})
        self.assertFalse(self.registry.path.exists())

    def test_limit_cannot_exceed_metadata(self):
        with self.assertRaisesRegex(ValueError, 'exceeds'):
            self.verify({'data':[{'id':'qwen', 'max_model_len':8192}]}, dict(self.profile, context_window=16384))

    def test_probe_errors_do_not_expose_secret(self):
        with patch('thumbwork.models.requests.get', return_value=Mock(ok=False, status_code=404)), patch('thumbwork.models.client_for_profile', side_effect=ValueError('bad')):
            with self.assertRaises(ValueError): verify_profile(dict(self.profile, context_window=8192))

    def test_counter_enabled_only_when_multimodal_usage_agrees(self):
        for count,enabled in ((512,True),(32,False)):
            with self.subTest(count=count):
                response=Mock(status_code=200,ok=True)
                response.json.return_value={'data':[{'id':'qwen','max_model_len':32768}]}
                client=Mock();client.invoke.side_effect=[LLMResult('red blue'),LLMResult('blue red',usage={'prompt_tokens':512})]
                client.count_tokens.return_value=count
                with patch('thumbwork.models.requests.get',return_value=response),patch('thumbwork.models.client_for_profile',return_value=client):
                    profile=verify_profile(self.profile)
                self.assertEqual('token_count_url' in profile,enabled)
                if enabled:self.assertEqual(profile['token_count_url'],'https://example.invalid/gateway/tokenize')
