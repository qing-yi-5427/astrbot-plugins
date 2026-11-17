"""Bounded, request-local repair of missing image tool calls.

The hook wraps one active runner, not a shared provider or an AstrBot class.
The corrective model response re-enters the normal tool execution/history path.
"""

import asyncio
import copy
import re
from typing import Any

from astrbot.api import logger
from astrbot.api.provider import LLMResponse

REPAIR_NOTICE = (
    "[生图执行校验] 当前用户明确要求实际生成图片，但上一份响应没有返回生图工具调用。"
    "本轮尚未执行生图，不能只承诺会画、给文字描述或声称已完成。"
    "请结合原始用户要求、人格、已有上下文和已检索的资料，整理完整的生图 prompt，"
    "调用一次 astrbot_generate_image。不能把用户原话不加整理地当作兜底 prompt。"
    "如果请求不允许或缺少必需信息，应明确拒绝或请求补充，不得绕过内容限制。"
    "如果后续用户已取消生图或改为仅讨论，遵从最新要求，不要生图。"
    "不要改变用户要求来规避审核；工具调用后的成功或失败由后续正常对话说明。"
)

_REFUSAL_OR_CLARIFICATION = re.compile(
    r"(?:不能|无法|不会|不允许|不支持|拒绝|不方便|不愿意).{0,16}"
    r"(?:画|绘|生成|制作|提供|处理|帮助|满足)|"
    r"(?:违反|触发|不符合).{0,12}(?:安全|审核|政策|限制|规定)|"
    r"(?:请|需要|先|能否).{0,8}(?:提供|补充|确认|告诉我|描述一下)|"
    r"(?:你想要|你希望|要什么).{0,18}(?:风格|尺寸|颜色|画|图)|"
    r"(?:取消|停止|不再|先不|不用|不继续).{0,8}(?:画|绘|生图|生成)|"
    r"不画了|不生成了|不生图了|"
    r"\b(?:cannot|can't|unable to|not allowed|won't|refuse to)\b|"
    r"\b(?:please provide|need more|could you clarify|which style|what style)\b",
    re.I,
)
_PROMISE = re.compile(
    r"(?:行|好的?|没问题|可以|这就|马上|我来|开始|已经|已).{0,24}"
    r"(?:画|绘|生成|制作|生图)|"
    r"(?:画|绘|生成).{0,8}(?:好了|完成|如下)|"
    r"\b(?:i['’]ll|i will|let me|generating|here is the image)\b",
    re.I,
)


def _field(value: Any, name: str, default: Any = None) -> Any:
    return (
        value.get(name, default)
        if isinstance(value, dict)
        else getattr(value, name, default)
    )


def image_tool(toolset: Any, name: str) -> Any:
    getter = getattr(toolset, "get_tool", None) or getattr(toolset, "get_func", None)
    tool = getter(name) if callable(getter) else None
    return tool if tool is not None and getattr(tool, "active", True) else None


def is_refusal_or_clarification(response: Any) -> bool:
    if getattr(response, "role", None) == "err":
        return True
    raw = getattr(response, "raw_completion", None)
    if _field(raw, "status") in {"failed", "incomplete"}:
        return True
    for choice in _field(raw, "choices", []) or []:
        if _field(_field(choice, "message"), "refusal"):
            return True
    for item in _field(raw, "output", []) or []:
        for part in _field(item, "content", []) or []:
            if _field(part, "type") == "refusal":
                return True
    text = (getattr(response, "completion_text", "") or "").strip()
    if _REFUSAL_OR_CLARIFICATION.search(text):
        return True
    return text.endswith(("?", "？")) and not _PROMISE.search(text)


class ImageCallGuard:
    def __init__(self, runner: Any, event: Any, name: str, timeout: int):
        self.runner = runner
        self.event = event
        self.name = name
        self.timeout = timeout
        self.repair_used = False
        self.run_context = runner.run_context
        self.request = runner.req

    def record(self, phase: str):
        # Only metadata, never reply text, prompts, credentials or raw responses.
        logger.info("[imagegen] call guard: %s", phase)
        trace = getattr(self.event, "trace", None)
        if trace is not None:
            try:
                trace.record(
                    "imagegen_call_guard", phase=phase, repair_used=self.repair_used
                )
            except Exception:
                logger.warning("[imagegen] could not record call guard trace")

    def attempted(self) -> bool:
        return bool(
            self.event.get_extra("imagegen_tool_issued")
            or self.event.get_extra("imagegen_attempted")
            or self.event.get_extra("imagegen_results")
        )

    def notice(self, reason: str, usage: Any = None) -> LLMResponse:
        return LLMResponse(
            role="assistant",
            completion_text=(
                "这次未实际执行生图工具，也没有收到图片。"
                + reason
                + "你可以重新明确描述，或使用 /i 图片描述。"
            ),
            usage=usage,
        )

    async def inspect(self, response: Any, *, include_model: bool) -> Any:
        calls = getattr(response, "tools_call_name", []) or []
        if self.name in calls:
            self.event.set_extra("imagegen_tool_issued", True)
        if calls or self.attempted() or is_refusal_or_clarification(response):
            return response
        if self.repair_used:
            self.record("still_missing")
            return self.notice(
                "模型仍未返回生图调用，已停止自动补执行。",
                getattr(response, "usage", None),
            )

        toolset = self.runner._func_tool_for_provider()
        tool = image_tool(toolset, self.name)
        if tool is None or not isinstance(getattr(toolset, "tools", None), list):
            return response  # Respect capability filtering and exhausted step limits.
        self.repair_used = (
            True  # Set before awaiting: at most one correction per event.
        )
        self.record("repair_requested")
        usage = getattr(response, "usage", None)
        if usage is not None:
            # Keep billing stats without inflating final context-window occupancy.
            self.runner.stats.token_usage += usage
        scoped_tools = copy.copy(toolset)
        scoped_tools.tools = [tool]
        contexts = list(
            self.runner._sanitize_contexts_for_provider(
                self.runner.run_context.messages
            )
        )
        contexts.append({"role": "system", "content": REPAIR_NOTICE})
        payload = {
            "contexts": contexts,
            "func_tool": scoped_tools,
            "session_id": self.runner.req.session_id,
            "extra_user_content_parts": self.runner.req.extra_user_content_parts,
            "abort_signal": self.runner._abort_signal,
            "request_max_retries": 1,
            "tool_choice": "required",
        }
        if include_model:
            payload["model"] = self.runner.req.model
        try:
            async with asyncio.timeout(self.timeout):
                pending = self.runner.provider.text_chat(**payload)
                wait_or_stop = getattr(self.runner, "_await_or_stop", None)
                repaired = (
                    await wait_or_stop(pending)
                    if callable(wait_or_stop)
                    else await pending
                )
        except TimeoutError:
            self.record("repair_timeout")
            return self.notice("补执行的对话模型请求超时，已停止自动补执行。")
        except Exception as exc:
            # Do not echo provider exceptions: they may contain secrets or history.
            logger.warning("[imagegen] call repair failed: %s", type(exc).__name__)
            self.record("repair_failed")
            return self.notice("补执行的对话模型请求失败，已停止自动补执行。")
        if repaired is None:
            self.record("repair_aborted")
            return (
                None  # Native runner finalizes its stop request without tool execution.
            )
        calls = getattr(repaired, "tools_call_name", []) or []
        if calls == [self.name]:
            self.event.set_extra("imagegen_tool_issued", True)
            self.record("repair_returned_tool")
            return repaired  # Original runner executes/saves the real tool call.
        if not calls and is_refusal_or_clarification(repaired):
            self.record("repair_declined_or_needs_details")
            return repaired
        self.record("repair_no_tool")
        return self.notice(
            "模型没有按要求返回生图调用，已停止自动补执行。",
            getattr(repaired, "usage", None),
        )


def install_runner_guard(runner: Any, event: Any, name: str, timeout: int) -> bool:
    existing = getattr(runner, "_imagegen_call_guard", None)
    if (
        existing is not None
        and existing.run_context is runner.run_context
        and existing.request is runner.req
    ):
        return False
    required = (
        "_iter_llm_responses",
        "_func_tool_for_provider",
        "_sanitize_contexts_for_provider",
    )
    if not all(callable(getattr(runner, attr, None)) for attr in required):
        return False
    if not image_tool(runner._func_tool_for_provider(), name):
        return False
    original = (
        existing.original_responses
        if existing is not None
        else runner._iter_llm_responses
    )
    guard = ImageCallGuard(runner, event, name, timeout)
    guard.original_responses = original

    async def guarded_responses(*, include_model: bool = True):
        async for response in original(include_model=include_model):
            if (
                runner.run_context is not guard.run_context
                or runner.req is not guard.request
                or getattr(response, "is_chunk", False)
            ):
                yield response
            else:
                inspected = await guard.inspect(response, include_model=include_model)
                if inspected is not None:
                    yield inspected

    # Buffer only this image-request runner, so an unexecuted promise cannot leak
    # through streaming before validation. Shared providers/config remain intact.
    runner.streaming = False
    runner._iter_llm_responses = guarded_responses
    runner._imagegen_call_guard = guard
    guard.record("installed")
    return True
