"""Offline compatibility test with real AstrBot and the bot-mention bridge.

Put the staged plugins' parent and /AstrBot on PYTHONPATH, then run this file.
No model requests, QQ messages, reactions or persistent data writes are made.
"""

import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiocqhttp import Event
from astrbot.api.message_components import At, AtAll, Image, Plain, Reply
from astrbot.api.provider import ProviderRequest
from astrbot.builtin_stars.astrbot.main import Main as BuiltinMain
from astrbot.core import astr_main_agent
from astrbot.core.astr_main_agent import MainAgentBuildConfig
from astrbot.core.conversation_mgr import Conversation
from astrbot.core.pipeline.process_stage.method.agent_sub_stages import internal
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.core.star.session_plugin_manager import SessionPluginManager
from astrbot_plugin_get_emoji_reply import main as reactions
from astrbot_plugin_mention_llm.main import MentionLLMPlugin


def native_event(text, parts):
    """Build a native QQ group event with only a mocked OneBot connection."""
    message = AstrBotMessage()
    message.type = MessageType.GROUP_MESSAGE
    message.group_id, message.self_id = "123", "456"
    message.sender = MessageMember("789", "offline-user")
    message.message, message.message_str, message.message_id = parts, text, "101"
    message.raw_message = Event(
        {
            "post_type": "message",
            "message_type": "group",
            "self_id": 456,
            "user_id": 789,
            "group_id": 123,
            "message_id": 101,
        }
    )
    event = AiocqhttpMessageEvent(
        text.removeprefix("/") if text.startswith("/aa") else text,
        message,
        PlatformMetadata("aiocqhttp", "offline", "offline"),
        "123",
        SimpleNamespace(call_action=AsyncMock()),
    )
    event.is_at_or_wake_command = True
    return event


async def main():
    """Check prefix preservation, native mention collection and reactions."""
    conversation = Conversation(
        platform_id="offline",
        user_id="offline:GroupMessage:123",
        cid="offline-cid",
        history='[{"role":"user","content":"offline-history"}]',
        persona_id="offline-persona",
    )
    settings = {
        "platform_settings": {
            "empty_mention_waiting": True,
            "empty_mention_waiting_need_reply": True,
        },
        "wake_prefix": ["/"],
    }
    context = SimpleNamespace(
        get_config=lambda **kwargs: settings,
        conversation_manager=SimpleNamespace(
            get_curr_conversation_id=AsyncMock(return_value="offline-cid"),
            get_conversation=AsyncMock(return_value=conversation),
        ),
    )
    config = MainAgentBuildConfig(tool_call_timeout=120, provider_wake_prefix="aa")
    bridge = MentionLLMPlugin(None)
    plugin = reactions.Main(None, {"random_enabled": False, "emoji_id": 124})
    results = []
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(
                astr_main_agent,
                "_get_session_conv",
                AsyncMock(return_value=conversation),
            )
        )
        stack.enter_context(
            patch.object(
                SessionPluginManager,
                "is_plugin_enabled_for_session",
                AsyncMock(return_value=True),
            )
        )
        stack.enter_context(
            patch.object(
                Image,
                "convert_to_file_path",
                AsyncMock(return_value="/offline/image.png"),
            )
        )
        await bridge.initialize()
        try:
            cases = [
                ("aa", native_event("/aa 你好", [Plain("/aa 你好")]), True),
                ("mention", native_event("你好", [At(qq="456"), Plain("你好")]), True),
                (
                    "mention_and_aa",
                    native_event("/aa 你好", [At(qq="456"), Plain("/aa 你好")]),
                    True,
                ),
                (
                    "mention_image",
                    native_event(
                        "看看图",
                        [
                            At(qq="456"),
                            Plain("看看图"),
                            Image.fromFileSystem("/offline/image.png"),
                        ],
                    ),
                    True,
                ),
                ("ordinary", native_event("你好", [Plain("你好")]), False),
                (
                    "other_mention",
                    native_event("你好", [At(qq="777"), Plain("你好")]),
                    False,
                ),
                ("all_mention", native_event("你好", [AtAll(), Plain("你好")]), False),
                (
                    "quoted_mention",
                    native_event(
                        "你好",
                        [
                            Reply(id="quoted", sender_id="777", chain=[At(qq="456")]),
                            Plain("你好"),
                        ],
                    ),
                    False,
                ),
            ]
            for name, event, eligible in cases:
                before = event.message_obj.message_str
                await bridge.mark_explicit_mention(event)
                request, _ = await internal.collect_initial_request(
                    event, context, config
                )
                if not eligible:
                    assert request is None
                    event.bot.call_action.assert_not_awaited()
                else:
                    assert isinstance(request, ProviderRequest)
                    assert request.conversation.persona_id == "offline-persona"
                    assert request.contexts == [
                        {"role": "user", "content": "offline-history"}
                    ]
                    if name == "mention_image":
                        assert request.image_urls == ["/offline/image.png"]
                    old_prompt, old_context = request.prompt, request.contexts
                    await plugin.react_to_llm_request(event, request)
                    await plugin.react_to_llm_request(event, request)
                    event.bot.call_action.assert_awaited_once_with(
                        "set_msg_emoji_like",
                        message_id=101,
                        emoji_id="124",
                        set=True,
                        self_id="456",
                    )
                    assert (
                        request.prompt == old_prompt and request.contexts is old_context
                    )
                assert config.provider_wake_prefix == "aa"
                assert event.message_obj.message_str == before
                results.append({"case": name, "passed": True})

            bare = native_event("", [At(qq="456")])
            builtin = object.__new__(BuiltinMain)
            builtin.context = context
            iterator = builtin.handle_empty_mention(bare)
            request = await anext(iterator)
            assert isinstance(request, ProviderRequest)
            await iterator.aclose()  # Do not start the real 60-second waiter.
            await plugin.react_to_llm_request(bare, request)
            bare.bot.call_action.assert_awaited_once()
            results.append({"case": "bare_mention_native_builtin", "passed": True})

            stalled = native_event("你好", [At(qq="456"), Plain("你好")])
            cancelled = asyncio.Event()

            async def blocked(*args, **kwargs):
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

            stalled.bot.call_action.side_effect = blocked
            with patch.object(reactions, "REACTION_TIMEOUT_SECONDS", 0.01):
                await asyncio.wait_for(
                    plugin.react_to_llm_request(stalled, ProviderRequest()), timeout=0.5
                )
            assert cancelled.is_set() and not stalled.get_extra(
                "_get_emoji_reply_sent", False
            )
            results.append(
                {"case": "reaction_timeout_does_not_block_llm", "passed": True}
            )
        finally:
            await bridge.terminate()
    import json

    print(
        json.dumps(
            {
                "offline": True,
                "model_api_calls": 0,
                "qq_messages": 0,
                "live_reactions": 0,
                "results": results,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
