"""Lightweight AstrBot contract doubles; HTTP tests use a real local server."""

import importlib
import json
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import docstring_parser
import pytest
import pytest_asyncio
from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))


@pytest.fixture
def config():
    schema = json.loads((ROOT / "_conf_schema.json").read_text())
    return {key: item["default"] for key, item in schema.items()}


@pytest_asyncio.fixture
async def server():
    runners = []

    async def start(handler):
        app = web.Application()
        app.router.add_route("*", "/{path:.*}", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        runners.append(runner)
        port = site._server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    yield start
    for runner in runners:
        await runner.cleanup()


class FakeImage:
    @staticmethod
    def fromBytes(data):
        return SimpleNamespace(image=data)


class FakeEvent:
    def __init__(self, message="/i a cat", session="test:private:1", fail_after=None):
        self.message = message
        self.unified_msg_origin = session
        self.sent = []
        self.extra = {}
        self.call_llm = True
        self.stopped = False
        self.fail_after = fail_after
        self.image_attempts = 0

    def get_message_str(self):
        return self.message

    def should_call_llm(self, flag):
        self.call_llm = flag

    def stop_event(self):
        self.stopped = True

    def get_extra(self, key):
        return self.extra.get(key)

    def set_extra(self, key, value):
        self.extra[key] = value

    def plain_result(self, text):
        return SimpleNamespace(text=text)

    def chain_result(self, components):
        return SimpleNamespace(chain=components)

    async def send(self, result):
        if hasattr(result, "chain"):
            if self.fail_after is not None and self.image_attempts >= self.fail_after:
                raise RuntimeError("adapter error with credentials or image bytes")
            self.image_attempts += 1
        self.sent.append(result)


@pytest.fixture
def plugin_module(monkeypatch):
    modules = {}
    for name in (
        "astrbot",
        "astrbot.api",
        "astrbot.api.event",
        "astrbot.api.star",
        "astrbot.api.message_components",
        "astrbot.api.provider",
        "astrbot.core",
        "astrbot.core.agent",
        "astrbot.core.agent.message",
    ):
        module = types.ModuleType(name)
        module.__path__ = []
        modules[name] = module
        monkeypatch.setitem(sys.modules, name, module)

    def command(name, alias):
        def decorate(func):
            func.command_name = name
            func.aliases = alias
            return func

        return decorate

    def tool(name):
        def decorate(func):
            doc = docstring_parser.parse(func.__doc__)
            func.tool_name = name
            func.tool_args = {arg.arg_name: arg.type_name for arg in doc.params}
            return func

        return decorate

    class FakeStar:
        def __init__(self, context):
            self.context = context

    modules["astrbot.api"].AstrBotConfig = dict
    modules["astrbot.api"].logger = logging.getLogger("imagegen-test")
    modules["astrbot.api.event"].AstrMessageEvent = FakeEvent
    modules["astrbot.api.event"].filter = SimpleNamespace(
        command=command,
        llm_tool=tool,
        on_llm_request=lambda **kwargs: lambda func: func,
        on_llm_response=lambda **kwargs: lambda func: func,
        on_agent_done=lambda **kwargs: lambda func: func,
    )
    modules["astrbot.api.star"].Context = object
    modules["astrbot.api.star"].Star = FakeStar
    modules["astrbot.api.star"].register = lambda *args: lambda cls: cls
    modules["astrbot.api.message_components"].Image = FakeImage
    modules["astrbot.api.provider"].ProviderRequest = SimpleNamespace
    modules["astrbot.api.provider"].LLMResponse = SimpleNamespace
    modules["astrbot.core.agent.message"].TextPart = lambda text: SimpleNamespace(
        type="text", text=text
    )
    monkeypatch.delitem(sys.modules, "astrbot_plugin_imagegen.main", raising=False)
    module = importlib.import_module("astrbot_plugin_imagegen.main")
    yield module
    sys.modules.pop("astrbot_plugin_imagegen.main", None)
