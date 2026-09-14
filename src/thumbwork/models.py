"""Named endpoint profiles. Secrets live only in the configuration directory."""
from __future__ import annotations
import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
import requests
from PIL import Image, ImageDraw
from .llm_client import OpenAICompatibleMultimodalClient
from .storage import atomic_json, config_root, file_lock


def validate_name(name):
    if not isinstance(name, str) or not re.fullmatch(r'[\w][\w.-]{0,99}', name) or name in {'.', '..'}:
        raise ValueError('Name must contain 1–100 letters, numbers, underscores, dots or hyphens, starting with a letter, number or underscore.')
    return name


def normalize_base_url(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Enter an HTTP(S) API URL.')
    base = value.strip().rstrip('/')
    url = urlsplit(base)
    if url.scheme not in {'http', 'https'} or not url.netloc or url.username or url.password or url.query or url.fragment:
        raise ValueError('Use an HTTP(S) API URL without embedded credentials, query parameters or a fragment.')
    # Preserve any gateway/deployment prefix, including its version segment.
    return base.removesuffix('/chat/completions')


def validate_profile(profile):
    if not isinstance(profile, dict):
        raise ValueError('Model profile must be an object')
    p = dict(profile)
    for key in ('base_url', 'api_key', 'model_id'):
        if not isinstance(p.get(key), str) or not p[key].strip():
            raise ValueError(f'{key} must be a non-empty string')
        p[key] = p[key].strip()
    p['base_url'] = normalize_base_url(p['base_url'])
    url = urlsplit(p['base_url'])
    if url.scheme not in {'http', 'https'} or not url.netloc or url.username or url.password or url.query or url.fragment:
        raise ValueError('base_url must be an HTTP(S) API prefix without embedded credentials, query or fragment')
    if 'context_window' in p and (type(p['context_window']) is not int or p['context_window'] <= 0):
        raise ValueError('context_window must be a positive integer')
    if 'enable_thinking' in p and type(p['enable_thinking']) is not bool:
        raise ValueError('enable_thinking must be a boolean')
    if 'thinking_mode' in p and p['thinking_mode'] not in {'on', 'off', 'default', 'unsupported'}:
        raise ValueError('thinking_mode must be on, off, default or unsupported')
    if p.get('token_count_url'):
        if not isinstance(p['token_count_url'], str):
            raise ValueError('token_count_url must be a URL string')
        counter_url = urlsplit(p['token_count_url'])
        if (counter_url.scheme, counter_url.netloc) != (url.scheme, url.netloc):
            raise ValueError('Token counter must use the same origin as base_url')
    return p


def client_for_profile(profile, trace_dir=None):
    p = validate_profile(profile)
    return OpenAICompatibleMultimodalClient(p['base_url'], p['api_key'], p['model_id'],
        llm_trace_dir=str(trace_dir) if trace_dir else None, enable_thinking=p.get('enable_thinking'), token_count_url=p.get('token_count_url'))


def public_profile(profile):
    return {k: v for k, v in profile.items() if k != 'api_key'}


class ModelRegistry:
    def __init__(self, directory=None):
        self.path = config_root(directory) / 'config.json'

    def read(self):
        if not self.path.exists():
            return {'version': 1, 'models': {}}
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise ValueError('Cannot read model configuration; expected valid JSON') from None
        if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('models'), dict):
            raise ValueError('Unsupported model configuration format/version')
        return data

    def get(self, name):
        try:
            profile = self.read()['models'][name]
        except KeyError:
            raise ValueError(f'Unknown model profile: {name}. Use thumbwork models add NAME.') from None
        p = validate_profile(profile)
        if 'context_window' not in p:
            raise ValueError(f'Model profile {name} has no context_window; verify or replace it.')
        return p

    def resolve_name(self, name=None):
        if name is None:
            name = self.read().get('default_model')
            if name is None:
                raise ValueError('No default model configured. Use thumbwork models default NAME or pass --model NAME.')
        validate_name(name)
        self.get(name)
        return name

    def set_default(self, name):
        validate_name(name)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with file_lock(self.path.parent / '.config.lock'):
            self.get(name)
            data = self.read()
            data['default_model'] = name
            atomic_json(self.path, data, private=True)

    def save(self, name, profile, *, replace=False):
        validate_name(name)
        # The read-modify-write is locked as well as atomically replaced.
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with file_lock(self.path.parent / '.config.lock'):
            data = self.read()
            if name in data['models'] and not replace:
                raise ValueError(f'Model profile {name} already exists; use --replace.')
            data['models'][name] = validate_profile(profile)
            atomic_json(self.path, data, private=True)



def reported_thinking_switch(metadata):
    """Only explicit switch metadata counts; reasoning output is not switch support."""
    capabilities = metadata.get('capabilities', {})
    for source in (metadata, capabilities if isinstance(capabilities, dict) else {}):
        for key in ('supports_thinking_switch', 'supports_enable_thinking'):
            if type(source.get(key)) is bool:
                return source[key]
    parameters = metadata.get('supported_parameters')
    if isinstance(parameters, list) and any(key in parameters for key in ('enable_thinking', 'chat_template_kwargs.enable_thinking')):
        return True
    return None


def choose_thinking(profile, metadata, ask_thinking=None):
    p = dict(profile)
    supported = reported_thinking_switch(metadata)
    choice = p.get('thinking_mode')
    choice_source = 'user_supplied'
    if choice is None and 'enable_thinking' in p:
        choice = 'on' if p['enable_thinking'] else 'off'
    if choice is None:
        if supported is False:
            choice = 'unsupported'
            choice_source = 'server_reported'
        elif supported is True and type(metadata.get('default_enable_thinking')) is bool:
            choice = 'on' if metadata['default_enable_thinking'] else 'off'
            choice_source = 'server_reported'
        elif ask_thinking:
            choice = ask_thinking(supported)
        else:
            raise ValueError('Choose a thinking setting with --thinking on, off, default or unsupported. '
                             'The server did not provide a usable default choice; default sends no thinking override.')
    if choice not in {'on', 'off', 'default', 'unsupported'}:
        raise ValueError('Thinking setting must be on, off, default or unsupported.')
    if supported is False and choice in {'on', 'off'}:
        raise ValueError('The server reports no thinking switch. Use --thinking unsupported or default.')
    if supported is True and choice == 'unsupported':
        raise ValueError('The server reports a thinking switch. Choose --thinking on, off or default.')
    p['thinking_mode'] = choice
    p['thinking_mode_source'] = choice_source
    p['thinking_switch_supported'] = supported if supported is not None else (
        True if choice in {'on', 'off'} else False if choice == 'unsupported' else None)
    p['thinking_support_source'] = 'server_reported' if supported is not None else (
        'unknown' if choice == 'default' else 'user_supplied')
    if choice in {'on', 'off'}:
        p['enable_thinking'] = choice == 'on'
    else:
        p.pop('enable_thinking', None)
    return p


def verify_profile(profile, *, ask_context=None, ask_thinking=None, progress=None):
    report = progress or (lambda message: None)
    p = validate_profile(profile)
    headers = {'Authorization': 'Bearer ' + p['api_key']}
    reported = None
    metadata = {}
    report('Checking endpoint metadata...')
    try:
        response = requests.get(p['base_url'] + '/models', headers=headers, timeout=20)
        try:
            if response.status_code in (401, 403):
                raise ValueError('Endpoint authentication failed. Check your API key and access permissions.')
            if response.ok:
                try:
                    data = response.json().get('data')
                except (ValueError, AttributeError):
                    data = None
                if isinstance(data, list):
                    matches = [m for m in data if isinstance(m, dict) and m.get('id') == p['model_id']]
                    if not matches:
                        raise ValueError('Model ID is not listed by the endpoint. Check the model name.')
                    metadata = matches[0]
                    for key in ('max_model_len', 'context_length', 'context_window', 'max_context_length'):
                        value = metadata.get(key)
                        if type(value) is int and value > 0:
                            reported = value
                            break
        finally:
            response.close()
    except requests.RequestException:
        # Metadata is optional; actual completions verify endpoint access.
        pass
    modalities = metadata.get('input_modalities')
    if metadata.get('supports_image_input') is False or (
            isinstance(modalities, list) and modalities and 'image' not in modalities):
        raise ValueError('This endpoint reports no image input support. Thumbwork requires a vision-language model (VLM). Choose another model.')
    if reported:
        if p.get('context_window', reported) > reported:
            raise ValueError(f'Explicit context window exceeds server-reported limit {reported}')
        if 'context_window' not in p:
            p['context_window'] = reported
            p['context_source'] = 'server_reported'
        else:
            p['context_source'] = 'user_supplied'
        p['server_context_window'] = reported
        report(f'Context limit reported by server: {reported:,} tokens.')
    else:
        if 'context_window' not in p and ask_context:
            p['context_window'] = int(ask_context())
        if 'context_window' not in p:
            raise ValueError('Endpoint does not report its context limit; provide --context-window. A short probe cannot verify maximum capacity.')
        p['context_source'] = 'user_supplied'
        p.pop('server_context_window', None)
    p = validate_profile(choose_thinking(p, metadata, ask_thinking))
    report(f'Using {p["context_window"]:,} context tokens ({p["context_source"]}).')
    report(f'Thinking: {p["thinking_mode"]}; switch support: {p["thinking_support_source"]}.')
    p.pop('token_count_url', None)
    report('Checking image understanding with two visual questions...')
    with TemporaryDirectory(prefix='thumbwork-model-probe-') as temporary:
        image = Path(temporary) / 'probe.png'
        client = client_for_profile(p)
        client.max_retry = 1
        # Reverse the colors in the second request to reject canned answers or
        # endpoints that accept an image field but ignore its contents.
        colors = secrets.SystemRandom().sample(['red', 'blue', 'green', 'yellow'], 2)
        for expected in (colors, list(reversed(colors))):
            picture = Image.new('RGB', (128, 64), expected[0])
            ImageDraw.Draw(picture).rectangle((64, 0, 127, 63), fill=expected[1])
            picture.save(image)
            messages = [{'role': 'user', 'content': [
                {'text': 'Name the colors of the left and right halves of this image, in that order. Answer with only two English color names.'},
                {'image': str(image)}]}]
            try:
                result = client.invoke(messages)
            except Exception:
                raise ValueError('Image request failed. Check the URL, API key, model name and chosen thinking setting. Thumbwork requires image input; if this is a text-only endpoint, choose another VLM.') from None
            answer = re.sub(r'<think>.*?</think>', '', result.content, flags=re.DOTALL | re.IGNORECASE)
            found = re.findall(r'\b(?:red|blue|green|yellow)\b', answer.lower())
            if found != expected:
                raise ValueError('Image understanding check failed: the model did not identify the image correctly. Thumbwork requires a VLM that can read screenshots. Choose another model or check that image input is enabled.')
        usage = getattr(result, 'usage', {})
        observed = usage.get('prompt_tokens') if isinstance(usage, dict) else None
        if type(observed) is int and observed > 0:
            prefix = p['base_url'].removesuffix('/v1')
            client.token_count_url = prefix + '/tokenize'
            if client.count_tokens(messages) == observed:
                p['token_count_url'] = client.token_count_url
    report('Image understanding checks passed.')
    p['verified_at'] = datetime.now(timezone.utc).isoformat()
    p['supports_image_input'] = True
    p['verification'] = 'scored_image_probes'
    return p
