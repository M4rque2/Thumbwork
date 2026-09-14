"""Multimodal token estimates; explicit uncertainty and upward usage calibration."""
from __future__ import annotations
import math
from PIL import Image


class TokenCounter:
    def __init__(self, client=None, calibration=1.0):
        self.client = client
        self.calibration = max(1.0, float(calibration))
        self.last_source = 'estimated'
        self.counter_calibration = 1.0
        self.last_messages = None
        self.last_endpoint_count = None

    @staticmethod
    def raw_estimate(messages):
        # UTF-8 bytes bound ordinary text tokenization conservatively. Image patch costs
        # vary by processor; this is deliberately a heuristic, never an exact token count.
        tokens = 32
        for message in messages:
            tokens += 32
            for part in message['content']:
                if 'text' in part:
                    tokens += len(part['text'].encode('utf-8'))
                elif 'image' in part:
                    path = part['image'].removeprefix('file://')
                    with Image.open(path) as image:
                        width, height = image.size
                    tokens += 256 + math.ceil(width / 16) * math.ceil(height / 16)
        return tokens

    def count(self, messages):
        count = self.client.count_tokens(messages) if self.client and hasattr(self.client, 'count_tokens') else None
        self.last_messages = messages
        self.last_endpoint_count = count
        if type(count) is int and count > 0:
            self.last_source = 'endpoint' if self.counter_calibration == 1 else 'endpoint_adjusted'
            return math.ceil(count * self.counter_calibration)
        self.last_source = 'estimated'
        return math.ceil(self.raw_estimate(messages) * self.calibration * 1.20)

    def observe(self, messages, usage):
        observed = usage.get('prompt_tokens')
        if type(observed) is not int or observed <= 0: return
        self.calibration = max(self.calibration, observed / max(1, self.raw_estimate(messages)))
        if self.client and hasattr(self.client, 'count_tokens'):
            counted = self.last_endpoint_count if self.last_messages is messages else None
            if type(counted) is int and counted > 0:
                self.counter_calibration = max(self.counter_calibration, observed / counted)
