from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_qing_media.features.imagegen.intent import explicit_image_prompt

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
        "/aa 生成一张yzgg，要有轨迹球要素",
        "帮我生成一张小糖豆，带有水彩风格",
        "/aa 这个图片是奶娃。根据这个奶娃形象， 画一张玩《影之刃零》的奶蛙",
        "这张是我家的猫，帮我画一张它在窗边睡觉的图",
        "主体是黄色的，眼睛是绿色的。请根据这些特征，绘制一个头像",
        "这是参考图。\n参考这张图，生成一张水彩风格的海报",
        "我想给朋友一个惊喜；请制作一张生日贺卡图片",
        "刚才那张颜色太深了！重新画一张浅色的猫图",
        "这是我家的猫。画一张猫，不要画狗",
        "这张是参考图，画一张猫图，prompt 用英文",
        "这是角色说明。生成一张yzgg，要有轨迹球要素",
        "这是参考图。画一张猫图，文案写你好",
        "这是参考图。画一张猫图，配字为“你好，世界。”",
        "主体叫‘小猫’。请画一张“小猫”在窗边的图",
        "这是参考。\n```text\n画一只狗\n```\n请画一张猫图",
        "> 有人说画一张狗图\n请画一张猫图",
        "This is my cat. Please draw a watercolor picture of it",
        "Draw a cat, don't draw dogs",
        'This is my cat. Please draw "the cat"',
        "/aa 生成一个GIF",
        "这是参考图。帮我制作一个gif表情包",
        "Please generate an animated gif of a cat",
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
        "生成一张表格，要有轨迹球要素",
        "生成一张清单，要有简洁风格",
        "生成一张Excel报表，包含卡通要素",
        "生成一张发票，要有猫猫要素",
        "生成一张yzgg，只要提示词，要有轨迹球要素",
        "不要生成一张yzgg，要有轨迹球要素",
        "他说生成一张yzgg，要有轨迹球要素",
        "生成一张表格",
        "这是我家的猫。你觉得好看吗？",
        "这张图片是奶娃。根据这个形象，介绍一下它的特点",
        "这是参考图。你能画图吗？",
        "这是参考图。你能生成图片吗？我只是问问",
        "这个配图真准。画图是我的爱好",
        "这是我家的猫。怎么生成一张它的图片？",
        "这是参考图。帮我写一段画猫的提示词",
        "这是参考图。画一张猫，只要提示词",
        "这张是奶娃。根据这个形象，画一张猫图，不用画，只写prompt",
        "这是我家的猫。不要画一张它的图",
        "画一张猫。算了，别画了",
        "这是参考图，画一张猫，不用生成图片",
        "画一张猫，先别生成了，先聊聊",
        "他说：画一张猫图",
        "他说，画一张猫图",
        "他说：\n画一张猫图",
        "他说，根据这个形象，画一张猫图",
        "群友刚才说，帮我画一张猫图",
        "示例：\n画一张猫图",
        "这句怎么理解，画一张猫图",
        "如果用户这样说，画一张猫图，会发生什么？",
        "请翻译：\n画一张猫图",
        "请分析这个指令，生成一张猫图",
        "他说“这个是奶娃。画一张玩游戏的奶娃”。你觉得呢？",
        '他说 "this is a cat. draw a cat"，你觉得呢？',
        "他说「这是参考图，画一张猫图」。你觉得呢？",
        "他说‘这是参考图，画一张猫图’。你觉得呢？",
        "`这是参考图，\n画一张猫图`是什么意思？",
        "`画一张猫图` 是什么指令？",
        "例子如下\n```text\n这是参考图。画一张猫图\n```",
        "~~~text\n这是参考图。画一张猫图\n~~~",
        "> 这是参考图。画一张猫图\n这句话是什么意思？",
        "这是一段文字：\n“画一张猫图",
        "This is my cat. Don't generate an image",
        "Draw a cat. Stop drawing",
        "帮我写代码，生成一张猫图",
        "这个插件的用法是，生成一张猫图",
        "根据这段话“你好，画一张猫图”介绍它的含义",
        'Based on this quote "draw a cat", explain its meaning',
        "你支持gif吗？",
        "这个gif好看吗？",
        "怎么制作gif？",
        "生成一段gif说明",
        "帮我写代码，生成一个gif",
        "这是参考图。生成一个gif，只要提示词",
    ],
)
def test_no_image_routing_without_current_instruction(text):
    assert explicit_image_prompt(text) is None


async def test_plain_chat_never_sends_generates_or_rewrites_memory(
    plugin_module, config
):
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


@pytest.mark.parametrize(
    "message",
    [
        "/aa 画一张猫猫图",
        "/aa 这个图片是奶娃。根据这个奶娃形象， 画一张玩《影之刃零》的奶蛙",
        "这是我家的猫，帮我画一张猫猫图",
    ],
)
async def test_explicit_route_only_guides_model_and_never_sends_raw_prompt(
    plugin_module, config, message
):
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock()
    image = SimpleNamespace(name=plugin_module.TOOL_NAME, active=True)
    prompt = message.removeprefix("/aa ")
    history = [{"role": "user", "content": "先前的聊天"}]
    media_and_memory = [
        SimpleNamespace(type="text", text="quoted image and recalled facts")
    ]
    req = SimpleNamespace(
        prompt=prompt,
        system_prompt="original persona",
        contexts=history,
        extra_user_content_parts=media_and_memory,
        func_tool=RequestToolSet([image]),
    )
    event = FakeEvent(message)
    await plugin.guide_image_requests(event, req)
    assert plugin_module.TOOL_HINT in req.system_prompt
    assert req.prompt == prompt
    assert req.contexts is history and req.extra_user_content_parts is media_and_memory
    assert req.func_tool.get_tool(plugin_module.TOOL_NAME) is image
    assert event.sent == []
    plugin.client.generate.assert_not_awaited()


@pytest.mark.parametrize(
    "message",
    [
        "这是我家的猫。你觉得好看吗？",
        "他说，根据这个形象，画一张猫图",
        "> 根据这个形象，画一张猫图\n这句话是什么意思？",
        "这是参考图。画一张猫，只要提示词",
        "这是参考图。画一张猫，算了，别画了",
        "这是参考图。你能生成图片吗？",
    ],
)
async def test_descriptions_without_authorization_keep_normal_chat(
    plugin_module, config, message
):
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock()
    memory = SimpleNamespace(name="recall_long_term_memory", active=True)
    image = SimpleNamespace(name=plugin_module.TOOL_NAME, active=True)
    tools = RequestToolSet([memory, image])
    history = [{"role": "user", "content": "旧消息：画一张猫图"}]
    req = SimpleNamespace(
        prompt=message,
        system_prompt="original persona",
        contexts=history,
        func_tool=tools,
    )
    event = FakeEvent(message)
    await plugin.guide_image_requests(event, req)
    await plugin.ensure_image_tool_call(event, SimpleNamespace())
    assert req.prompt == message and req.system_prompt == "original persona"
    assert req.contexts is history and req.func_tool.tools == [memory]
    assert tools.tools == [memory, image]
    assert not event.get_extra("imagegen_call_guard_active")
    assert event.sent == [] and event.call_llm is True and not event.stopped
    plugin.client.generate.assert_not_awaited()
