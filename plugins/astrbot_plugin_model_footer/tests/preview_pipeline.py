"""Offline model-response fixture through the real AstrBot rendering pipeline.

No model API request and no QQ message is sent.
"""
import asyncio
import copy
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
from PIL import Image as PILImage
from astrbot.api.provider import LLMResponse
from astrbot.core.message.components import Image
from astrbot.core.message.message_event_result import MessageEventResult, ResultContentType
from astrbot.core.pipeline.result_decorate import stage as pipeline
from astrbot.core.utils.t2i.network_strategy import NetworkRenderStrategy
from astrbot_plugin_model_footer.bridge import MODEL_KEY, _RENDER_EVENT
from astrbot_plugin_model_footer.main import ModelFooterPlugin


class PreviewEvent:
    plugins_name = None
    unified_msg_origin = 'isolated-footer-render-test'

    def __init__(self, text, model):
        self.extra = {}
        self.result = MessageEventResult().message(text).set_result_content_type(ResultContentType.LLM_RESULT)
        self.model = model

    def set_extra(self, key, value):
        self.extra[key] = value

    def get_extra(self, key, default=None):
        return self.extra.get(key, default)

    def get_result(self):
        return self.result

    def get_platform_name(self):
        return 'isolated-preview'

    def is_stopped(self):
        return False


async def main():
    cfg = json.loads(Path('/AstrBot/data/cmd_config.json').read_text(encoding='utf-8-sig'))
    cfg = copy.deepcopy(cfg)
    cfg.update(t2i=True, t2i_strategy='remote', t2i_active_template='codex', t2i_word_threshold=50)
    cfg['platform_settings']['segmented_reply']['enable'] = False
    cfg['platform_settings'].update(reply_prefix='', reply_with_mention=False, reply_with_quote=False)
    cfg['provider_tts_settings']['enable'] = False
    cfg['provider_settings']['display_reasoning_text'] = False
    cfg['content_safety']['also_use_in_response'] = False
    context = SimpleNamespace(astrbot_config=cfg, plugin_manager=SimpleNamespace(context=SimpleNamespace(get_using_tts_provider_async=AsyncMock(return_value=None))))
    plugin = ModelFooterPlugin(MagicMock())
    renderer = NetworkRenderStrategy(sys.argv[1])
    pipeline.html_renderer.network_strategy = renderer
    stage = pipeline.ResultDecorateStage()
    await stage.initialize(context)
    await plugin.initialize()
    text = '## 回复模型标注已启用\n\n这张图片展示文转图的新页脚样式，模型名称来自本次回复的信息。\n\n- 保留原有字体、配色和 Markdown 排版\n- 不同群聊的并发回复不会串用模型标签\n- 自动回退时显示实际使用的回退模型\n\n> 这是一张排版测试图，没有向模型发起请求，也没有发送 QQ 消息。'
    events = [PreviewEvent(text, 'gpt-6.1-sol'), PreviewEvent(text, 'mimo-chat'), PreviewEvent(text, None)]

    async def render(event, filename):
        await plugin.remember_response_model(event, LLMResponse(role='assistant', completion_text=text, raw_completion={'model': event.model}))
        async for _ in stage.process(event):
            pass
        assert _RENDER_EVENT.get() is None
        assert len(event.result.chain) == 1 and isinstance(event.result.chain[0], Image), 'Real pipeline did not produce an image'
        url = event.result.chain[0].url or event.result.chain[0].file
        assert url.startswith('http'), 'Render unexpectedly fell back to a local file'
        async with aiohttp.ClientSession(trust_env=False) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                data = await response.read()
        with PILImage.open(io.BytesIO(data)) as image:
            assert image.width == 720
            image.save(Path(sys.argv[2]) / filename, format='PNG')
            print(filename, image.size, 'model:', event.get_extra(MODEL_KEY), 'bytes:', len(data))
    try:
        await asyncio.gather(*(render(event, name) for event, name in zip(events, ['footer-gpt-preview.png', 'footer-mimo-preview.png', 'footer-unknown-preview.png'])))
    finally:
        await plugin.terminate()


asyncio.run(main())
