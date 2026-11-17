from ...runtime import FeatureService

"""Command and LLM-tool entry points for image generation."""

import asyncio
import copy
import json
import time

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context

from .call_guard import image_tool, install_runner_guard
from .delivery_receipt import (
    confirm_group_delivery,
    image_fingerprint,
    receipt_is_ambiguous,
)
from .execution_budget import (
    IMAGE_SEND_TIMEOUT,
    NOTICE_TIMEOUT,
    install_execution_budget,
)
from .image_client import ImageGenError, ImagesClient, ImageSettings
from .intent import explicit_image_prompt

TOOL_NAME = "astrbot_generate_image"
TOOL_HINT = (
    "当前用户明确要求生成、绘制或制作图片。先按照用户要求、相关对话和已知资料，"
    "整理一份给生图模型的完整 prompt，再将它作为 prompt 参数调用 astrbot_generate_image。"
    "工具负责实际生图并直接将图片发送到当前会话。"
    "用户明确要求 GIF 动图时，将 output_format 设为 gif，并在 prompt 中保留动作和循环要求；"
    "普通图片不指定 output_format，沿用插件配置。不要将静态图声称为已生成的动图。"
    "保持当前人格的语言风格。涉及群友、熟人或过去的事实时，先参考已注入的长期记忆和对话；"
    "若相关资料不足且有可用的记忆检索工具，先检索，再整理生图描述。"
    "仅讨论生图方法或询问能力时无需调用。不要只回复图片描述来代替执行生图。"
    "根据工具返回的 status 和 sent 判断结果。status 为 error、partial 或 unconfirmed 时，"
    "读取 message 和 error，用当前人格明确告诉用户问题阶段、原因及确认送达数量；"
    "delivery_outcome 为 unknown 时，图片已生成但发送回执未确认，可能已在会话中显示；"
    "不要说发送了零张或确定发送失败。request_outcome 为 unknown 时说明生成执行状态不明。"
    "不要声称成功或自动反复重试。"
)

FAILURE_REPLY_HINT = (
    "请继续本轮对话，用当前人格向用户解释这份工具结果中的失败阶段和原因。"
    "依据 generated/sent 区分生成失败与发送问题；sent 只代表确认送达数，不是实际送达总数。"
    "delivery_outcome 为 unknown 时明确说明图片已生成，发送状态未确认，可能已显示，"
    "不要把 sent=0 说成实际发送零张或确定失败；请用户先看会话，勿自动重发或重新生图。"
    "只在 error.code 为 content_rejected 时说明服务明确拒绝内容，其他错误不要臆测审核原因。"
    "request_outcome 为 unknown 时说明是否已生成或计费无法确认。"
    "不要只说会继续画、不要假称成功、不要自动再次调用生图工具。"
)


class ImageGenPlugin(FeatureService):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.client = ImagesClient()
        concurrency = config.get("max_concurrent", 2)
        if type(concurrency) is not int or not 1 <= concurrency <= 10:
            raise ValueError("max_concurrent 必须是 1–10 之间的整数。")
        self._slots = asyncio.Semaphore(concurrency)
        self._busy_sessions: set[str] = set()

    async def image_command(self, event: AstrMessageEvent):
        """生成图片：/i 图片描述（也支持 /imagegen、/生图）。"""
        event.should_call_llm(False)
        # Read all remaining text: a normal str command parameter only receives
        # one whitespace-delimited word on some AstrBot versions.
        parts = event.get_message_str().strip().split(maxsplit=1)
        prompt = parts[1] if len(parts) > 1 else ""
        output_format = ""
        if prompt.split(maxsplit=1)[:1] == ["--gif"]:
            prompt = prompt[5:].lstrip()
            output_format = "gif"
        try:
            await self._generate_and_send(event, prompt, output_format=output_format)
        finally:
            event.stop_event()

    async def generate_image(
        self, event: AstrMessageEvent, prompt: str, output_format: str = ""
    ) -> str:
        """仅当当前用户明确要求生成图片、画图、绘制插画或制作图片时调用。

        将用户要求及对话中的相关细节整理成完整的图片描述；保留用户指定的风格和文字。
        工具会调用 GPT Image 并将真实图片直接发送到当前会话，无需另行调用发送工具。
        返回 JSON 包含 status、generated、sent、message、formats；formats 是实际返回格式。
        sent 只表示确认送达数；实际格式不是 gif 时，不得声称已生成 GIF 动图。
        失败或部分发送时，error 提供原因类别、失败阶段、HTTP 状态和服务端执行状态。
        插件不会另发失败通知，你必须用当前人格向用户解释 message/error，不能沉默或只说会画。
        delivery_outcome=unknown 表示图片已生成但发送状态未确认，可能已显示，不能说发送零张。
        仅 request_outcome=unknown 表示生成/计费无法确认。不要自动重复调用或虚构成功。
        明确要求 GIF 动图时指定 output_format=gif；未要求格式时留空，沿用插件配置。

        Args:
            prompt(string): 完整的生图描述，包含主体、场景、风格、构图及需要绘制的文字。
            output_format(string): 可选；留空沿用配置，或指定 png、jpeg、webp、gif。GIF 需服务支持。
        """
        # A repeated tool call in the same event must not bill the same prompt twice.
        cache = event.get_extra("imagegen_results")
        if cache is None:
            cache = {}
            event.set_extra("imagegen_results", cache)
        key = prompt.strip() if isinstance(prompt, str) else ""
        if key in cache:
            return cache[key]
        # A rejected/ambiguous/partially-sent request is not retried with a new
        # prompt in the same event. A fresh user message can explicitly retry.
        for previous in cache.values():
            if json.loads(previous).get("status") in {
                "error",
                "partial",
                "unconfirmed",
            }:
                return previous
        event.set_extra("imagegen_attempted", True)
        result = await self._generate_and_send(
            event, prompt, notify_errors=False, output_format=output_format
        )
        serialized = json.dumps(result, ensure_ascii=False)
        cache[key] = serialized
        return serialized

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
                scoped.tools = [
                    tool for tool in tools if getattr(tool, "name", None) != TOOL_NAME
                ]
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

    async def ensure_image_tool_call(self, event: AstrMessageEvent, run_context):
        if not explicit_image_prompt(event.get_message_str()):
            return
        # AstrBot's main local runner is registered before this event hook. Resolve
        # it by both session and context identity; never wrap a nested/other turn.
        try:
            from astrbot.core.pipeline.process_stage.follow_up import (
                _ACTIVE_AGENT_RUNNERS,
            )
        except ImportError:
            logger.warning(
                "[imagegen] current AstrBot does not expose the local runner registry"
            )
            return
        runner = _ACTIVE_AGENT_RUNNERS.get(event.unified_msg_origin)
        if runner is None or runner.run_context is not run_context:
            return
        if not image_tool(
            getattr(getattr(runner, "req", None), "func_tool", None), TOOL_NAME
        ):
            return
        try:
            settings = ImageSettings.from_config(self.config)
        except ImageGenError:
            # The tool itself returns the configuration error without an API call.
            return
        if install_execution_budget(runner, TOOL_NAME, settings.timeout, settings.n):
            event.set_extra("imagegen_execution_budget", runner.tool_executor.seconds)
            logger.info(
                "[imagegen] image-only tool execution budget: %ss",
                runner.tool_executor.seconds,
            )
        else:
            logger.warning(
                "[imagegen] native executor incompatible; image budget not installed"
            )
        if not self.config.get("ensure_tool_call", True):
            return
        timeout = self.config.get("tool_call_repair_timeout", 180)
        if type(timeout) is not int or not 1 <= timeout <= 180:
            logger.warning(
                "[imagegen] invalid repair timeout; call guard not installed"
            )
            return
        if install_runner_guard(runner, event, TOOL_NAME, timeout):
            event.set_extra("imagegen_call_guard_active", True)
        elif getattr(runner, "_imagegen_call_guard", None) is None:
            logger.warning(
                "[imagegen] local runner/tool capability incompatible; call guard skipped"
            )

    async def _generate_and_send(
        self,
        event: AstrMessageEvent,
        prompt: str,
        *,
        notify_errors: bool = True,
        output_format: str = "",
    ) -> dict:
        session = event.unified_msg_origin
        result = {"status": "error", "generated": 0, "sent": 0, "message": ""}
        if session in self._busy_sessions or self._slots.locked():
            self._record_error(
                result,
                ImageGenError(
                    "当前有生图任务正在执行，请等图片返回后再试。",
                    code="busy",
                    stage="prepare",
                    request_outcome="not_started",
                ),
            )
            if notify_errors:
                await self._notify(event, result["message"])
            return result
        self._busy_sessions.add(session)
        stage = "prepare"
        task_started = time.monotonic()
        try:
            async with self._slots:
                if not isinstance(prompt, str) or not prompt.strip():
                    raise ImageGenError(
                        "请提供图片描述，例如：/i 一只趴在窗边的橘猫。",
                        code="invalid_prompt",
                        stage="prepare",
                        request_outcome="not_started",
                    )
                # A per-request format must never change the shared plugin config.
                config = (
                    self.config
                    if output_format == ""
                    else {
                        **self.config,
                        "output_format": output_format,
                    }
                )
                settings = ImageSettings.from_config(config)
                if self.config.get("show_progress", True):
                    await self._notify(event, "正在生成图片，请稍候……")
                stage = "request"
                images = await self.client.generate(prompt, settings)
                result["generated"] = len(images)
                result["formats"] = [
                    "gif"
                    if data.startswith((b"GIF87a", b"GIF89a"))
                    else "png"
                    if data.startswith(b"\x89PNG\r\n\x1a\n")
                    else "jpeg"
                    if data.startswith(b"\xff\xd8\xff")
                    else "webp"
                    for data in images
                ]
                logger.info(
                    "[imagegen] generated %s image(s), %s bytes, elapsed %.1fs",
                    len(images),
                    sum(len(data) for data in images),
                    time.monotonic() - task_started,
                )
                stage = "send"
                used_receipts: set[str] = set()
                attempted_hashes: set[str] = set()
                for index, data in enumerate(images):
                    fingerprint = image_fingerprint(data)
                    repeated_image = fingerprint in attempted_hashes
                    attempted_hashes.add(fingerprint)
                    started = time.time()
                    try:
                        await asyncio.wait_for(
                            event.send(event.chain_result([Image.fromBytes(data)])),
                            timeout=IMAGE_SEND_TIMEOUT,
                        )
                    except Exception as exc:
                        if not receipt_is_ambiguous(exc):
                            raise
                        # Without a baseline sequence, an identical earlier image
                        # in this batch could falsely confirm a second send.
                        confirmed = not repeated_image and await confirm_group_delivery(
                            event, data, started, used_receipts
                        )
                        if not confirmed:
                            self._record_unconfirmed(result, len(images) - index - 1)
                            logger.warning(
                                "[imagegen] image delivery unconfirmed; no automatic retry"
                            )
                            if notify_errors:
                                await self._notify(event, result["message"])
                            return result
                        result["history_confirmed"] = (
                            result.get("history_confirmed", 0) + 1
                        )
                        logger.info(
                            "[imagegen] image delivery confirmed by group history"
                        )
                    result["sent"] += 1
                result.update(
                    status="success",
                    delivery_outcome="confirmed",
                    message=f"已向当前会话发送 {result['sent']} 张图片。",
                )
                logger.info(
                    "[imagegen] confirmed delivery: %s/%s, elapsed %.1fs",
                    result["sent"],
                    result["generated"],
                    time.monotonic() - task_started,
                )
        except asyncio.CancelledError:
            logger.warning(
                "[imagegen] task cancelled at %s; generated=%s confirmed=%s; no automatic retry",
                stage,
                result["generated"],
                result["sent"],
            )
            raise
        except ImageGenError as exc:
            self._record_error(result, exc)
            logger.warning(
                "[imagegen] %s/%s: %s", exc.stage, exc.code, result["message"]
            )
            if notify_errors:
                await self._notify(event, "生图失败：" + result["message"])
        except Exception as exc:
            # Adapter exceptions can include credentials or base64. Log only type.
            logger.error("[imagegen] request/send failed: %s", type(exc).__name__)
            sending = stage == "send"
            message = (
                f"图片已生成 {result['generated']} 张，已发送 {result['sent']} 张，"
                "但会话平台发送失败；请检查平台连接，不要重新生图。"
                if sending
                else "插件执行发生异常，无法确认服务端是否已完成生成；请检查日志，勿自动重试。"
            )
            self._record_error(
                result,
                ImageGenError(
                    message,
                    code="image_send_failed" if sending else "execution_failed",
                    stage=stage,
                    request_outcome="generated" if sending else "unknown",
                ),
            )
            if notify_errors:
                await self._notify(event, result["message"])
        finally:
            self._busy_sessions.discard(session)
        return result

    @staticmethod
    def _record_unconfirmed(result: dict, not_attempted: int):
        message = f"图片已生成 {result['generated']} 张，但发送回执超时，发送状态未确认，图片可能已显示。"
        if result["sent"]:
            message += f"其中已有 {result['sent']} 张确认送达。"
        if not_attempted:
            message += f"另有 {not_attempted} 张尚未尝试发送。"
        message += "请先查看会话；不会自动重发或重新生图。"
        result.update(
            status="partial" if result["sent"] else "unconfirmed",
            delivery_outcome="unknown",
            unconfirmed=1,
            not_attempted=not_attempted,
            message=message,
            error={
                "code": "image_send_unconfirmed",
                "stage": "send",
                "http_status": None,
                "request_outcome": "generated",
                "delivery_outcome": "unknown",
                "automatic_retry": False,
            },
            reply_instruction=FAILURE_REPLY_HINT,
        )

    @staticmethod
    def _record_error(result: dict, exc: ImageGenError):
        result.update(
            status="partial" if result["sent"] else "error",
            message=str(exc),
            error=exc.details(),
            reply_instruction=FAILURE_REPLY_HINT,
        )

    @staticmethod
    async def _notify(event: AstrMessageEvent, text: str):
        try:
            await asyncio.wait_for(
                event.send(event.plain_result(text)), timeout=NOTICE_TIMEOUT
            )
        except Exception:
            logger.error("[imagegen] could not send text notification")
