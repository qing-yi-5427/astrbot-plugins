from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_imagegen.intent import explicit_image_prompt

from .conftest import FakeEvent
from .test_plugin import RequestToolSet


@pytest.mark.parametrize(
    "text",
    [
        "/aa 画一张猫猫图",
        "aa 生成一张猫图",
        "帮我画一只猫",
        "请给我绘制一个头像",
        "我想要制作一张海报",
        "生成一张带文字的图片",
        "!aa 画只猫",
        "draw a cat",
        "please generate a picture of a cat",
        "can you create a cat image",
        "根据你对群友丢哥的理解，画一张丢哥",
        "根据你对小糖豆的记忆画一张小糖豆",
        "根据记忆画小糖豆",
        "请再画一张猫图",
        "画一张猫图，prompt 用英文",
        "画一张猫，不要画狗",
        "生成一张海报，文案写你好",
        "创作一张猫的插画",
        "based on our conversation, draw a cat",
    ],
)
def test_explicit_requests(text):
    assert explicit_image_prompt(text)


@pytest.mark.parametrize(
    "text",
    [
        "你好",
        "怎么生成一张猫图",
        "如何画猫",
        "教我画图",
        "画猫的提示词怎么写",
        "不要画一张猫图",
        "生成一张猫图，不用画，只要提示词",
        "别生成图片",
        "生成一个生图插件",
        "写代码生成猫图",
        "他说生成一张猫图",
        "只讨论图片生成方法",
        "画个重点",
        "解释如何生成图片",
        "don't generate a cat picture",
        "how to create an image",
        "这张猫图好看吗",
        "生成一段猫猫文案",
        "draw a conclusion",
        "draw comparisons between two models",
        "/aa 介绍一下你记忆里的小糖豆",
        "我喜欢画画",
        "小糖豆的画像好看吗",
        "再来一张",
        "根据你画的小糖豆介绍他的兴趣",
        "根据那幅画介绍画家",
        "你能画图吗",
        "你能生成图片吗",
        "能不能画图？",
        "创作一首诗",
        "创作一个故事",
        "画图是我的爱好",
        "绘画很难",
    ],
)
def test_no_image_routing_without_current_instruction(text):
    assert explicit_image_prompt(text) is None


async def test_plain_chat_never_sends_generates_or_rewrites_memory(plugin_module, config):
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock()
    memory = SimpleNamespace(name="recall_long_term_memory", active=True)
    image = SimpleNamespace(name=plugin_module.TOOL_NAME, active=True)
    tools = RequestToolSet([memory, image])
    remembered = [SimpleNamespace(type="text", text="小糖豆的旧资料", _no_save=True)]
    history = [
        {"role": "user", "content": "生成一张猫图"},
        {"role": "assistant", "content": "画好了"},
    ]
    req = SimpleNamespace(
        prompt="介绍一下你记忆里的小糖豆",
        system_prompt="Amadeus 红莉栖",
        contexts=history,
        extra_user_content_parts=remembered,
        func_tool=tools,
    )
    event = FakeEvent("/aa 介绍一下你记忆里的小糖豆")
    await plugin.guide_image_requests(event, req)
    assert req.prompt == "介绍一下你记忆里的小糖豆"
    assert req.system_prompt == "Amadeus 红莉栖"
    assert req.contexts is history
    assert req.extra_user_content_parts is remembered
    assert req.func_tool.tools == [memory]
    assert tools.tools == [memory, image]
    assert event.sent == [] and not event.stopped and event.call_llm is True
    plugin.client.generate.assert_not_awaited()
    assert not hasattr(plugin, "fallback_image_request")
    assert not hasattr(plugin, "sync_fallback_history")


async def test_explicit_route_only_guides_model_and_never_sends_raw_prompt(plugin_module, config):
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock()
    image = SimpleNamespace(name=plugin_module.TOOL_NAME, active=True)
    req = SimpleNamespace(
        prompt="画一张猫猫图",
        system_prompt="original persona",
        contexts=[],
        extra_user_content_parts=[],
        func_tool=RequestToolSet([image]),
    )
    event = FakeEvent("/aa 画一张猫猫图")
    await plugin.guide_image_requests(event, req)
    assert plugin_module.TOOL_HINT in req.system_prompt
    assert req.prompt == "画一张猫猫图"
    assert req.func_tool.get_tool(plugin_module.TOOL_NAME) is image
    assert event.sent == []
    plugin.client.generate.assert_not_awaited()
