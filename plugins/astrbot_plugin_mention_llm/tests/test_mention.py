"""Offline contract tests; no AstrBot installation, network or paid models."""

import asyncio
import importlib.util
import logging
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch


@dataclass
class BuildConfig:
    provider_wake_prefix: str = "aa"
    max_quoted_fallback_images: int = 7
    provider_settings: dict = field(default_factory=lambda: {"marker": ["unchanged"]})


@dataclass
class At:
    qq: str


class Event:
    def __init__(self, text="你好", messages=None):
        self.message_str = text
        self.message_obj = SimpleNamespace(
            message=[At("456")] if messages is None else messages,
            message_str=text,
            raw_message={"self_id": 456, "text": text},
        )
        self.extra = {}
        self.is_at_or_wake_command = True
        self.stopped = False
        self.group, self.self_id, self.sender = "123", "456", "789"
        self.platform = "aiocqhttp"
        self.unified_msg_origin = "qq:GroupMessage:123"
        self.plugins_name = None

    def get_messages(self):
        return self.message_obj.message

    def get_extra(self, key):
        return self.extra.get(key)

    def set_extra(self, key, value):
        self.extra[key] = value

    def get_group_id(self):
        return self.group

    def get_self_id(self):
        return self.self_id

    def get_sender_id(self):
        return self.sender

    def get_platform_name(self):
        return self.platform

    def is_stopped(self):
        return self.stopped


class MentionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = AsyncMock(return_value=(object(), None))

        async def original(event, plugin_context, config, req=None):
            return await self.calls(event, plugin_context, config, req)

        self.original = original
        self.internal = SimpleNamespace(collect_initial_request=original)
        names = (
            "astrbot",
            "astrbot.api",
            "astrbot.api.event",
            "astrbot.api.message_components",
            "astrbot.api.star",
            "astrbot.core.star.session_plugin_manager",
        )
        modules = {name: ModuleType(name) for name in names}
        modules["astrbot.api"].logger = logging.getLogger("mention-test")
        modules["astrbot.api.event"].AstrMessageEvent = Event
        modules["astrbot.api.event"].filter = SimpleNamespace(
            on_waiting_llm_request=lambda **kw: lambda fn: fn
        )
        modules["astrbot.api.message_components"].At = At
        modules["astrbot.api.star"].Context = object
        modules["astrbot.api.star"].Star = type(
            "Star", (), {"__init__": lambda self, ctx: None}
        )
        modules["astrbot.api.star"].register = lambda *args: lambda cls: cls
        self.session_enabled = AsyncMock(return_value=True)
        modules[
            "astrbot.core.star.session_plugin_manager"
        ].SessionPluginManager = SimpleNamespace(
            is_plugin_enabled_for_session=self.session_enabled
        )
        self.module_patch = patch.dict(sys.modules, modules)
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        spec = importlib.util.spec_from_file_location(
            "mention_under_test",
            Path(__file__).parents[1] / "main.py",
            submodule_search_locations=[str(Path(__file__).parents[1])],
        )
        self.module = importlib.util.module_from_spec(spec)
        package_patch = patch.dict(sys.modules, {spec.name: self.module})
        package_patch.start()
        self.addCleanup(package_patch.stop)
        spec.loader.exec_module(self.module)
        # Parser lifecycle and API behavior have separate focused tests.
        parser_patch = patch.object(self.module, "MentionParserPatch")
        self.parser_class = parser_patch.start()
        self.addCleanup(parser_patch.stop)
        self.import_patch = patch.object(
            self.module.importlib, "import_module", return_value=self.internal
        )
        self.import_patch.start()
        self.addCleanup(self.import_patch.stop)
        self.plugin = self.module.MentionLLMPlugin(None)
        await self.plugin.initialize()
        self.addAsyncCleanup(self.plugin.terminate)
        self.config, self.context = BuildConfig(), object()

    async def test_mention_copies_only_per_call_prefix(self):
        event = Event()
        text, parts, raw = (
            event.message_str,
            event.get_messages(),
            event.message_obj.raw_message,
        )
        await self.plugin.mark_explicit_mention(event)
        result = await self.internal.collect_initial_request(
            event, self.context, self.config
        )
        self.assertIs(result, self.calls.return_value)
        passed_event, context, scoped, request = self.calls.await_args.args
        self.assertIs(passed_event, event)
        self.assertIs(context, self.context)
        self.assertIsNone(request)
        self.assertIsNot(scoped, self.config)
        self.assertEqual(scoped.provider_wake_prefix, "")
        self.assertIs(scoped.provider_settings, self.config.provider_settings)
        self.assertEqual(scoped.max_quoted_fallback_images, 7)
        self.assertEqual(self.config.provider_wake_prefix, "aa")
        self.assertEqual(event.message_str, text)
        self.assertEqual(event.message_obj.message_str, text)
        self.assertIs(event.get_messages(), parts)
        self.assertIs(event.message_obj.raw_message, raw)
        self.calls.assert_awaited_once()

    async def test_native_aa_prefix_is_never_rewritten(self):
        for text in ("aa 你好", "aa", "aa /help", "aa 画一张猫图"):
            with self.subTest(text=text):
                event = Event(text)
                await self.plugin.mark_explicit_mention(event)
                await self.internal.collect_initial_request(
                    event, self.context, self.config
                )
                self.assertIs(self.calls.await_args.args[2], self.config)
                self.assertEqual(event.message_str, text)

    async def test_keyword_invocation_preserves_argument_identity(self):
        event = Event("aa hello", [])
        await self.internal.collect_initial_request(
            event=event, plugin_context=self.context, config=self.config
        )
        self.assertIs(self.calls.await_args.args[0], event)
        self.assertIs(self.calls.await_args.args[2], self.config)

    async def test_media_and_reply_objects_are_untouched(self):
        media = [At("456"), object(), SimpleNamespace(chain=[At("456")]), object()]
        event = Event("看看附件", media)
        await self.plugin.mark_explicit_mention(event)
        await self.internal.collect_initial_request(event, self.context, self.config)
        self.assertIs(self.calls.await_args.args[0].get_messages(), media)
        self.assertEqual(event.message_str, "看看附件")

    async def test_only_top_level_group_mentions_are_marked(self):
        cases = [
            Event(messages=[]),
            Event(messages=[At("777")]),
            Event(messages=[At("all")]),
            Event(messages=[At("0")]),
            Event("@机器人 你好", ["@456"]),
            Event(messages=[SimpleNamespace(chain=[At("456")])]),
        ]
        for attribute, value in (
            ("platform", "discord"),
            ("group", ""),
            ("self_id", ""),
            ("sender", "456"),
            ("is_at_or_wake_command", False),
            ("stopped", True),
            ("plugins_name", []),
            ("plugins_name", ["other_plugin"]),
        ):
            event = Event()
            setattr(event, attribute, value)
            cases.append(event)
        for event in cases:
            with self.subTest(event=event):
                await self.plugin.mark_explicit_mention(event)
                self.assertIsNone(event.get_extra(self.module.MENTION_REQUEST))
                await self.internal.collect_initial_request(
                    event, self.context, self.config
                )
                self.assertIs(self.calls.await_args.args[2], self.config)
        self.session_enabled.assert_not_awaited()

    async def test_session_disable_is_respected(self):
        self.session_enabled.return_value = False
        event = Event()
        await self.plugin.mark_explicit_mention(event)
        self.assertIsNone(event.get_extra(self.module.MENTION_REQUEST))
        self.session_enabled.assert_awaited_once_with(
            event.unified_msg_origin, self.module.NAME
        )

    async def test_profile_explicitly_enabling_plugin(self):
        event = Event()
        event.plugins_name = [self.module.NAME]
        await self.plugin.mark_explicit_mention(event)
        self.assertIs(event.get_extra(self.module.MENTION_REQUEST), True)

    async def test_prebuilt_requests_are_never_overridden(self):
        for supplied in ("extra", "positional", "keyword"):
            with self.subTest(supplied=supplied):
                event, request = Event(), object()
                if supplied == "extra":
                    event.set_extra("provider_request", request)
                await self.plugin.mark_explicit_mention(event)
                if supplied == "positional":
                    await self.internal.collect_initial_request(
                        event, self.context, self.config, request
                    )
                elif supplied == "keyword":
                    await self.internal.collect_initial_request(
                        event, self.context, self.config, req=request
                    )
                else:
                    await self.internal.collect_initial_request(
                        event, self.context, self.config
                    )
                    self.assertIsNone(event.get_extra(self.module.MENTION_REQUEST))
                self.assertIs(self.calls.await_args.args[2], self.config)

    async def test_empty_prefix_needs_no_override(self):
        self.config.provider_wake_prefix = ""
        event = Event()
        await self.plugin.mark_explicit_mention(event)
        await self.internal.collect_initial_request(event, self.context, self.config)
        self.assertIs(self.calls.await_args.args[2], self.config)

    async def test_concurrent_mention_and_aa_are_isolated(self):
        entered, release, configs = asyncio.Event(), asyncio.Event(), {}

        async def pending(event, context, config, req):
            configs[event.message_str] = config
            if event.message_str == "你好":
                entered.set()
                await release.wait()
            return None, None

        self.calls.side_effect = pending
        mention, aa = Event(), Event("aa 你好", messages=[])
        await self.plugin.mark_explicit_mention(mention)
        task = asyncio.create_task(
            self.internal.collect_initial_request(mention, self.context, self.config)
        )
        await entered.wait()
        await self.internal.collect_initial_request(aa, self.context, self.config)
        self.assertEqual(configs["你好"].provider_wake_prefix, "")
        self.assertIs(configs["aa 你好"], self.config)
        self.assertEqual(self.config.provider_wake_prefix, "aa")
        release.set()
        await task

    async def test_repeated_initialize_does_not_wrap_twice(self):
        wrapper = self.internal.collect_initial_request
        await self.plugin.initialize()
        self.assertIs(self.internal.collect_initial_request, wrapper)

    async def test_termination_restores_original(self):
        old_wrapper, event = self.internal.collect_initial_request, Event()
        await self.plugin.mark_explicit_mention(event)
        await self.plugin.terminate()
        self.assertIs(self.internal.collect_initial_request, self.original)
        await old_wrapper(event, self.context, self.config)
        self.assertIs(self.calls.await_args.args[2], self.config)
        unmarked = Event()
        await self.plugin.mark_explicit_mention(unmarked)
        self.assertIsNone(unmarked.get_extra(self.module.MENTION_REQUEST))

    async def test_other_plugins_later_wrapper_is_preserved(self):
        ours = self.internal.collect_initial_request

        async def other(*args, **kwargs):
            return await ours(*args, **kwargs)

        self.internal.collect_initial_request = other
        event = Event()
        await self.plugin.mark_explicit_mention(event)
        await self.plugin.terminate()
        self.assertIs(self.internal.collect_initial_request, other)
        await other(event, self.context, self.config)
        self.assertIs(self.calls.await_args.args[2], self.config)

    async def test_exception_and_cancellation_propagate(self):
        event = Event()
        await self.plugin.mark_explicit_mention(event)
        for error in (RuntimeError("offline"), asyncio.CancelledError()):
            self.calls.side_effect = error
            with self.assertRaises(type(error)):
                await self.internal.collect_initial_request(
                    event, self.context, self.config
                )
            self.assertEqual(self.config.provider_wake_prefix, "aa")

    async def test_incompatible_collector_fails_without_patching(self):
        await self.plugin.terminate()
        broken = AsyncMock()
        self.internal.collect_initial_request = broken
        with self.assertRaisesRegex(RuntimeError, "Unsupported AstrBot"):
            await self.plugin.initialize()
        self.assertIs(self.internal.collect_initial_request, broken)

    async def test_incompatible_parser_keeps_collector_unchanged(self):
        await self.plugin.terminate()
        self.plugin._parser_patch.install.side_effect = RuntimeError(
            "Unsupported parser"
        )
        with self.assertRaisesRegex(RuntimeError, "Unsupported parser"):
            await self.plugin.initialize()
        self.assertIs(self.internal.collect_initial_request, self.original)
        self.assertFalse(self.plugin._running)


if __name__ == "__main__":
    unittest.main()
