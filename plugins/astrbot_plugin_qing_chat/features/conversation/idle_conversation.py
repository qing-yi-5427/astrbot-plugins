"""Rotate idle QQ conversations without deleting history or long-term memory."""

import asyncio
import math
import time
from functools import partial

from .tool_history import bounded_int

STORE_KEY = "idle_group_state_v1"
RESET_EXTRA = "tool_history_idle_rotation"


def valid_timestamp(value, default):
    """Validate persisted timestamps shared by baseline, sends and rotations."""
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    ):
        return value
    return default


class IdleConversation:
    def __init__(self, owner):
        self.owner = owner
        self.config = owner.config
        self.logger = owner.logger
        self.active = False
        self.state = {}
        self.lock = asyncio.Lock()
        self.patches = []

    async def initialize(self):
        """Load private durable state; start conservatively when no sends are known."""
        if self.active or getattr(self.owner, "context", None) is None:
            return
        if not self.config.get("enable_idle_new_conversation", True):
            return
        try:
            state = await self.owner.get_kv_data(STORE_KEY, None)
            if not (
                isinstance(state, dict)
                and state.get("version") == 1
                and isinstance(state.get("last_sent"), dict)
                and isinstance(state.get("last_reset"), dict)
                and valid_timestamp(state.get("tracking_started_at"), None) is not None
            ):
                state = {
                    "version": 1,
                    "tracking_started_at": time.time(),
                    "last_sent": {},
                    "last_reset": {},
                }
                await self.owner.put_kv_data(STORE_KEY, state)
            self.state = state
        except Exception as exc:  # noqa: BLE001 - Disable only this optional feature.
            self.logger.warning(
                "Idle rotation disabled: state unavailable (%s)", type(exc).__name__
            )
            return
        self.active = True
        self.bind_clients()
        self.logger.info(
            "Idle conversation tracking active: %d OneBot clients; threshold=%dh",
            len(self.patches),
            bounded_int(self.config, "idle_new_conversation_hours", 8, 1, 168),
        )

    def bind_clients(self):
        """Observe successful OneBot sends, including tool and proactive messages."""
        if not self.active:
            return
        for platform in self.owner.context.platform_manager.platform_insts:
            meta = platform.meta()
            if meta.name != "aiocqhttp":
                continue
            bot = platform.bot
            if any(client is bot for client, _, _ in self.patches):
                continue
            original = bot.call_action
            wrapped = partial(self.observe_send, original, str(meta.id))
            bot.call_action = wrapped
            self.patches.append((bot, original, wrapped))

    async def observe_send(self, original, platform_id, action, **params):
        """Record only confirmed message IDs; never alter the original send result.

        Args:
            original: This client's unmodified OneBot action method.
            platform_id: AstrBot adapter instance ID.
            action: Original OneBot API action name.
            **params: Unchanged original action parameters.

        Returns:
            The original action's response, preserving exceptions on failed sends.
        """
        result = await original(action, **params)
        if (
            self.active
            and self.config.get("enable_idle_new_conversation", True)
            and action in {"send_msg", "send_group_msg", "send_group_forward_msg"}
            and params.get("group_id")
            and params.get("message_type", "group") == "group"
            and isinstance(result, dict)
            and result.get("message_id") is not None
            and result.get("retcode", 0) == 0
            and result.get("status") != "failed"
        ):
            async with self.lock:
                key = f"{platform_id}:{params['group_id']}"
                self.state["last_sent"][key] = time.time()
                try:
                    await self.owner.put_kv_data(STORE_KEY, self.state)
                except Exception as exc:  # noqa: BLE001 - Preserve successful sends.
                    # Persistence must never turn a delivered message into a failure.
                    self.logger.warning(
                        "Idle send timestamp not persisted (%s)", type(exc).__name__
                    )
        return result

    async def before_request(self, event, req):
        """Rotate inside the native session lock before history enters the Agent.

        Args:
            event: Newly awakened QQ group message; queue time is already over.
            req: Mutable request used by the pending native runner reset coroutine.
        """
        if (
            not self.active
            or not self.config.get("enable_idle_new_conversation", True)
            or event.get_platform_name() != "aiocqhttp"
            or not event.get_group_id()
            or event.get_extra(RESET_EXTRA)
            or req.conversation is None
            or req.tool_calls_result
        ):
            return
        from astrbot.builtin_stars.astrbot.group_chat_context import (
            GROUP_HISTORY_FOOTER,
            GROUP_HISTORY_HEADER,
        )
        from astrbot.core.provider.entities import ProviderRequest
        from astrbot.core.star.session_plugin_manager import SessionPluginManager

        umo = event.unified_msg_origin
        cfg = self.owner.context.get_config(umo=umo)
        if cfg.get("agent_runner", {}).get("runner_type", "local") != "local":
            return
        if not await SessionPluginManager.is_plugin_enabled_for_session(
            umo, "tavily_result_limiter"
        ):
            return
        async with self.lock:
            now = time.time()
            baseline = valid_timestamp(self.state["tracking_started_at"], now)
            key = f"{event.get_platform_id()}:{event.get_group_id()}"
            anchor = max(
                baseline,
                valid_timestamp(self.state["last_sent"].get(key), baseline),
                valid_timestamp(self.state["last_reset"].get(umo), baseline),
            )
            hours = bounded_int(self.config, "idle_new_conversation_hours", 8, 1, 168)
            if now - anchor < hours * 3600:
                return
            manager = self.owner.context.conversation_manager
            previous = req.conversation
            if await manager.get_curr_conversation_id(umo) != previous.cid:
                self.logger.warning(
                    "Idle rotation skipped: request conversation is stale"
                )
                return
            # A manually created, still-empty conversation already meets the goal.
            if previous.history in (None, "", "[]") and not req.contexts:
                self.state["last_reset"][umo] = now
                await self.owner.put_kv_data(STORE_KEY, self.state)
                return
            new_cid = await manager.new_conversation(
                umo, event.get_platform_id(), persona_id=previous.persona_id
            )
            try:
                fresh = await manager.get_conversation(umo, new_cid)
                if fresh is None:
                    raise RuntimeError("Fresh conversation unavailable")
            except Exception:
                await manager.switch_conversation(umo, previous.cid)
                raise
            req.conversation = fresh
            req.contexts = []
            if req.session_id == previous.cid:
                req.session_id = new_cid

            # Consume only the group backlog through this event. The native helper
            # preserves later queued events; no record or memory database is erased.
            builtin = self.owner.context.get_registered_star("astrbot")
            group_context = getattr(
                getattr(builtin, "star_cls", None), "group_chat_context", None
            )
            if group_context is not None:
                await group_context.on_req_llm(event, ProviderRequest())
            event.set_extra("_group_context_record_id", None)
            event.set_extra("_group_context_raw_idx", -1)
            kept_parts = []
            for part in req.extra_user_content_parts:
                text = (
                    part.get("text", "")
                    if isinstance(part, dict)
                    else getattr(part, "text", "")
                )
                if (
                    isinstance(text, str)
                    and text.startswith(GROUP_HISTORY_HEADER)
                    and text.endswith(GROUP_HISTORY_FOOTER)
                ):
                    continue
                kept_parts.append(part)
            req.extra_user_content_parts = kept_parts
            event.set_extra(
                RESET_EXTRA, {"previous_cid": previous.cid, "new_cid": new_cid}
            )
            self.state["last_reset"][umo] = now
            try:
                await self.owner.put_kv_data(STORE_KEY, self.state)
            except Exception as exc:  # noqa: BLE001 - Rotation already completed.
                self.logger.warning(
                    "Idle rotation timestamp not persisted (%s)", type(exc).__name__
                )
            self.logger.info(
                "Idle group conversation rotated after %.2fh; previous history retained; session=%s",
                (now - anchor) / 3600,
                umo,
            )

    async def close(self):
        """Restore only API observers owned by this plugin on reload or disable."""
        self.active = False
        for bot, original, wrapped in reversed(self.patches):
            if bot.call_action is wrapped:
                bot.call_action = original
        self.patches.clear()
