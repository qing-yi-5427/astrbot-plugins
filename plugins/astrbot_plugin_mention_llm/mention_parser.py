"""Bound optional nickname lookups without replacing the native QQ parser."""

import asyncio
import importlib
import inspect
from copy import copy
from functools import wraps

from astrbot.api import logger

ADAPTER_MODULE = "astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter"
NICKNAME_BUDGET_SECONDS = 1.0


class _NicknameBot:
    """Delegate OneBot operations with a per-message nickname lookup budget."""

    def __init__(self, original, event, mentioned_ids):
        self.original = original
        self.self_id = str(event.self_id)
        self.group_id = str(event.group_id)
        self.mentioned_ids = mentioned_ids
        self.deadline = None
        self.cancelled = False

    def __getattr__(self, name):
        return getattr(self.original, name)

    async def call_action(self, action, **params):
        """Limit only profile queries made by this message's native @ parser.

        Args:
            action: Original OneBot action name.
            **params: Unmodified OneBot routing and action parameters.

        Returns:
            Original API data, or a minimal ID-based name on lookup failure.
        """
        user_id = str(params.get("user_id", ""))
        if (
            action not in {"get_group_member_info", "get_stranger_info"}
            or user_id not in self.mentioned_ids
            or str(params.get("self_id", self.self_id)) != self.self_id
            or (
                action == "get_group_member_info"
                and str(params.get("group_id")) != self.group_id
            )
        ):
            return await self.original.call_action(action, **params)

        # A nonempty card prevents the native parser's second stranger lookup.
        # Keep the numeric identity, not an invented nickname or a shared cache.
        fallback = {"user_id": int(user_id), "card": user_id, "nickname": user_id}
        if user_id == self.self_id:
            return fallback

        loop = asyncio.get_running_loop()
        if self.deadline is None:
            self.deadline = loop.time() + NICKNAME_BUDGET_SECONDS
        remaining = self.deadline - loop.time()
        if remaining <= 0:
            return fallback
        try:
            data = await asyncio.wait_for(
                self.original.call_action(action, **params), timeout=remaining
            )
            return data if isinstance(data, dict) and data else fallback
        except asyncio.CancelledError:
            # AstrBot 4.28.2 catches BaseException in its parser. Re-raise again
            # after it returns so a cancelled message cannot proceed to the LLM.
            self.cancelled = True
            raise
        except Exception as exc:  # noqa: BLE001 - Optional metadata must not block waking.
            logger.warning(
                "[mention_llm] Optional nickname lookup failed (%s); using QQ ID",
                type(exc).__name__,
            )
            return fallback


class MentionParserPatch:
    """Install and remove a narrowly scoped native-parser runtime wrapper."""

    def __init__(self, context, plugin_name):
        self.context = context
        self.plugin_name = plugin_name
        self.active = False
        self.adapter_class = self.original = self.wrapper = None

    def install(self):
        """Validate the installed parser before changing its runtime reference.

        Raises:
            RuntimeError: The installed AstrBot parser has an unsupported API.
        """
        module = importlib.import_module(ADAPTER_MODULE)
        adapter_class = module.AiocqhttpAdapter
        original = adapter_class._convert_handle_message_event
        if not inspect.iscoroutinefunction(original) or tuple(
            inspect.signature(original).parameters
        ) != ("self", "event", "get_reply"):
            raise RuntimeError(
                "Unsupported AstrBot QQ parser; mention bridge not installed."
            )

        @wraps(original)
        async def parse(adapter, event, get_reply=True):
            # Recursive quote parsing must use the real bot, not the outer
            # message's name substitutions or budget. Never mutate the adapter.
            if isinstance(adapter.bot, _NicknameBot):
                adapter = copy(adapter)
                adapter.bot = adapter.bot.original
            if (
                not self.active
                or self.context is None
                or not get_reply
                or event.get("message_type") != "group"
                or not event.get("group_id")
                or not event.get("self_id")
                or not isinstance(event.get("message"), list)
                or str(event.get("user_id")) == str(event.self_id)
            ):
                return await original(adapter, event, get_reply=get_reply)
            mentioned_ids = {
                str(part.get("data", {}).get("qq", ""))
                for part in event.message
                if isinstance(part, dict) and part.get("type") == "at"
            }
            if str(event.self_id) not in mentioned_ids:
                return await original(adapter, event, get_reply=get_reply)

            # Match profile selection before native waking applies optional
            # per-user group sessions. These are local configuration reads.
            from astrbot.core.star.session_plugin_manager import SessionPluginManager

            umo = f"{adapter.metadata.id}:GroupMessage:{event.group_id}"
            config = self.context.get_config(umo=umo)
            plugins = config.get("plugin_set", ["*"])
            if plugins != ["*"] and self.plugin_name not in plugins:
                return await original(adapter, event, get_reply=get_reply)
            if config.get("platform_settings", {}).get("unique_session", False):
                umo = (
                    f"{adapter.metadata.id}:GroupMessage:"
                    f"{event.user_id}_{event.group_id}"
                )
            if not await SessionPluginManager.is_plugin_enabled_for_session(
                umo, self.plugin_name
            ):
                return await original(adapter, event, get_reply=get_reply)

            scoped = copy(adapter)
            scoped.bot = bot = _NicknameBot(adapter.bot, event, mentioned_ids)
            result = await original(scoped, event, get_reply=get_reply)
            task = asyncio.current_task()
            if bot.cancelled or (task is not None and task.cancelling()):
                raise asyncio.CancelledError
            logger.debug("[mention_llm] Parsed bot mention without self profile lookup")
            return result

        self.adapter_class, self.original, self.wrapper = adapter_class, original, parse
        self.active = True
        adapter_class._convert_handle_message_event = parse

    def uninstall(self):
        """Restore only our own reference, preserving any later plugin wrapper."""
        self.active = False
        if (
            self.adapter_class is not None
            and self.adapter_class._convert_handle_message_event is self.wrapper
        ):
            self.adapter_class._convert_handle_message_event = self.original
