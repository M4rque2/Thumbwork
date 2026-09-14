import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import requests

from thumbwork.llm_client import LLMInvokeError, parse_streaming_response
from thumbwork.models import client_for_profile, validate_profile


def response_with(body):
    response = requests.Response()
    response.status_code = 200
    response._content = body.encode("utf-8")
    response._content_consumed = True
    response.close = Mock()
    return response


def content_event(text):
    return "data: " + json.dumps({"choices": [{"delta": {"content": text}}]}) + "\n\n"


class StreamingResponseTests(unittest.TestCase):
    def test_multiline_events_metadata_reasoning_and_utf8(self):
        response = response_with(
            ': heartbeat\r\n\r\nevent: message\r\nid: 1\r\n'
            'data: {"choices":\r\ndata: [{"delta": {"reasoning_content": "thinking"}}]}\r\n\r\n'
            'data: {"choices": [{"delta": {"content": "地球"}}]}\n\n'
            'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n'
            'data: {"choices": [], "usage": {"completion_tokens": 10}}\n\n'
            'data: [DONE]\n\n'
        )
        result = parse_streaming_response(response)
        self.assertEqual((result.content, result.reasoning), ("地球", "thinking"))
        self.assertEqual(result.usage, {"completion_tokens": 10})

    def test_missing_completion_marker_rejects_partial_answer(self):
        response = response_with(content_event("partial"))
        with self.assertRaisesRegex(ValueError, "before.*DONE"):
            parse_streaming_response(response)

    def test_unterminated_completion_event_is_not_accepted(self):
        response = response_with(content_event("partial") + "data: [DONE]\n")
        with self.assertRaisesRegex(ValueError, "before.*DONE"):
            parse_streaming_response(response)

    def test_malformed_event_is_not_silently_skipped(self):
        for data in ("{broken", "[]"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_streaming_response(response_with(f"data: {data}\n\ndata: [DONE]\n\n"))

    def test_server_error_after_http_success_is_rejected(self):
        response = response_with('data: {"error": {"message": "generation failed"}}\n\n')
        with self.assertRaisesRegex(ValueError, "generation failed"):
            parse_streaming_response(response)


class StreamingClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config_path = Path(self.temp.name) / "model.json"
        # A legacy config must not turn streaming off.
        self.config_path.write_text(json.dumps({
            "base_url": "https://example.invalid/v1",
            "api_key": "test-key",
            "model_id": "test-model",
            "stream": False,
        }))
        self.client = client_for_profile(json.loads(self.config_path.read_text()), trace_dir=self.temp.name)
        self.messages = [{"role": "user", "content": [{"text": "hello"}]}]

    def test_fixed_streaming_and_retry_discard_partial_response(self):
        partial = response_with(content_event("discard this"))
        complete = response_with(content_event("complete answer") + "data: [DONE]\n\n")
        self.assertIs(self.client.include_usage, True)
        with patch("thumbwork.llm_client.requests.post", side_effect=[partial, complete]) as post, \
                patch("thumbwork.llm_client.time.sleep"), patch("builtins.print"):
            content = self.client.invoke(self.messages).content
        self.assertEqual(content, "complete answer")
        self.assertEqual(post.call_count, 2)
        for call in post.call_args_list:
            self.assertEqual(call.args[0], "https://example.invalid/v1/chat/completions")
            self.assertEqual(set(call.kwargs["json"]), {"model", "messages", "stream", "stream_options"})
            self.assertIs(call.kwargs["json"]["stream"], True)
            self.assertIs(call.kwargs["stream"], True)
            self.assertEqual(call.kwargs["headers"]["accept"], "text/event-stream")
        partial.close.assert_called_once()
        complete.close.assert_called_once()
        traces = [json.loads(p.read_text()) for p in Path(self.temp.name).glob("llm_trace_*.json")]
        self.assertEqual(sum(trace["error"] is not None for trace in traces), 1)

    def test_failed_stream_exhausts_retries_and_closes_response(self):
        response = response_with(content_event("partial"))
        self.client.max_retry = 1
        with patch("thumbwork.llm_client.requests.post", return_value=response), patch("builtins.print"):
            with self.assertRaises(LLMInvokeError):
                self.client.invoke(self.messages)
        response.close.assert_called_once()

    def test_http_failure_preserves_server_explanation_and_redacts_secret(self):
        response = response_with(json.dumps({"error": {"message": "Too many images for test-key"}}))
        response.status_code = 500
        self.client.max_retry = 1
        with patch("thumbwork.llm_client.requests.post", return_value=response), patch("builtins.print") as output:
            with self.assertRaises(LLMInvokeError) as caught:
                self.client.invoke(self.messages)
        message = str(caught.exception)
        self.assertIn("500", message)
        self.assertIn("Too many images for [REDACTED]", message)
        self.assertNotIn("test-key", message)
        self.assertNotIn("test-key", str(output.call_args_list))
        trace = json.loads(next(Path(self.temp.name).glob("llm_trace_*.json")).read_text())
        self.assertIn("Too many images for [REDACTED]", trace["error"])
        response.close.assert_called_once()

    def test_plain_text_server_detail_is_bounded(self):
        response = response_with("Upstream failed\n" + "x" * 3000)
        response.status_code = 502
        self.client.max_retry = 1
        with patch("thumbwork.llm_client.requests.post", return_value=response), patch("builtins.print"):
            with self.assertRaises(LLMInvokeError) as caught:
                self.client.invoke(self.messages)
        self.assertIn("Upstream failed", str(caught.exception))
        self.assertLess(len(str(caught.exception)), 1200)

    def test_base_url_preserves_gateway_path_and_handles_trailing_slash(self):
        expected = "https://example.invalid/inference/qwen/model/v1/chat/completions"
        for suffix in ("", "/"):
            with self.subTest(suffix=suffix):
                config = json.loads(self.config_path.read_text())
                config["base_url"] = "https://example.invalid/inference/qwen/model/v1" + suffix
                self.config_path.write_text(json.dumps(config))
                client = client_for_profile(json.loads(self.config_path.read_text()), trace_dir=self.temp.name)
                response = response_with(content_event("answer") + "data: [DONE]\n\n")
                with patch("thumbwork.llm_client.requests.post", return_value=response) as post:
                    client.invoke(self.messages)
                self.assertEqual(post.call_args.args[0], expected)
                trace = json.loads(max(Path(self.temp.name).glob("llm_trace_*.json")).read_text())
                self.assertEqual(trace["metadata"]["url"], expected)

    def test_explicit_thinking_switch_is_forwarded_and_traced(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                config = json.loads(self.config_path.read_text())
                config["enable_thinking"] = enabled
                self.config_path.write_text(json.dumps(config))
                client = client_for_profile(json.loads(self.config_path.read_text()), trace_dir=self.temp.name)
                response = response_with(content_event("answer") + "data: [DONE]\n\n")
                with patch("thumbwork.llm_client.requests.post", return_value=response) as post:
                    client.invoke(self.messages)
                self.assertEqual(post.call_args.kwargs["json"]["chat_template_kwargs"],
                                 {"enable_thinking": enabled})
                trace_path = max(Path(self.temp.name).glob("llm_trace_*.json"))
                trace = json.loads(trace_path.read_text())
                self.assertIs(trace["request"]["chat_template_kwargs"]["enable_thinking"], enabled)

    def test_invalid_thinking_switch_is_rejected(self):
        for value in ("false", "true", 0, 1, None, [], {}):
            with self.subTest(value=value):
                config = json.loads(self.config_path.read_text())
                config["enable_thinking"] = value
                self.config_path.write_text(json.dumps(config))
                with self.assertRaisesRegex(ValueError, "must be a boolean"):
                    validate_profile(config)


class ModelConfigValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "model.json"
        self.valid = {
            "base_url": "https://example.invalid/v1",
            "api_key": "secret-test-key",
            "model_id": "test-model",
        }

    def test_missing_required_fields_have_clear_errors(self):
        for key in self.valid:
            with self.subTest(key=key):
                config = dict(self.valid); del config[key]
                with self.assertRaisesRegex(ValueError, key + " must be a non-empty string"):
                    validate_profile(config)

    def test_invalid_required_values_are_not_coerced_to_strings(self):
        for key in self.valid:
            for value in (None, "", " \t\n", False, True, 0, 42, [], {}, {"secret": "secret-test-key"}):
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ValueError) as caught:
                        validate_profile(dict(self.valid, **{key: value}))
                    self.assertNotIn("secret-test-key", str(caught.exception))

    def test_valid_strings_are_trimmed_and_optional_thinking_stays_optional(self):
        config = {key: f"  {value}  " for key, value in self.valid.items()}
        config["base_url"] = "  https://example.invalid/v1/  "
        self.assertEqual(validate_profile(config), self.valid)
