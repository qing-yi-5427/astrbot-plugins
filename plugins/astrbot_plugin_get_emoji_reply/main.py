"""Add a random QQ reaction to /aa or explicit bot mentions entering the LLM."""

import asyncio
import random
import re
from collections.abc import Mapping
from sys import maxsize

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star

DEFAULT_EMOJI_IDS = ("14", "21", "63", "66", "74", "76", "99", "124", "201", "428")
REACTION_TIMEOUT_SECONDS = 1.0

AA_COMMAND_PATTERN = re.compile(r"^/aa(?:\s|$)")


class Main(Star):
    """使用 LLOneBot 的消息表情回应接口自动贴 QQ 系统表情。"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.emoji_id = self._read_emoji_id()
        self.random_enabled = self.config.get("random_enabled", True) is True
        self.emoji_ids = self._read_emoji_ids()

    def _read_emoji_id(self) -> str:
        """读取并校验 LLOneBot 使用的 QQ 表情 ID。"""
        value = str(self.config.get("emoji_id", 124)).strip()
        if value.isascii() and value.isdecimal():
            return value
        logger.warning("emoji_id=%r 无效，回退到 GET/OK 表情 124", value)
        return "124"

    def _read_emoji_ids(self) -> tuple[str, ...]:
        """Normalize the configurable pool without duplicate weighting."""
        values = self.config.get("emoji_ids", list(DEFAULT_EMOJI_IDS))
        if not isinstance(values, (list, tuple)):
            logger.warning("emoji_ids 必须为列表，回退到固定表情")
            return (self.emoji_id,)
        result = []
        for raw in values:
            value = str(raw).strip()
            if not (value.isascii() and value.isdecimal()):
                continue
            value = str(int(value))
            if value not in result:
                result.append(value)
        return tuple(result) or (self.emoji_id,)

    def _choose_emoji_id(self) -> str:
        return random.choice(self.emoji_ids) if self.random_enabled else self.emoji_id

    @filter.on_llm_request(priority=-(maxsize + 1))
    async def react_to_llm_request(
        self,
        event: AstrMessageEvent,
        _request: ProviderRequest,
    ) -> None:
        """React only to eligible group messages already entering the LLM.

        Args:
            event: Current QQ group event.
            _request: Native LLM request; it is never modified by this plugin.
        """
        if event.get_platform_name() != "aiocqhttp":
            return

        message_obj = getattr(event, "message_obj", None)
        original_text = str(getattr(message_obj, "message_str", "") or "").strip()
        if not event.is_at_or_wake_command:
            return
        if event.get_extra("_get_emoji_reply_sent", False):
            return

        raw_message = getattr(message_obj, "raw_message", None)
        if not isinstance(raw_message, Mapping):
            return
        if raw_message.get("post_type") != "message":
            return
        if raw_message.get("message_type") != "group":
            return

        raw_message_id = raw_message.get("message_id")
        try:
            message_id = int(raw_message_id)
        except (TypeError, ValueError):
            logger.debug("收到 LLOneBot 群消息但 message_id 无效：%r", raw_message_id)
            return

        self_id = str(raw_message.get("self_id") or event.get_self_id() or "")
        sender_id = str(raw_message.get("user_id") or event.get_sender_id() or "")
        if self_id and sender_id == self_id:
            return
        if not AA_COMMAND_PATTERN.match(original_text):
            messages = getattr(message_obj, "message", None)
            if (
                not self_id
                or not isinstance(messages, (list, tuple))
                or not any(
                    isinstance(part, At) and str(part.qq) == self_id
                    for part in messages
                )
            ):
                return

        bot = getattr(event, "bot", None)
        call_action = getattr(bot, "call_action", None)
        if not callable(call_action):
            logger.debug("当前 AstrBot QQ 事件没有可用的 call_action")
            return

        params: dict[str, object] = {
            "message_id": message_id,
            "emoji_id": self._choose_emoji_id(),
            "set": True,
        }
        if self_id:
            params["self_id"] = self_id

        try:
            # Supplemental feedback must not hold up the actual model request.
            await asyncio.wait_for(
                call_action("set_msg_emoji_like", **params),
                timeout=REACTION_TIMEOUT_SECONDS,
            )
            event.set_extra("_get_emoji_reply_sent", True)
        except asyncio.TimeoutError:
            logger.debug(
                "LLOneBot reaction timed out for message %s; continuing LLM", message_id
            )
        except (TypeError, ValueError) as exc:
            logger.warning("表情回应参数无效，已跳过消息 %s：%s", message_id, exc)
        except Exception as exc:  # noqa: BLE001
            # Optional feedback failures must not interrupt the native LLM flow.
            logger.debug(
                "LLOneBot reaction failed for message %s (%s)",
                message_id,
                type(exc).__name__,
            )
