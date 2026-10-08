"""Command and LLM-tool entry points for image generation."""

import asyncio
import copy
import json

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register

from .image_client import ImageGenError, ImagesClient, ImageSettings
from .intent import explicit_image_prompt

TOOL_NAME = "astrbot_generate_image"
TOOL_HINT = (
    "当前用户明确要求生成、绘制或制作图片。先按照用户要求、相关对话和已知资料，"
    "整理一份给生图模型的完整 prompt，再将它作为 prompt 参数调用 astrbot_generate_image。"
    "工具负责实际生图并直接将图片发送到当前会话。"
    "保持当前人格的语言风格。涉及群友、熟人或过去的事实时，先参考已注入的长期记忆和对话；"
    "若相关资料不足且有可用的记忆检索工具，先检索，再整理生图描述。"
    "仅讨论生图方法或询问能力时无需调用。不要只回复图片描述来代替执行生图。"
    "根据工具返回的 status 和 sent 判断结果；失败时如实说明，不要声称已生成或自动反复重试。"
)


@register("astrbot_plugin_imagegen", "qingyi", "GPT Image 指令生图与模型工具调用", "1.2.0")
class ImageGenPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.client = ImagesClient()
        concurrency = config.get("max_concurrent", 2)
        if type(concurrency) is not int or not 1 <= concurrency <= 10:
            raise ValueError("max_concurrent 必须是 1–10 之间的整数。")
        self._slots = asyncio.Semaphore(concurrency)
        self._busy_sessions: set[str] = set()

    @filter.command("i", alias={"imagegen", "生图"})
    async def image_command(self, event: AstrMessageEvent):
        """生成图片：/i 图片描述（也支持 /imagegen、/生图）。"""
        event.should_call_llm(False)
        # Read all remaining text: a normal str command parameter only receives
        # one whitespace-delimited word on some AstrBot versions.
        parts = event.get_message_str().strip().split(maxsplit=1)
        prompt = parts[1] if len(parts) > 1 else ""
        try:
            await self._generate_and_send(event, prompt)
        finally:
            event.stop_event()

    @filter.llm_tool(name=TOOL_NAME)
    async def generate_image(self, event: AstrMessageEvent, prompt: str) -> str:
        """仅当当前用户明确要求生成图片、画图、绘制插画或制作图片时调用。

        将用户要求及对话中的相关细节整理成完整的图片描述；保留用户指定的风格和文字。
        工具会调用 GPT Image 并将真实图片直接发送到当前会话，无需另行调用发送工具。
        返回 JSON 包含 status、generated、sent、message；只有 sent 大于零才表示已发送图片。
        失败时如实说明原因，不要自动重复调用；不要输出虚构图片链接或声称成功。

        Args:
            prompt(string): 完整的生图描述，包含主体、场景、风格、构图及需要绘制的文字。
        """
        # A repeated tool call in the same event must not bill the same prompt twice.
        cache = event.get_extra("imagegen_results")
        if cache is None:
            cache = {}
            event.set_extra("imagegen_results", cache)
        key = prompt.strip() if isinstance(prompt, str) else ""
        if key in cache:
            return cache[key]
        result = await self._generate_and_send(event, prompt)
        serialized = json.dumps(result, ensure_ascii=False)
        cache[key] = serialized
        return serialized

    @filter.on_llm_request()
    async def guide_image_requests(self, event: AstrMessageEvent, req: ProviderRequest):
        # Tool registration normally exposes a plugin's tool to every LLM turn.
        # Keep unrelated turns equivalent to the pre-install tool set. Clone only
        # this request's set; never mutate shared persona/registry collections.
        image_prompt = explicit_image_prompt(event.get_message_str())
        if not image_prompt:
            tools = getattr(req.func_tool, "tools", None)
            if isinstance(tools, list) and any(
                getattr(tool, "name", None) == TOOL_NAME for tool in tools
            ):
                scoped = copy.copy(req.func_tool)
                scoped.tools = [tool for tool in tools if getattr(tool, "name", None) != TOOL_NAME]
                req.func_tool = scoped
            return
        # Respect persona tool allowlists and globally disabled tools.
        if req.func_tool is not None:
            get_tool = getattr(req.func_tool, "get_tool", None) or getattr(
                req.func_tool, "get_func", None
            )
            tool = get_tool(TOOL_NAME) if callable(get_tool) else None
            available = tool is not None and getattr(tool, "active", True)
            if (
                available
                and image_prompt
                and self.config.get("inject_tool_hint", True)
                and TOOL_HINT not in (req.system_prompt or "")
            ):
                req.system_prompt = (req.system_prompt or "") + "\n" + TOOL_HINT

    async def _generate_and_send(self, event: AstrMessageEvent, prompt: str) -> dict:
        session = event.unified_msg_origin
        result = {"status": "error", "generated": 0, "sent": 0, "message": ""}
        if session in self._busy_sessions or self._slots.locked():
            result["message"] = "当前有生图任务正在执行，请等图片返回后再试。"
            await self._notify(event, result["message"])
            return result
        self._busy_sessions.add(session)
        try:
            async with self._slots:
                if not isinstance(prompt, str) or not prompt.strip():
                    raise ImageGenError("请提供图片描述，例如：/i 一只趴在窗边的橘猫。")
                settings = ImageSettings.from_config(self.config)
                if self.config.get("show_progress", True):
                    await self._notify(event, "正在生成图片，请稍候……")
                images = await self.client.generate(prompt, settings)
                result["generated"] = len(images)
                for data in images:
                    await event.send(event.chain_result([Image.fromBytes(data)]))
                    result["sent"] += 1
                result.update(
                    status="success", message=f"已向当前会话发送 {result['sent']} 张图片。"
                )
        except ImageGenError as exc:
            result["message"] = str(exc)
            logger.warning("[imagegen] %s", result["message"])
            await self._notify(event, "生图失败：" + result["message"])
        except Exception as exc:
            # Adapter exceptions can include credentials or base64. Log only type.
            logger.error("[imagegen] request/send failed: %s", type(exc).__name__)
            result["status"] = "partial" if result["sent"] else "error"
            result["message"] = (
                f"生成了 {result['generated']} 张图片，已发送 {result['sent']} 张；"
                "执行或发送失败，请检查 AstrBot 平台连接和日志。"
            )
            await self._notify(event, result["message"])
        finally:
            self._busy_sessions.discard(session)
        return result

    @staticmethod
    async def _notify(event: AstrMessageEvent, text: str):
        try:
            await event.send(event.plain_result(text))
        except Exception:
            logger.error("[imagegen] could not send text notification")
