"""Offline regression tests against AstrBot's real OneBot message parser.

Run in an isolated AstrBot process with the staged plugin parent and /AstrBot
on PYTHONPATH. No QQ sends, network calls, model requests or database writes.
"""

import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiocqhttp import Event
from astrbot.api.message_components import At, File, Image, Record, Reply
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
    AiocqhttpAdapter,
)
from astrbot.core.star.session_plugin_manager import SessionPluginManager
from astrbot_plugin_mention_llm import mention_parser
from astrbot_plugin_mention_llm.main import NAME


def at(qq):
    """Build a structured OneBot mention."""
    return {"type": "at", "data": {"qq": str(qq)}}


def text(value):
    """Build a structured OneBot text segment."""
    return {"type": "text", "data": {"text": value}}


def event(parts=None, **overrides):
    """Build a real raw OneBot event without connecting a client."""
    data = {
        "time": 1792244505,
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "message_id": 100,
        "self_id": 456,
        "group_id": 123,
        "user_id": 789,
        "sender": {"user_id": 789, "nickname": "offline-user"},
        "message": [at(456), text("你好")] if parts is None else parts,
    }
    data.update(overrides)
    return Event(data)


class NativeParserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.native = AiocqhttpAdapter._convert_handle_message_event
        self.cfg = {"plugin_set": ["*"], "platform_settings": {}}
        self.context = SimpleNamespace(get_config=lambda **kwargs: self.cfg)
        self.enabled = AsyncMock(return_value=True)
        session_patch = patch.object(
            SessionPluginManager, "is_plugin_enabled_for_session", self.enabled
        )
        session_patch.start()
        self.addCleanup(session_patch.stop)

        async def action(action, **params):
            if action == "get_group_member_info":
                return {"card": "real-card", "user_id": params["user_id"]}
            if action == "get_stranger_info":
                return {"nickname": "real-nickname"}
            raise AssertionError(f"Unexpected platform action: {action}")

        self.bot = SimpleNamespace(
            call_action=AsyncMock(side_effect=action),
            send=AsyncMock(side_effect=AssertionError("Unexpected QQ send")),
            marker=object(),
        )
        self.adapter = object.__new__(AiocqhttpAdapter)
        self.adapter.bot = self.bot
        self.adapter.metadata = PlatformMetadata("aiocqhttp", "offline", "offline")
        self.patch = mention_parser.MentionParserPatch(self.context, NAME)
        self.patch.install()
        self.addCleanup(self.patch.uninstall)

    async def parse(self, raw=None, **kwargs):
        return await self.adapter._convert_handle_message_event(
            event() if raw is None else raw, **kwargs
        )

    async def test_self_mention_never_awaits_slow_profile_api(self):
        async def slow(*args, **kwargs):
            await asyncio.sleep(35.7)
            raise AssertionError("Self nickname lookup should never run")

        self.bot.call_action.side_effect = slow
        raw = event()
        snapshot = copy.deepcopy(raw)
        started = asyncio.get_running_loop().time()
        result = await asyncio.wait_for(self.parse(raw), timeout=0.5)
        self.assertLess(asyncio.get_running_loop().time() - started, 0.5)
        self.assertEqual(result.message_str, "你好")
        self.assertIsInstance(result.message[0], At)
        self.assertEqual(str(result.message[0].qq), "456")
        self.assertEqual(result.message[0].name, "456")
        self.assertEqual(raw, snapshot)
        self.assertIs(result.raw_message, raw)
        self.assertIs(self.adapter.bot, self.bot)
        self.assertIs(self.adapter.create_event(result).bot, self.bot)
        self.bot.call_action.assert_not_awaited()

    async def test_aa_ordinary_private_other_and_all_keep_native_results(self):
        cases = (
            event([text("/aa 你好")]),
            event([text("ordinary @456 text")]),
            event([at(777), text("你好")]),
            event([at("all"), text("你好")]),
            event([at(456), text("你好")], message_type="private"),
            event([at(456), text("你好")], user_id=456),
        )
        for raw in cases:
            with self.subTest(raw=raw.message):
                actual = await self.parse(raw)
                native = await self.native(self.adapter, raw)
                self.assertEqual(actual.message_str, native.message_str)
                self.assertEqual(actual.message, native.message)
                self.assertEqual(actual.sender, native.sender)
                self.assertEqual(actual.session_id, native.session_id)
        self.enabled.assert_not_awaited()

    async def test_aa_with_self_mention_keeps_command_text(self):
        result = await self.parse(event([at(456), text("/aa 你好")]))
        self.assertEqual(result.message_str, "/aa 你好")
        self.bot.call_action.assert_not_awaited()

    async def test_other_mentions_keep_successful_names_and_routing(self):
        raw = event([at(456), at(777), text("你好")])
        result = await self.parse(raw)
        self.assertEqual([str(p.qq) for p in result.message[:2]], ["456", "777"])
        self.assertEqual(result.message[1].name, "real-card")
        self.assertIn("@real-card(777)", result.message_str)
        self.bot.call_action.assert_awaited_once_with(
            "get_group_member_info",
            group_id=123,
            user_id=777,
            no_cache=False,
            self_id=456,
        )

    async def test_empty_card_uses_native_stranger_lookup(self):
        self.bot.call_action.side_effect = (
            {"card": "", "nickname": "group-name"},
            {"nickname": "stranger-name"},
        )
        result = await self.parse(event([at(456), at(777)]))
        self.assertEqual(result.message[1].name, "stranger-name")
        self.assertEqual(self.bot.call_action.await_count, 2)

    async def test_budget_is_total_not_per_mentioned_user(self):
        async def slow(*args, **kwargs):
            await asyncio.sleep(35.7)

        self.bot.call_action.side_effect = slow
        raw = event([at(456)] + [at(qq) for qq in range(700, 720)])
        # Exercise the production one-second total budget, not a scaled timeout.
        started = asyncio.get_running_loop().time()
        result = await asyncio.wait_for(self.parse(raw), timeout=2)
        elapsed = asyncio.get_running_loop().time() - started
        self.assertGreaterEqual(elapsed, 0.9)
        self.assertLess(elapsed, 1.8)
        self.assertEqual(len(result.message), 21)
        self.assertEqual(result.message[-1].name, "719")
        self.bot.call_action.assert_awaited_once()

    async def test_group_and_stranger_queries_share_same_deadline(self):
        async def slow(action, **params):
            await asyncio.sleep(0.03)
            return {"card": ""} if action == "get_group_member_info" else {}

        self.bot.call_action.side_effect = slow
        with patch.object(mention_parser, "NICKNAME_BUDGET_SECONDS", 0.04):
            result = await self.parse(event([at(456), at(777), at(778)]))
        self.assertEqual(result.message[1].name, "777")
        self.assertEqual(result.message[2].name, "778")
        self.assertEqual(self.bot.call_action.await_count, 2)

    async def test_failures_and_empty_profiles_preserve_mention_identity(self):
        for failure in (RuntimeError("offline"), None, {}, [], "bad response"):
            with self.subTest(failure=type(failure).__name__):
                if isinstance(failure, Exception):
                    self.bot.call_action.side_effect = failure
                else:
                    self.bot.call_action.side_effect = None
                    self.bot.call_action.return_value = failure
                result = await self.parse(event([at(456), at(777)]))
                self.assertEqual(str(result.message[1].qq), "777")
                self.assertEqual(result.message[1].name, "777")

    async def test_profile_and_session_disable_restore_native_lookup(self):
        self.cfg["plugin_set"] = ["another_plugin"]
        result = await self.parse()
        self.assertEqual(result.message[0].name, "real-card")
        self.enabled.assert_not_awaited()
        self.cfg["plugin_set"] = [NAME]
        self.enabled.return_value = False
        result = await self.parse()
        self.assertEqual(result.message[0].name, "real-card")
        self.enabled.assert_awaited_once_with("offline:GroupMessage:123", NAME)

    async def test_unique_session_disable_uses_native_session_id(self):
        self.cfg["platform_settings"]["unique_session"] = True
        self.enabled.return_value = False
        await self.parse()
        self.enabled.assert_awaited_once_with("offline:GroupMessage:789_123", NAME)
        self.bot.call_action.assert_awaited_once()

    async def test_media_and_file_apis_are_not_replaced(self):
        file_segment = {
            "type": "file",
            "data": {"file_id": "offline-file", "file": "report.txt"},
        }

        async def action(action, **params):
            self.assertEqual(action, "get_group_file_url")
            return {"url": "https://offline.invalid/report", "name": "report.txt"}

        self.bot.call_action.side_effect = action
        raw = event(
            [
                file_segment,
                at(456),
                {"type": "image", "data": {"file": "https://offline.invalid/image"}},
                {"type": "record", "data": {"file": "https://offline.invalid/audio"}},
            ]
        )
        snapshot = copy.deepcopy(raw)
        result = await self.parse(raw)
        self.assertEqual([type(p) for p in result.message], [File, At, Image, Record])
        self.assertEqual(result.message[0].name, "report.txt")
        self.assertEqual(raw, snapshot)
        self.bot.call_action.assert_awaited_once_with(
            "get_group_file_url", file_id="offline-file", group_id=123, self_id=456
        )

    async def test_quote_parser_does_not_inherit_outer_names_or_budget(self):
        quoted = event([at(456), at(777), text("quoted")], message_id=99)

        async def action(action, **params):
            if action == "get_msg":
                return dict(copy.deepcopy(quoted))
            return {"card": "quoted-real-name"}

        self.bot.call_action.side_effect = action
        raw = event([{"type": "reply", "data": {"id": "99"}}, at(456), text("hi")])
        result = await self.parse(raw)
        self.assertIsInstance(result.message[0], Reply)
        self.assertEqual(result.message[0].chain[0].name, "quoted-real-name")
        self.assertEqual(result.message[0].chain[1].name, "quoted-real-name")
        self.assertEqual(result.message[1].name, "456")
        self.assertEqual(self.bot.call_action.await_count, 3)
        self.assertIs(self.adapter.bot, self.bot)

    async def test_quoted_self_mention_alone_does_not_enable_fast_path(self):
        self.bot.call_action.return_value = dict(event([at(456)], message_id=99))
        self.bot.call_action.side_effect = None
        await self.parse(event([{"type": "reply", "data": {"id": "99"}}]))
        self.enabled.assert_not_awaited()

    async def test_concurrent_messages_never_replace_shared_client(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocking(action, **params):
            entered.set()
            await release.wait()
            return {"card": "real-name"}

        self.bot.call_action.side_effect = blocking
        pending = asyncio.create_task(self.parse(event([at(456), at(777)])))
        await entered.wait()
        try:
            other = await asyncio.wait_for(self.parse(event([text("/aa hi")])), 0.5)
            self.assertEqual(other.message_str, "/aa hi")
            self.assertIs(self.adapter.bot, self.bot)
        finally:
            release.set()
            await pending

    async def test_cancellation_is_not_swallowed_by_native_parser(self):
        entered = asyncio.Event()

        async def blocking(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        self.bot.call_action.side_effect = blocking
        pending = asyncio.create_task(self.parse(event([at(456), at(777)])))
        await entered.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertIs(self.adapter.bot, self.bot)

    async def test_other_onebot_actions_and_routing_are_exact_passthrough(self):
        proxy = mention_parser._NicknameBot(self.bot, event(), {"456", "777"})
        result = {"message_id": 1234}
        self.bot.call_action.side_effect = None
        self.bot.call_action.return_value = result
        for action, params in (
            ("send_group_msg", {"group_id": 123, "message": "offline"}),
            ("set_msg_emoji_like", {"message_id": 100, "emoji_id": 76}),
            ("get_group_member_info", {"user_id": 456, "group_id": 999}),
            ("get_stranger_info", {"user_id": 456, "self_id": 999}),
            ("get_stranger_info", {"user_id": 888}),
        ):
            self.assertIs(await proxy.call_action(action, **params), result)
            self.bot.call_action.assert_awaited_with(action, **params)
        self.assertIs(proxy.marker, self.bot.marker)

    async def test_uninstall_restores_and_old_wrapper_is_passive(self):
        old_wrapper = AiocqhttpAdapter._convert_handle_message_event
        self.patch.uninstall()
        self.assertIs(AiocqhttpAdapter._convert_handle_message_event, self.native)
        result = await old_wrapper(self.adapter, event())
        self.assertEqual(result.message[0].name, "real-card")
        self.bot.call_action.assert_awaited_once()

    async def test_other_plugin_wrapper_survives_uninstall(self):
        ours = AiocqhttpAdapter._convert_handle_message_event

        async def other(adapter, raw, get_reply=True):
            return await ours(adapter, raw, get_reply)

        AiocqhttpAdapter._convert_handle_message_event = other
        try:
            self.patch.uninstall()
            self.assertIs(AiocqhttpAdapter._convert_handle_message_event, other)
            result = await self.parse()
            self.assertEqual(result.message[0].name, "real-card")
        finally:
            AiocqhttpAdapter._convert_handle_message_event = self.native

    async def test_incompatible_parser_does_not_install_wrapper(self):
        self.patch.uninstall()

        async def unsupported(self, event):
            return event

        with patch.object(
            AiocqhttpAdapter, "_convert_handle_message_event", unsupported
        ):
            with self.assertRaisesRegex(RuntimeError, "Unsupported AstrBot QQ parser"):
                self.patch.install()
            self.assertIs(AiocqhttpAdapter._convert_handle_message_event, unsupported)
            self.assertFalse(self.patch.active)


if __name__ == "__main__":
    unittest.main(verbosity=2)
