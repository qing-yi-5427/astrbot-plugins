import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class Event:
    def __init__(self, session="bot:GroupMessage:1", admin=True, llm=True):
        self.unified_msg_origin = session
        self.admin = admin
        self.llm = llm
        self.extra = {"_qing_reply_model_footer": "actual-model"}
        self.data = {"text": "hello", "model_name": "existing-model"}
        self.rendered = []
        self.template = '<div class="brand-name">Amadeus</div>'

    def is_admin(self):
        return self.admin

    def get_result(self):
        return SimpleNamespace(is_llm_result=lambda: self.llm)

    def get_extra(self, key):
        return self.extra.get(key)

    def set_extra(self, key, value):
        self.extra[key] = value


def image_bytes(fmt="PNG", size=(32, 32)):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, (40, 150, 220)).save(buffer, format=fmt)
    return buffer.getvalue()


class FakeResponse:
    def __init__(
        self, body=b"", status=200, content_type="image/png", content_length=None
    ):
        self.body = body
        self.status = status
        self.content_type = content_type
        self.content_length = content_length
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def iter_chunked(self, size):
        import asyncio

        for start in range(0, len(self.body), size):
            await asyncio.sleep(0)
            yield self.body[start : start + size]


def fake_session(monkeypatch, module, response):
    calls = []

    class Session:
        def __init__(self, **kwargs):
            calls.append(("session", kwargs))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            if isinstance(response, Exception):
                raise response
            return response

    monkeypatch.setattr(module.aiohttp, "ClientSession", Session)
    return calls


@pytest.fixture
def event():
    return Event()
