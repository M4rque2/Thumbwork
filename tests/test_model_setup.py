import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from thumbwork.cli import main
from thumbwork.models import ModelRegistry, normalize_base_url
from test_llm_client import response_with, content_event


def completion(text):
    return response_with(content_event(text) + 'data: [DONE]\n\n')


def successful_replies():
    return [completion('red blue'), completion('blue red')]


class ModelSetupTests(unittest.TestCase):
    endpoint = 'https://example.invalid/inference/qwen/vision/v1/chat/completions'
    key = 'secret-setup-key'

    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.registry = ModelRegistry(self.root / 'config')
        env = patch.dict(os.environ, {'THUMBWORK_CONFIG_DIR': str(self.root/'config'),
                                     'THUMBWORK_PROJECTS_DIR': str(self.root/'projects'),
                                     'SETUP_TEST_KEY': self.key})
        env.start(); self.addCleanup(env.stop)
        colors = patch('thumbwork.models.secrets.SystemRandom.sample', return_value=['red', 'blue'])
        colors.start(); self.addCleanup(colors.stop)

    def invoke(self, values=(), metadata=None, replies=None, args=None, interactive=True, status=200):
        values = iter(values)
        self.prompts = []
        def answer(label):
            self.prompts.append(label)
            value = next(values)
            if isinstance(value, BaseException):
                raise value
            return value
        def secret(label):
            self.prompts.append(label)
            return self.key
        response = Mock(ok=status == 200, status_code=status)
        response.json.return_value = {'data': [metadata or {'id': 'vision', 'max_model_len': 131072}]}
        out, err = io.StringIO(), io.StringIO()
        with patch('sys.stdin.isatty', return_value=interactive), patch('builtins.input', side_effect=answer), patch('thumbwork.cli.getpass.getpass', side_effect=secret), patch('thumbwork.models.requests.get', return_value=response) as get, patch('thumbwork.llm_client.requests.post', side_effect=successful_replies() if replies is None else replies) as post, patch('thumbwork.runtime.AdbTools') as adb, patch('thumbwork.smoke_runner.run_scenario') as smoke, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(['models', 'add'] if args is None else args)
        adb.assert_not_called()
        smoke.assert_not_called()
        self.assertNotIn(self.key, out.getvalue() + err.getvalue())
        self.assertFalse((self.root/'projects').exists())
        return code, out.getvalue(), err.getvalue(), get, post

    def test_interactive_setup_saves_after_image_probes_and_suggests_smoke(self):
        code, out, err, get, post = self.invoke([self.endpoint, 'vision', 'on'])
        self.assertEqual(code, 0)
        self.assertIn('Model vision is ready.', out)
        self.assertIn('Optional: run the baseline smoke test later: thumbwork smoke --model vision --task all --mode quick', out)
        self.assertNotIn('smoke tests passed', out)
        self.assertTrue(self.prompts[0].startswith('Endpoint URL'))
        self.assertTrue(self.prompts[1].startswith('API key'))
        self.assertTrue(self.prompts[2].startswith('Model name'))
        self.assertIn('did not report thinking-switch support', err)
        self.assertEqual(get.call_args.args[0], self.endpoint.removesuffix('/chat/completions') + '/models')
        self.assertEqual(post.call_count, 2)
        for call in post.call_args_list:
            self.assertEqual(call.args[0], self.endpoint)
            self.assertEqual(call.kwargs['json']['chat_template_kwargs'], {'enable_thinking': True})
        first, second = [call.kwargs['json']['messages'][0]['content'][1]['image_url']['url'] for call in post.call_args_list[:2]]
        self.assertNotEqual(first, second)
        profile = self.registry.get('vision')
        self.assertEqual(profile['base_url'], self.endpoint.removesuffix('/chat/completions'))
        self.assertEqual(profile['thinking_support_source'], 'user_supplied')
        self.assertTrue(profile['supports_image_input'])
        self.assertNotIn('smoke_test', profile)

    def test_missing_metadata_prompts_and_retries_invalid_input(self):
        values = ['not-a-url', self.endpoint, 'vision', 'lots', '0', '131072', 'maybe', 'default']
        code, out, err, _, post = self.invoke(values, metadata={'id': 'vision'}, args=['models', 'add', '--json'])
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual(result['context_window'], 131072)
        self.assertEqual(result['context_source'], 'user_supplied')
        self.assertIsNone(result['thinking_switch_supported'])
        self.assertEqual(result['thinking_support_source'], 'unknown')
        self.assertEqual(result['suggested_smoke_command'], 'thumbwork smoke --model vision --task all --mode quick')
        self.assertNotIn('smoke_test', result)
        self.assertNotIn('enable_thinking', result)
        self.assertIn('short test cannot measure', err)
        self.assertTrue(all('chat_template_kwargs' not in call.kwargs['json'] for call in post.call_args_list))

    def test_reported_capabilities_and_default_need_no_guessing(self):
        metadata = {'id': 'vision', 'max_model_len': 131072, 'supports_thinking_switch': True,
                    'default_enable_thinking': False, 'input_modalities': ['text', 'image']}
        code, _, _, _, post = self.invoke([self.endpoint, 'vision'], metadata=metadata)
        self.assertEqual(code, 0)
        profile = self.registry.get('vision')
        self.assertEqual(profile['thinking_support_source'], 'server_reported')
        self.assertEqual(profile['thinking_mode_source'], 'server_reported')
        self.assertIs(profile['enable_thinking'], False)
        self.assertEqual(len(self.prompts), 3)
        self.assertTrue(all(call.kwargs['json']['chat_template_kwargs']['enable_thinking'] is False for call in post.call_args_list))

    def test_text_only_metadata_rejects_before_probes_and_save(self):
        code, out, _, _, post = self.invoke([self.endpoint, 'vision'], metadata={'id': 'vision', 'input_modalities': ['text']})
        self.assertEqual(code, 2)
        self.assertIn('Choose another model', json.loads(out)['reason'])
        post.assert_not_called()
        self.assertFalse(self.registry.path.exists())

    def test_no_thinking_switch_and_model_id_with_slash(self):
        metadata = {'id': 'Qwen/vision', 'max_model_len': 131072, 'supports_thinking_switch': False}
        code, out, _, _, post = self.invoke([self.endpoint, 'Qwen/vision'], metadata=metadata)
        self.assertEqual(code, 0)
        self.assertIn('Model Qwen-vision is ready.', out)
        profile = self.registry.get('Qwen-vision')
        self.assertIs(profile['thinking_switch_supported'], False)
        self.assertEqual(profile['thinking_mode'], 'unsupported')
        self.assertNotIn('enable_thinking', profile)
        self.assertTrue(all(call.kwargs['json']['model'] == 'Qwen/vision' for call in post.call_args_list))
        self.assertTrue(all('chat_template_kwargs' not in call.kwargs['json'] for call in post.call_args_list))

    def test_authentication_failure_does_not_save_or_probe(self):
        code, out, _, _, post = self.invoke([self.endpoint, 'vision'], status=401)
        self.assertEqual(code, 2)
        self.assertIn('authentication failed', json.loads(out)['reason'])
        post.assert_not_called()
        self.assertFalse(self.registry.path.exists())

    def test_server_reported_no_switch_rejects_an_explicit_override(self):
        args = ['models', 'add', '--base-url', self.endpoint, '--model-id', 'vision',
                '--api-key-env', 'SETUP_TEST_KEY', '--thinking', 'on']
        metadata = {'id': 'vision', 'max_model_len': 131072, 'supports_thinking_switch': False}
        code, out, _, _, post = self.invoke(args=args, metadata=metadata, interactive=False)
        self.assertEqual(code, 2)
        self.assertIn('reports no thinking switch', json.loads(out)['reason'])
        post.assert_not_called()
        self.assertFalse(self.registry.path.exists())

    def test_endpoint_that_ignores_images_is_not_saved(self):
        code, out, _, _, post = self.invoke([self.endpoint, 'vision', 'default'], replies=[completion('red blue'), completion('red blue')])
        self.assertEqual(code, 2)
        self.assertIn('Image understanding check failed', json.loads(out)['reason'])
        self.assertEqual(post.call_count, 2)
        self.assertFalse(self.registry.path.exists())

    def test_failed_image_check_does_not_replace_existing_profile(self):
        self.registry.save('office', {'base_url': 'https://old.invalid/v1', 'model_id': 'old',
                                     'api_key': self.key, 'context_window': 32768})
        before = self.registry.path.read_bytes()
        replies = [completion('I cannot see images')]
        args = ['models', 'add', 'office', '--replace', '--base-url', self.endpoint,
                '--model-id', 'vision', '--api-key-env', 'SETUP_TEST_KEY', '--thinking', 'default']
        code, out, _, _, post = self.invoke(args=args, replies=replies, interactive=False)
        self.assertEqual(code, 2)
        self.assertIn('Image understanding check failed', json.loads(out)['reason'])
        self.assertEqual(post.call_count, 1)
        self.assertEqual(self.registry.path.read_bytes(), before)

    def test_verifying_saved_model_also_leaves_smoke_for_later(self):
        self.registry.save('office', {'base_url': self.endpoint, 'model_id': 'vision',
                                     'api_key': self.key, 'context_window': 131072,
                                     'thinking_mode': 'default'})
        code, out, _, _, post = self.invoke(args=['models', 'verify', 'office', '--json'], interactive=False)
        self.assertEqual(code, 0)
        self.assertEqual(post.call_count, 2)
        result = json.loads(out)
        self.assertTrue(self.registry.get('office')['supports_image_input'])
        self.assertIn('--model office', result['suggested_smoke_command'])
        self.assertNotIn('smoke_test', result)

    def test_canceling_wizard_does_not_save(self):
        code, out, _, get, post = self.invoke([self.endpoint, EOFError()])
        self.assertEqual(code, 130)
        self.assertEqual(json.loads(out)['status'], 'interrupted')
        get.assert_not_called(); post.assert_not_called()
        self.assertFalse(self.registry.path.exists())

    def test_noninteractive_unknown_thinking_requires_a_choice(self):
        args = ['models', 'add', '--base-url', self.endpoint, '--model-id', 'vision', '--api-key-env', 'SETUP_TEST_KEY']
        code, out, _, _, post = self.invoke(args=args, interactive=False)
        self.assertEqual(code, 2)
        self.assertIn('--thinking', json.loads(out)['reason'])
        post.assert_not_called()
        self.assertFalse(self.registry.path.exists())

    def test_url_normalization_preserves_gateway_and_rejects_unsafe_shapes(self):
        base = self.endpoint.removesuffix('/chat/completions')
        for value in (base, base+'/', self.endpoint, '  '+self.endpoint+'/  '):
            self.assertEqual(normalize_base_url(value), base)
        for value in ('ftp://example.invalid/v1', 'https://user:password@example.invalid/v1', base+'?key=secret', base+'#fragment'):
            with self.assertRaises(ValueError):
                normalize_base_url(value)
