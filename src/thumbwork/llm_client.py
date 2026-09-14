"""LLM client and request/response helpers for OpenAI-compatible endpoints."""

from __future__ import annotations

import base64
import sys
from dataclasses import dataclass, field
import json
import time
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True


def _json_safe(value):
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_json_safe(v) for v in value]
        if hasattr(value, "model_dump"):
            return _json_safe(value.model_dump())
        if hasattr(value, "dict"):
            return _json_safe(value.dict())
        return str(value)

class LlmTraceLogger:
    def __init__(self, trace_dir=None, secret=""):
        self.secret = secret
        self.trace_dir = Path(trace_dir) if trace_dir else None
        if self.trace_dir:
            self.trace_dir.mkdir(parents=True, exist_ok=True)

    def log(self, request_payload, response_payload=None, metadata=None, error=None):
        if self.trace_dir is None:
            return None
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        log_path = self.trace_dir / f"llm_trace_{timestamp}_{uuid.uuid4().hex[:8]}.json"
        # Try to extract any model 'reasoning' text when present in the response
        reasoning = None
        try:
            if isinstance(response_payload, dict):
                # OpenAI-like schema: choices -> [ { message: { reasoning: ... } } ]
                choices = response_payload.get("choices") or []
                if choices and isinstance(choices, list):
                    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
                    if isinstance(msg, dict):
                        reasoning = msg.get("reasoning") or msg.get("explanation")
                # Top-level reasoning key
                if reasoning is None:
                    reasoning = response_payload.get("reasoning")
        except Exception:
            reasoning = None

        record = {
            "timestamp": datetime.now().isoformat(),
            "metadata": _json_safe(metadata or {}),
            "request": _json_safe(request_payload),
            "response": _json_safe(response_payload),
            "reasoning": _json_safe(reasoning) if reasoning is not None else None,
            "error": str(error) if error else None,
        }
        with log_path.open("w", encoding="utf-8") as f:
            def redact(value):
                if isinstance(value, str):
                    return value.replace(self.secret, "[REDACTED]") if self.secret else value
                if isinstance(value, dict): return {redact(k):redact(v) for k,v in value.items()}
                if isinstance(value, list): return [redact(v) for v in value]
                return value
            json.dump(redact(record), f, ensure_ascii=False, indent=2)
        return str(log_path)


class LLMInvokeError(RuntimeError):
    """Raised when LLM invocation fails after all retries."""


def _server_error_detail(response, secret: str) -> str:
    """Keep a concise, redacted server explanation instead of losing it to HTTPError."""
    try:
        body = response.json()
    except ValueError:
        detail = response.text
    else:
        if isinstance(body, dict):
            detail = body.get("error") or body.get("message") or body.get("detail") or ""
            if isinstance(detail, dict):
                detail = detail.get("message") or detail.get("detail") or detail.get("type") or ""
        elif isinstance(body, str):
            detail = body
        else:
            detail = ""
    if not isinstance(detail, str):
        return ""
    if secret:
        detail = detail.replace(secret, "[REDACTED]")
    detail = " ".join(detail.split())
    return detail[:1000] + ("…" if len(detail) > 1000 else "")


def pil_to_base64_png(image: Image.Image) -> str:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def image_to_data_url(image_path: str) -> str:
    if image_path.startswith("file://"):
        image_path = image_path[len("file://") :]
    with Image.open(image_path) as image:
        original_image = image.copy()
    return f"data:image/png;base64,{pil_to_base64_png(original_image)}"


def convert_messages_to_openai_image_url(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Mobile-Agent message parts to OpenAI-compatible multimodal parts."""
    converted = []
    for message in messages:
        content = []
        for item in message["content"]:
            if "text" in item:
                content.append({"type": "text", "text": item["text"]})
            elif "image" in item:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": image_to_data_url(item["image"])},
                    }
                )
        converted.append({"role": message["role"], "content": content})
    return converted


@dataclass
class LLMResult:
    content: str
    reasoning: str = ""
    usage: dict[str, int] = field(default_factory=dict)


class ContextOverflowError(LLMInvokeError):
    """The endpoint explicitly rejected an oversized context; no action was executed."""


def is_context_overflow(text):
    text = text.lower()
    return any(marker in text for marker in (
        "context_length_exceeded", "maximum context length", "context window", "max_model_len",
        "context length exceeded", "too many tokens", "exceeds the model's maximum"))


def parse_streaming_response(response: requests.Response) -> LLMResult:
    """Assemble SSE data events, requiring the API's completion marker."""
    chunks = []
    reasoning_chunks = []
    data_lines = []
    usage = {}
    response.encoding = "utf-8"
    for line in response.iter_lines(decode_unicode=True):
        if line:
            field, separator, value = line.partition(":")
            if field == "data":
                data_lines.append(value.removeprefix(" ") if separator else "")
            continue
        if not data_lines:
            continue
        data = "\n".join(data_lines)
        data_lines.clear()
        if data.strip() == "[DONE]":
            return LLMResult("".join(chunks), "".join(reasoning_chunks), usage)
        try:
            event = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError("Malformed JSON in LLM stream") from exc
        if not isinstance(event, dict):
            raise ValueError("Expected a JSON object in LLM stream")
        if event.get("error") is not None:
            raise ValueError(f"LLM stream error: {event['error']}")
        if isinstance(event.get("usage"), dict):
            usage.update({k: v for k, v in event["usage"].items() if type(v) is int and v >= 0})
        choices = event.get("choices") or []
        if not choices:
            continue
        choice = choices[0]
        delta = choice.get("delta") or {}
        message = choice.get("message") or {}
        for part in (delta, message):
            reasoning = part.get("reasoning_content") or part.get("reasoning")
            if reasoning:
                reasoning_chunks.append(reasoning)
            if part.get("content"):
                chunks.append(part["content"])
    raise ValueError("LLM stream ended before [DONE]; response may be incomplete")


class OpenAICompatibleMultimodalClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model_name: str,
        max_retry: int = 3,
        llm_trace_dir: str | None = None,
        enable_thinking: bool | None = None,
        token_count_url: str | None = None,
    ):
        self.base_url = base_url.strip().rstrip("/")
        self.api_key = api_key
        self.model_name = model_name
        self.max_retry = max_retry
        self.trace_logger = LlmTraceLogger(llm_trace_dir, secret=api_key)
        self.enable_thinking = enable_thinking
        self.include_usage = True
        self.token_count_url = token_count_url

    def count_tokens(self, messages):
        """Use a same-origin counter previously verified with a multimodal probe."""
        if not self.token_count_url:
            return None
        response = None
        try:
            response = requests.post(self.token_count_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model_name, "messages": convert_messages_to_openai_image_url(messages)}, timeout=20)
            response.raise_for_status()
            count = response.json().get("count")
            if type(count) is int and count > 0:
                return count
        except (requests.RequestException, ValueError, AttributeError):
            pass
        finally:
            if response is not None: response.close()
        self.token_count_url = None
        return None

    def invoke(self, messages, *, max_tokens=None) -> LLMResult:
        request_url = f"{self.base_url}/chat/completions"
        payload = {"model": self.model_name,
            "messages": convert_messages_to_openai_image_url(messages), "stream": True}
        if self.include_usage:
            payload["stream_options"] = {"include_usage": True}
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if self.enable_thinking is not None:
            payload["chat_template_kwargs"] = {"enable_thinking": self.enable_thinking}
        headers = {"accept": "text/event-stream", "content-type": "application/json",
            "Authorization": f"Bearer {self.api_key}"}
        metadata = {"provider": "http-openai-compatible", "model": self.model_name,
            "url": request_url, "wrapper": self.__class__.__name__, "stream": True}
        last_error = "Unknown model invocation failure"
        for attempt in range(1, self.max_retry + 1):
            response = None
            try:
                # Retry once without the optional usage extension, even with max_retry=1.
                for compatibility_attempt in range(2):
                    response = requests.post(request_url, json=payload.copy(), headers=headers,
                        stream=True, timeout=300)
                    if response.status_code in (400, 422) and "stream_options" in payload:
                        body = response.text.lower()
                        if ("stream_options" in body or "include_usage" in body) and any(
                                marker in body for marker in ("unsupported", "unknown", "unrecognized", "not permitted", "not allowed", "extra inputs")):
                            response.close()
                            self.include_usage = False
                            payload.pop("stream_options")
                            continue
                    break
                if response.status_code in (400, 413, 422) and is_context_overflow(response.text):
                    raise ContextOverflowError("Endpoint rejected the request because its context is too large")
                response.raise_for_status()
                result = parse_streaming_response(response)
                result.content = result.content.replace(self.api_key, "[REDACTED]")
                result.reasoning = result.reasoning.replace(self.api_key, "[REDACTED]")
                self.trace_logger.log(payload, {"content": result.content, "reasoning": result.reasoning,
                    "usage": result.usage}, metadata=metadata)
                return result
            except ContextOverflowError:
                raise
            except Exception as exc:
                last_error = str(exc).replace(self.api_key, "[REDACTED]")
                if response is not None and response.status_code >= 400:
                    detail = _server_error_detail(response, self.api_key)
                    if detail:
                        last_error += f"; server detail: {detail}"
                if is_context_overflow(last_error):
                    raise ContextOverflowError("Endpoint rejected the request because its context is too large") from None
                self.trace_logger.log(payload, None, metadata=metadata, error=last_error)
                print(f"[WARN] Model call failed on attempt {attempt}: {last_error}", file=sys.stderr)
                if response is not None and 400 <= response.status_code < 500 and response.status_code not in (408, 429):
                    break
            finally:
                if response is not None: response.close()
            if attempt < self.max_retry: time.sleep(5)
        raise LLMInvokeError(f"LLM invoke failed for model '{self.model_name}': {last_error}")
