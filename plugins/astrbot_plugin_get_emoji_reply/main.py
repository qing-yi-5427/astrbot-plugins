"""为通过 /aa 调用 LLM 的 LLOneBot 群消息添加 随机 QQ 表情回应。"""

import random
import re
from collections.abc import Mapping
from sys import maxsize

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star


DEFAULT_EMOJI_IDS = ("124", "76", "201", "428")

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
        """仅回应由 /aa 触发且已经进入 LLM 请求阶段的群消息。"""
        if event.get_platform_name() != "aiocqhttp":
            return

        message_obj = getattr(event, "message_obj", None)
        original_text = str(getattr(message_obj, "message_str", "") or "").strip()
        if not AA_COMMAND_PATTERN.match(original_text):
            return
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
            await call_action("set_msg_emoji_like", **params)
            event.set_extra("_get_emoji_reply_sent", True)
        except (TypeError, ValueError) as exc:
            logger.warning("表情回应参数无效，已跳过消息 %s：%s", message_id, exc)
        except Exception as exc:
            # 回应是附加动作；接口失败不能中断实际 LLM 请求。
            logger.debug("LLOneBot 消息 %s 表情回应失败：%s", message_id, exc)
