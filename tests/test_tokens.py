import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from PIL import Image
from thumbwork.tokens import TokenCounter
from thumbwork.llm_client import OpenAICompatibleMultimodalClient, ContextOverflowError
from test_llm_client import response_with, content_event

class TokenTests(unittest.TestCase):
    def test_unicode_images_repetition_and_calibration(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'screen.png';Image.new('RGB',(32,32)).save(path)
            small=[{'role':'user','content':[{'text':'中文'}, {'image':str(path)}]}]
            counter=TokenCounter();before=counter.count(small)
            self.assertGreater(before,len('中文'))
            repeated=[{'role':'user','content':small[0]['content']*2}]
            self.assertGreater(counter.count(repeated),before)
            counter.observe(small,{'prompt_tokens':before*2})
            self.assertGreater(counter.count(small),before*2)
            calibrated=counter.calibration;counter.observe(small,{'prompt_tokens':1})
            self.assertEqual(counter.calibration,calibrated)
            Image.new('RGB',(1000,2000)).save(path)
            self.assertGreater(TokenCounter().count(small),before)
    def test_optional_usage_extension_fallback(self):
        bad=response_with('{"error":"stream_options unsupported"}');bad.status_code=400
        good=response_with(content_event('answer')+'data: [DONE]\n\n')
        client=OpenAICompatibleMultimodalClient('https://example.invalid/v1','secret','test',max_retry=1)
        with patch('thumbwork.llm_client.requests.post',side_effect=[bad,good]) as post:
            result=client.invoke([{'role':'user','content':[{'text':'hi'}]}])
        self.assertEqual(result.content,'answer');self.assertEqual(result.usage,{})
        self.assertNotIn('stream_options',post.call_args.kwargs['json'])
        self.assertFalse(client.include_usage)
    def test_overflow_is_not_retried_by_http_client(self):
        bad=response_with('{"error":"context_length_exceeded"}');bad.status_code=400
        client=OpenAICompatibleMultimodalClient('https://example.invalid/v1','secret','test')
        with patch('thumbwork.llm_client.requests.post',return_value=bad) as post:
            with self.assertRaises(ContextOverflowError): client.invoke([{'role':'user','content':[{'text':'hi'}]}])
        self.assertEqual(post.call_count,1)

    def test_verified_endpoint_count_and_fallback(self):
        from unittest.mock import Mock
        client=Mock();client.count_tokens.return_value=900
        counter=TokenCounter(client)
        messages=[{'role':'user','content':[{'text':'some input'}]}]
        self.assertEqual(counter.count(messages),900)
        self.assertEqual(counter.last_source,'endpoint')
        counter.observe(messages,{'prompt_tokens':1800})
        self.assertEqual(counter.count(messages),1800)
        client.count_tokens.return_value=None
        self.assertGreater(counter.count(messages),1800)
        self.assertEqual(counter.last_source,'estimated')

    def test_trace_redacts_escaped_secret_before_json_serialization(self):
        import json
        from thumbwork.llm_client import LlmTraceLogger
        with TemporaryDirectory() as tmp:
            secret='key-"with-backslash\\'
            logger=LlmTraceLogger(tmp,secret=secret)
            path=logger.log({'prompt':secret},error='error '+secret)
            data=json.loads(Path(path).read_text())
            self.assertEqual(data['request']['prompt'],'[REDACTED]')
            self.assertEqual(data['error'],'error [REDACTED]')
