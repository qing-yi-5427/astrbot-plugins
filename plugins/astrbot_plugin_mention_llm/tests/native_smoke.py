"""Exercise real AstrBot wake/agent/attachment code in an isolated process.

Run with the staged plugin's parent and /AstrBot on PYTHONPATH. No model,
platform, database or file-download operation reaches a live service.
"""

import asyncio
import copy
import json
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiocqhttp import Event
from astrbot.api.message_components import At, AtAll, File, Image, Plain, Record, Reply
from astrbot.api.provider import ProviderRequest
from astrbot.builtin_stars.astrbot.main import Main as BuiltinMain
from astrbot.core import astr_main_agent
from astrbot.core.conversation_mgr import Conversation
from astrbot.core.pipeline.context import PipelineContext
from astrbot.core.pipeline.process_stage.method.agent_sub_stages import internal
from astrbot.core.pipeline.process_stage.stage import AgentRequestSubStage
from astrbot.core.pipeline.waking_check.stage import WakingCheckStage
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.core.star.session_llm_manager import SessionServiceManager
from astrbot.core.star.session_plugin_manager import SessionPluginManager
from astrbot.core.star.star_handler import EventType, star_handlers_registry
from astrbot.core.utils.session_lock import session_lock_manager
from astrbot_plugin_mention_llm.main import MENTION_REQUEST, MentionLLMPlugin


def event(text, parts=None, *, private=False):
    """Construct a real native QQ event without a platform connection."""
    message = AstrBotMessage()
    message.type = MessageType.FRIEND_MESSAGE if private else MessageType.GROUP_MESSAGE
    message.group_id, message.self_id = "" if private else "123", "456"
    message.sender = MessageMember("789", "offline-user")
    message.message = [Plain(text)] if parts is None else parts
    message.message_str, message.message_id = text, "offline-message"
    message.raw_message = Event(
        {"self_id": 456, "message_type": "group", "group_id": 123}
    )
    result = AiocqhttpMessageEvent(
        text,
        message,
        PlatformMetadata("aiocqhttp", "offline", "offline"),
        "123",
        SimpleNamespace(
            call_action=AsyncMock(
                side_effect=AssertionError("Unexpected platform call")
            )
        ),
    )
    result.send = AsyncMock(side_effect=AssertionError("Unexpected QQ send"))
    result.send_typing, result.stop_typing = AsyncMock(), AsyncMock()
    return result


async def main():
    """Run native collection cases without contacting any live service."""
    config_path = Path("/AstrBot/data/cmd_config.json")
    config_bytes = config_path.read_bytes()
    cfg = json.loads(config_bytes.decode("utf-8-sig"))
    assert (
        cfg["wake_prefix"] == ["/"] and cfg["provider_settings"]["wake_prefix"] == "aa"
    )
    cfg = copy.deepcopy(cfg)
    history = [{"role": "user", "content": "offline-history"}]
    conversation = Conversation(
        platform_id="offline",
        user_id="offline:GroupMessage:123",
        cid="offline-cid",
        history=json.dumps(history),
        persona_id="offline-persona",
    )
    context = SimpleNamespace(
        conversation_manager=SimpleNamespace(
            get_curr_conversation_id=AsyncMock(return_value="offline-cid"),
            get_conversation=AsyncMock(return_value=conversation),
        ),
        get_config=lambda **kwargs: cfg,
    )
    ctx = PipelineContext(cfg, SimpleNamespace(context=context), "offline")
    results = []
    native_collector = internal.collect_initial_request
    collector_configs = []

    async def observed_collector(event, plugin_context, config, req=None):
        collector_configs.append(config)
        return await native_collector(event, plugin_context, config, req)

    async def get_conversation_locked(event, plugin_context):
        manager = session_lock_manager._get_loop_manager()
        assert manager._locks[event.unified_msg_origin].locked(), (
            "Conversation read outside native lock"
        )
        return conversation

    plugin = MentionLLMPlugin(None)

    async def hook(event, hook_type, *args, **kwargs):
        if hook_type == EventType.OnWaitingLLMRequestEvent:
            await plugin.mark_explicit_mention(event)
        return event.is_stopped()

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(internal, "collect_initial_request", observed_collector)
        )
        stack.enter_context(
            patch.object(astr_main_agent, "_get_session_conv", get_conversation_locked)
        )
        stack.enter_context(patch.object(internal, "call_event_hook", hook))
        stack.enter_context(
            patch.object(internal, "try_capture_follow_up", return_value=None)
        )
        stack.enter_context(
            patch.object(internal, "_select_provider", AsyncMock(return_value=object()))
        )
        stack.enter_context(
            patch.object(internal, "_provider_supports_modality", return_value=True)
        )
        build = stack.enter_context(
            patch.object(internal, "build_main_agent", AsyncMock(return_value=None))
        )
        stack.enter_context(
            patch.object(internal, "prepare_request_images", AsyncMock())
        )
        stack.enter_context(
            patch.object(internal, "_process_quote_message", AsyncMock())
        )
        session_ai = stack.enter_context(
            patch.object(
                SessionServiceManager,
                "should_process_llm_request",
                AsyncMock(return_value=True),
            )
        )
        session_plugin = stack.enter_context(
            patch.object(
                SessionPluginManager,
                "is_plugin_enabled_for_session",
                AsyncMock(return_value=True),
            )
        )
        stack.enter_context(
            patch.object(
                SessionPluginManager,
                "filter_handlers_by_session",
                AsyncMock(side_effect=lambda e, h: h),
            )
        )
        stack.enter_context(
            patch.object(
                star_handlers_registry, "get_handlers_by_event_type", return_value=[]
            )
        )
        image_paths = stack.enter_context(
            patch.object(Image, "convert_to_file_path", AsyncMock())
        )
        stack.enter_context(
            patch.object(
                Record,
                "convert_to_file_path",
                AsyncMock(return_value="/offline/audio.mp3"),
            )
        )
        stack.enter_context(
            patch.object(File, "get_file", AsyncMock(return_value="/offline/data.txt"))
        )
        await plugin.initialize()
        waking, agent = WakingCheckStage(), AgentRequestSubStage()
        await waking.initialize(ctx)
        waking._umo_auto_name_recorder.schedule = Mock()
        await agent.initialize(ctx)

        async def run_case(name, current, expected=None, *, preset=None):
            build.reset_mock()
            collector_configs.clear()
            original_text, raw_text = (
                current.message_str,
                current.message_obj.message_str,
            )
            messages = current.get_messages()
            await waking.process(current)
            # Production profile selection happens in the wake stage.
            if name == "profile_disabled":
                current.plugins_name = []
            if preset is not None:
                current.set_extra("provider_request", preset)
            native_text = current.message_str
            if not current.is_stopped():
                async for _ in agent.process(current):
                    pass
            assert cfg["provider_settings"]["wake_prefix"] == "aa" and cfg[
                "wake_prefix"
            ] == ["/"]
            assert (
                current.message_str == native_text
                and current.message_obj.message_str == raw_text
            )
            assert current.get_messages() is messages
            assert agent.agent_sub_stage.main_agent_cfg.provider_wake_prefix == ""
            if expected is None:
                build.assert_not_awaited()
                request = None
            else:
                build.assert_awaited_once()
                request = build.await_args.kwargs["req"]
                assert request.prompt == expected, (
                    name,
                    request.prompt,
                    expected,
                    original_text,
                )
                assert request.conversation.persona_id == "offline-persona"
                assert request.contexts == history
                assert build.await_args.kwargs["config"].provider_wake_prefix == "aa"
                if name.startswith("aa"):
                    assert collector_configs[0].provider_wake_prefix == "aa"
                elif preset is None:
                    assert collector_configs[0].provider_wake_prefix == ""
            current.send.assert_not_awaited()
            results.append({"case": name, "passed": True})
            return request

        try:
            await run_case(
                "mention_text", event("你好", [At(qq="456"), Plain("你好")]), "你好"
            )
            await run_case("aa_unchanged", event("/aa 你好"), " 你好")
            await run_case(
                "aa_with_mention",
                event("/aa 你好", [At(qq="456"), Plain("/aa 你好")]),
                " 你好",
            )
            await run_case("ordinary_group", event("你好"))
            await run_case(
                "mention_someone_else", event("你好", [At(qq="777"), Plain("你好")])
            )
            await run_case("mention_all", event("你好", [AtAll(), Plain("你好")]))
            await run_case("text_fake_mention", event("@机器人 你好"))
            await run_case(
                "private_unchanged",
                event("你好", [At(qq="456"), Plain("你好")], private=True),
            )
            await run_case(
                "profile_disabled", event("你好", [At(qq="456"), Plain("你好")])
            )
            session_plugin.return_value = False
            await run_case(
                "session_plugin_disabled", event("你好", [At(qq="456"), Plain("你好")])
            )
            session_plugin.return_value = True
            session_ai.return_value = False
            await run_case(
                "session_ai_disabled", event("你好", [At(qq="456"), Plain("你好")])
            )
            session_ai.return_value = True
            cfg["provider_settings"]["enable"] = False
            await run_case(
                "global_ai_disabled", event("你好", [At(qq="456"), Plain("你好")])
            )
            cfg["provider_settings"]["enable"] = True

            image_paths.side_effect = ["/offline/image.png", "/offline/quoted.png"]
            media = event(
                "看附件",
                [
                    At(qq="456"),
                    Plain("看附件"),
                    Image.fromFileSystem("/offline/image.png"),
                    Record.fromFileSystem("/offline/audio.mp3"),
                    File(name="data.txt", file="/offline/data.txt"),
                    Reply(
                        id="quoted",
                        sender_id="777",
                        chain=[Image.fromFileSystem("/offline/quoted.png")],
                    ),
                ],
            )
            request = await run_case("mention_attachments", media, "看附件")
            assert request.image_urls == ["/offline/image.png", "/offline/quoted.png"]
            assert request.audio_urls == ["/offline/audio.mp3"]
            assert "data.txt" in repr(request.extra_user_content_parts)

            bare = event("", [At(qq="456")])
            builtin = object.__new__(BuiltinMain)
            builtin.context = context
            iterator = builtin.handle_empty_mention(bare)
            preset = await anext(iterator)
            assert isinstance(preset, ProviderRequest)
            await iterator.aclose()  # Do not start the real 60-second waiter.
            await run_case("bare_mention_builtin", bare, preset.prompt, preset=preset)
            assert bare.get_extra(MENTION_REQUEST) is None

            await plugin.terminate()
            assert internal.collect_initial_request is observed_collector
            await run_case(
                "disabled_restores_gate", event("你好", [At(qq="456"), Plain("你好")])
            )
            await run_case("aa_after_disable", event("/aa 你好"), " 你好")
        finally:
            await plugin.terminate()
    assert config_path.read_bytes() == config_bytes
    print(
        json.dumps(
            {
                "offline": True,
                "model_api_calls": 0,
                "qq_messages": 0,
                "live_config_unchanged": True,
                "results": results,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
