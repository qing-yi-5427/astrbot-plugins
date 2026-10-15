"""Allow explicit QQ bot mentions through the native LLM prefix gate."""

import importlib
import inspect
from dataclasses import replace
from functools import wraps

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At
from astrbot.api.star import Context, Star, register

NAME = "astrbot_plugin_mention_llm"
MENTION_REQUEST = "mention_llm_native_request"
COLLECTOR_MODULE = (
    "astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal"
)


@register(NAME, "qingyi", "仅为明确 @ 机器人的 QQ 群消息放行原生 LLM 前缀检查", "1.0.0")
class MentionLLMPlugin(Star):
    """Keep the existing command and conversation pipeline unchanged."""

    def __init__(self, context: Context):
        super().__init__(context)
        self._running = False
        self._module = None
        self._original = None
        self._wrapper = None

    async def initialize(self):
        """Install one reversible wrapper at the local agent's collection point."""
        if self._running:
            return
        module = importlib.import_module(COLLECTOR_MODULE)
        original = module.collect_initial_request
        signature = inspect.signature(original)
        if not inspect.iscoroutinefunction(original) or tuple(signature.parameters) != (
            "event",
            "plugin_context",
            "config",
            "req",
        ):
            raise RuntimeError(
                "Unsupported AstrBot request collector; mention bridge not installed."
            )

        @wraps(original)
        async def collect_for_mention(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            event = bound.arguments["event"]
            config = bound.arguments["config"]
            if (
                self._running
                and event.get_extra(MENTION_REQUEST) is True
                and bound.arguments.get("req") is None
                and event.get_extra("provider_request") is None
                and config.provider_wake_prefix
                and not event.message_str.startswith(config.provider_wake_prefix)
            ):
                # Copy only this call's build config. The original collector still
                # runs inside AstrBot's session lock and handles all attachments.
                bound.arguments["config"] = replace(config, provider_wake_prefix="")
                return await original(*bound.args, **bound.kwargs)
            # Preserve argument shape, object identity and behavior for /aa and
            # every other request, including prebuilt plugin requests.
            return await original(*args, **kwargs)

        self._module, self._original, self._wrapper = (
            module,
            original,
            collect_for_mention,
        )
        self._running = True
        module.collect_initial_request = collect_for_mention
        logger.info(
            "[mention_llm] Explicit group mention bridge installed; command config unchanged"
        )

    @filter.on_waiting_llm_request()
    async def mark_explicit_mention(self, event: AstrMessageEvent):
        """Mark only mentions that already passed AstrBot's LLM entry checks.

        Args:
            event: Current QQ group event after command/session permission checks.
        """
        if (
            not self._running
            or event.get_platform_name() != "aiocqhttp"
            or not event.get_group_id()
            or not event.is_at_or_wake_command
            or event.is_stopped()
            or event.get_extra("provider_request") is not None
        ):
            return
        self_id = str(event.get_self_id())
        if not self_id or str(event.get_sender_id()) == self_id:
            return
        enabled_plugins = getattr(event, "plugins_name", None)
        if enabled_plugins is not None and NAME not in enabled_plugins:
            return
        # Inspect only top-level structured mentions, never quoted messages,
        # displayed nicknames, @all, or text that merely contains an @ sign.
        if not any(
            isinstance(part, At) and str(part.qq) == self_id
            for part in event.get_messages()
        ):
            return
        # Non-message hooks are not session-filtered in AstrBot 4.28.2.
        from astrbot.core.star.session_plugin_manager import SessionPluginManager

        if await SessionPluginManager.is_plugin_enabled_for_session(
            event.unified_msg_origin, NAME
        ):
            event.set_extra(MENTION_REQUEST, True)

    async def terminate(self):
        """Restore our alias without removing another plugin's later wrapper."""
        self._running = False
        if (
            self._module is not None
            and self._module.collect_initial_request is self._wrapper
        ):
            self._module.collect_initial_request = self._original
        logger.info("[mention_llm] Mention bridge disabled")
