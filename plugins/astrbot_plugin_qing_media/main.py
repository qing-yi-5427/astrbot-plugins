"""Two-suite facade: only this module registers AstrBot handlers and tools."""

import json
from sys import maxsize
from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.command import GreedyStr
from .runtime import SuiteRuntime, hide_image_tool, safe_filter
from .features.imagegen.main import ImageGenPlugin as ImagegenService
from .features.render.main import T2IStylePlugin as RenderService
from .features.image_source.main import QingImageSourcePlugin as ImageSourceService

LEGACY = {
    "imagegen": "astrbot_plugin_imagegen",
    "render": "astrbot_plugin_t2i_style",
    "image_source": "astrbot_plugin_qing_image_source",
}
LEGACY_IDS = {
    "imagegen": "qingyi/astrbot_plugin_imagegen",
    "render": "qingyi/astrbot_plugin_t2i_style",
    "image_source": "qingyi/astrbot_plugin_qing_image_source",
}
TOOL_NAME = "astrbot_generate_image"


@register("astrbot_plugin_qing_media", "qingyi", "图片与主题", "1.0.0")
class Main(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context, config)
        self.config = config
        self.modules = SuiteRuntime(
            self,
            "astrbot_plugin_qing_media",
            LEGACY,
            LEGACY_IDS,
            {
                "imagegen": (ImagegenService, True),
                "render": (RenderService, True),
                "image_source": (ImageSourceService, True),
            },
        )

    async def initialize(self):
        await self.modules.initialize()

    @safe_filter(filter.on_plugin_loaded(priority=maxsize), LEGACY.values())
    async def guard_legacy_install(self, metadata):
        await self.modules.check_conflict()

    async def terminate(self):
        await self.modules.terminate()

    @safe_filter(filter.command("i", alias={"imagegen", "生图"}), LEGACY.values())
    async def imagegen_image_command(self, event: AstrMessageEvent):
        "生成图片：/i 图片描述（也支持 /imagegen、/生图）。"
        if not await self.modules.allowed("imagegen", event):
            return
        return await self.modules.call("imagegen", "image_command", event)

    @safe_filter(filter.llm_tool(name=TOOL_NAME), LEGACY.values())
    async def imagegen_generate_image(
        self, event: AstrMessageEvent, prompt: str, output_format: str = ""
    ):
        "仅当当前用户明确要求生成图片、画图、绘制插画或制作图片时调用。\n\n将用户要求及对话中的相关细节整理成完整的图片描述；保留用户指定的风格和文字。\n工具会调用 GPT Image 并将真实图片直接发送到当前会话，无需另行调用发送工具。\n返回 JSON 包含 status、generated、sent、message、formats；formats 是实际返回格式。\nsent 只表示确认送达数；实际格式不是 gif 时，不得声称已生成 GIF 动图。\n失败或部分发送时，error 提供原因类别、失败阶段、HTTP 状态和服务端执行状态。\n插件不会另发失败通知，你必须用当前人格向用户解释 message/error，不能沉默或只说会画。\ndelivery_outcome=unknown 表示图片已生成但发送状态未确认，可能已显示，不能说发送零张。\n仅 request_outcome=unknown 表示生成/计费无法确认。不要自动重复调用或虚构成功。\n明确要求 GIF 动图时指定 output_format=gif；未要求格式时留空，沿用插件配置。\n\nArgs:\n    prompt(string): 完整的生图描述，包含主体、场景、风格、构图及需要绘制的文字。\n    output_format(string): 可选；留空沿用配置，或指定 png、jpeg、webp、gif。GIF 需服务支持。"
        if not await self.modules.allowed("imagegen", event):
            return json.dumps(
                {
                    "status": "error",
                    "message": "当前会话的生图功能未启用。",
                    "error": {
                        "code": "feature_disabled",
                        "stage": "prepare",
                        "request_outcome": "not_started",
                    },
                },
                ensure_ascii=False,
            )
        return await self.modules.call(
            "imagegen", "generate_image", event, prompt, output_format
        )

    @safe_filter(filter.on_llm_request(), LEGACY.values())
    async def imagegen_guide_image_requests(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        if not await self.modules.allowed("imagegen", event):
            hide_image_tool(req)
            return
        return await self.modules.call("imagegen", "guide_image_requests", event, req)

    @safe_filter(filter.on_agent_begin(), LEGACY.values())
    async def imagegen_ensure_image_tool_call(
        self, event: AstrMessageEvent, run_context
    ):
        if not await self.modules.allowed("imagegen", event):
            return
        return await self.modules.call(
            "imagegen", "ensure_image_tool_call", event, run_context
        )

    @safe_filter(filter.on_plugin_loaded(), LEGACY.values())
    async def render_report_plugin_loaded(self, metadata):
        if not await self.modules.allowed("render", None):
            return
        return await self.modules.call("render", "report_plugin_loaded", metadata)

    @safe_filter(filter.on_astrbot_loaded(), LEGACY.values())
    async def render_report_startup_ready(self):
        if not await self.modules.allowed("render", None):
            return
        return await self.modules.call("render", "report_startup_ready")

    @safe_filter(filter.command("t2i", alias={"图样式"}), LEGACY.values())
    @safe_filter(filter.permission_type(filter.PermissionType.ADMIN), LEGACY.values())
    async def render_t2i(self, event: AstrMessageEvent, command: GreedyStr):
        if not await self.modules.allowed("render", event):
            return
        async for result in self.modules.iterate("render", "t2i", event, command):
            yield result

    @safe_filter(filter.on_llm_response(priority=-10000), LEGACY.values())
    async def render_remember_response_model(
        self, event: AstrMessageEvent, response: LLMResponse
    ):
        if not await self.modules.allowed("render", event):
            return
        return await self.modules.call(
            "render", "remember_response_model", event, response
        )

    @safe_filter(
        filter.event_message_type(filter.EventMessageType.ALL, priority=10),
        LEGACY.values(),
    )
    async def image_source_on_message(self, event: AstrMessageEvent):
        if not await self.modules.allowed("image_source", event):
            return
        async for result in self.modules.iterate("image_source", "on_message", event):
            yield result

    @safe_filter(filter.permission_type(filter.PermissionType.ADMIN), LEGACY.values())
    @safe_filter(filter.command("搜图状态"), LEGACY.values())
    async def image_source_search_status(self, event: AstrMessageEvent):
        "查看搜图引擎、缓存与最近额度状态。"
        if not await self.modules.allowed("image_source", event):
            return
        async for result in self.modules.iterate(
            "image_source", "search_status", event
        ):
            yield result

    @safe_filter(filter.permission_type(filter.PermissionType.ADMIN), LEGACY.values())
    @safe_filter(filter.command("搜图清缓存"), LEGACY.values())
    async def image_source_clear_search_cache(self, event: AstrMessageEvent):
        "清除搜图结果缓存。"
        if not await self.modules.allowed("image_source", event):
            return
        async for result in self.modules.iterate(
            "image_source", "clear_search_cache", event
        ):
            yield result

    @safe_filter(filter.permission_type(filter.PermissionType.ADMIN), LEGACY.values())
    @safe_filter(filter.command("link帮助"), LEGACY.values())
    async def image_source_search_help(self, event: AstrMessageEvent):
        "显示搜图-qing插件帮助。"
        if not await self.modules.allowed("image_source", event):
            return
        async for result in self.modules.iterate("image_source", "search_help", event):
            yield result
