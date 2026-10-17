import asyncio
import importlib.util
import logging
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch


def load_plugin():
    modules = {
        name: ModuleType(name)
        for name in (
            "astrbot",
            "astrbot.api",
            "astrbot.api.event",
            "astrbot.api.provider",
            "astrbot.api.star",
            "astrbot.api.message_components",
        )
    }
    modules["astrbot.api"].AstrBotConfig = dict
    modules["astrbot.api"].logger = logging.getLogger("reaction-test")
    modules["astrbot.api.event"].AstrMessageEvent = object
    modules["astrbot.api.event"].filter = SimpleNamespace(
        on_llm_request=lambda **kw: lambda fn: fn
    )
    modules["astrbot.api.message_components"].At = At
    modules["astrbot.api.provider"].ProviderRequest = object
    modules["astrbot.api.star"].Context = object
    modules["astrbot.api.star"].Star = type(
        "Star", (), {"__init__": lambda self, ctx: None}
    )
    spec = importlib.util.spec_from_file_location(
        "emoji_under_test", Path(__file__).parents[1] / "main.py"
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class At:
    def __init__(self, qq):
        self.qq = qq


plugin = load_plugin()


def event(text="/aa test", *, messages=None, **raw_changes):
    extra = {}
    raw = {
        "post_type": "message",
        "message_type": "group",
        "message_id": 123,
        "self_id": 1,
        "user_id": 2,
    }
    raw.update(raw_changes)
    return SimpleNamespace(
        message_obj=SimpleNamespace(
            message_str=text, raw_message=raw, message=messages or []
        ),
        get_platform_name=lambda: "aiocqhttp",
        is_at_or_wake_command=True,
        get_extra=lambda key, default=None: extra.get(key, default),
        set_extra=lambda key, value: extra.update({key: value}),
        get_self_id=lambda: "1",
        get_sender_id=lambda: "2",
        bot=SimpleNamespace(call_action=AsyncMock()),
    )


class ReactionTests(unittest.IsolatedAsyncioTestCase):
    def test_legacy_config_enables_default_random_pool(self):
        obj = plugin.Main(None, {"emoji_id": 201})
        self.assertTrue(obj.random_enabled)
        self.assertEqual(obj.emoji_ids, plugin.DEFAULT_EMOJI_IDS)
        self.assertEqual(len(set(obj.emoji_ids)), 10)
        self.assertEqual(obj.emoji_id, "201")

    def test_pool_normalizes_and_deduplicates(self):
        obj = plugin.Main(
            None, {"emoji_ids": ["076", 76, " 124 ", None, True, -1, "abc", "１２４"]}
        )
        self.assertEqual(obj.emoji_ids, ("76", "124"))

    def test_invalid_pools_fall_back(self):
        for pool in ([], [None, "bad"], "124,76", None):
            with self.subTest(pool=pool):
                self.assertEqual(
                    plugin.Main(
                        None, {"emoji_ids": pool, "emoji_id": 201}
                    )._choose_emoji_id(),
                    "201",
                )

    def test_invalid_fixed_id_falls_back(self):
        self.assertEqual(
            plugin.Main(
                None, {"random_enabled": False, "emoji_id": "bad"}
            )._choose_emoji_id(),
            "124",
        )

    def test_fixed_mode_does_not_draw_randomly(self):
        with patch.object(plugin.random, "choice", side_effect=AssertionError):
            self.assertEqual(
                plugin.Main(
                    None, {"random_enabled": False, "emoji_id": 201}
                )._choose_emoji_id(),
                "201",
            )

    async def test_each_message_uses_a_fresh_pool_choice(self):
        obj = plugin.Main(None, {"emoji_ids": [76, 124]})
        with patch.object(plugin.random, "choice", side_effect=["76", "124"]) as choice:
            for expected in ("76", "124"):
                e = event()
                await obj.react_to_llm_request(e, None)
                e.bot.call_action.assert_awaited_once_with(
                    "set_msg_emoji_like",
                    message_id=123,
                    emoji_id=expected,
                    set=True,
                    self_id="1",
                )
            self.assertEqual(choice.call_count, 2)
            choice.assert_called_with(("76", "124"))

    async def test_successful_event_is_not_reacted_to_twice(self):
        obj, e = plugin.Main(None, {}), event()
        await obj.react_to_llm_request(e, None)
        await obj.react_to_llm_request(e, None)
        e.bot.call_action.assert_awaited_once()

    async def test_only_aa_group_messages_are_eligible(self):
        cases = [event(t) for t in ("hello", "/aaa", "/aa_test", "/help")]
        cases += [
            event(message_type="private"),
            event(post_type="notice"),
            event(user_id=1),
            event(message_id="bad"),
        ]
        e = event()
        e.is_at_or_wake_command = False
        cases.append(e)
        e = event()
        e.get_platform_name = lambda: "other"
        cases.append(e)
        for e in cases:
            await plugin.Main(None, {}).react_to_llm_request(e, None)
            e.bot.call_action.assert_not_awaited()

    async def test_api_failure_does_not_break_llm_request(self):
        e = event()
        e.bot.call_action.side_effect = RuntimeError("unsupported emoji")
        await plugin.Main(None, {}).react_to_llm_request(e, None)
        self.assertFalse(e.get_extra("_get_emoji_reply_sent", False))

    async def test_explicit_bot_mentions_are_eligible(self):
        for parts in ([At("1")], [At(1), object()], [object(), At("1")]):
            with self.subTest(parts=parts):
                e = event("你好", messages=parts)
                await plugin.Main(None, {"random_enabled": False}).react_to_llm_request(
                    e, None
                )
                e.bot.call_action.assert_awaited_once_with(
                    "set_msg_emoji_like",
                    message_id=123,
                    emoji_id="124",
                    set=True,
                    self_id="1",
                )

    async def test_raw_bot_id_is_used_for_both_matching_and_routing(self):
        e = event("你好", messages=[At("9")], self_id=9)
        await plugin.Main(None, {}).react_to_llm_request(e, None)
        self.assertEqual(e.bot.call_action.await_args.kwargs["self_id"], "9")

    async def test_mention_uses_same_random_pool_and_deduplication_as_aa(self):
        obj = plugin.Main(None, {"emoji_ids": [76, 124]})
        with patch.object(plugin.random, "choice", side_effect=["76", "124"]) as choice:
            first, second = event("你好", messages=[At("1")]), event()
            for e in (first, second):
                await obj.react_to_llm_request(e, None)
                await obj.react_to_llm_request(e, None)
                e.bot.call_action.assert_awaited_once()
            self.assertEqual(first.bot.call_action.await_args.kwargs["emoji_id"], "76")
            self.assertEqual(
                second.bot.call_action.await_args.kwargs["emoji_id"], "124"
            )
            self.assertEqual(choice.call_count, 2)

    async def test_at_plus_aa_only_sends_one_reaction(self):
        e = event("/aa 你好", messages=[At("1")])
        obj = plugin.Main(None, {})
        await obj.react_to_llm_request(e, None)
        await obj.react_to_llm_request(e, None)
        e.bot.call_action.assert_awaited_once()

    async def test_fake_quoted_all_and_other_mentions_are_not_eligible(self):
        cases = [
            event("@机器人 你好"),
            event("你好", messages=[At("2")]),
            event("你好", messages=[At("all")]),
            event("你好", messages=[SimpleNamespace(chain=[At("1")])]),
            event("你好", messages=[{"type": "at", "data": {"qq": "1"}}]),
        ]
        malformed = event("你好")
        malformed.message_obj.message = 123
        cases.append(malformed)
        for e in cases:
            with self.subTest(event=e):
                await plugin.Main(None, {}).react_to_llm_request(e, None)
                e.bot.call_action.assert_not_awaited()

    async def test_mention_still_requires_group_event_and_native_wake(self):
        cases = [
            event("你好", messages=[At("1")], message_type="private"),
            event("你好", messages=[At("1")], post_type="notice"),
            event("你好", messages=[At("1")], user_id=1),
            event("你好", messages=[At("1")], message_id="bad"),
        ]
        e = event("你好", messages=[At("1")])
        e.is_at_or_wake_command = False
        cases.append(e)
        e = event("你好", messages=[At("1")])
        e.get_platform_name = lambda: "other"
        cases.append(e)
        for e in cases:
            await plugin.Main(None, {}).react_to_llm_request(e, None)
            e.bot.call_action.assert_not_awaited()

    async def test_original_event_and_request_are_untouched(self):
        parts = [At("1"), SimpleNamespace(image="offline-image")]
        e, req = (
            event("你好", messages=parts),
            SimpleNamespace(prompt="persona prompt", contexts=["history"]),
        )
        before_event, before_request = (
            e.message_obj.__dict__.copy(),
            req.__dict__.copy(),
        )
        await plugin.Main(None, {}).react_to_llm_request(e, req)
        self.assertEqual(e.message_obj.__dict__, before_event)
        self.assertEqual(req.__dict__, before_request)
        self.assertIs(e.message_obj.message, parts)

    async def test_slow_reaction_is_cancelled_and_llm_can_continue(self):
        cancelled = asyncio.Event()

        async def blocked(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        for text, parts in (("/aa test", []), ("你好", [At("1")])):
            with (
                self.subTest(text=text),
                patch.object(plugin, "REACTION_TIMEOUT_SECONDS", 0.01),
            ):
                e = event(text, messages=parts)
                e.bot.call_action.side_effect = blocked
                await asyncio.wait_for(
                    plugin.Main(None, {}).react_to_llm_request(e, None), timeout=0.5
                )
                self.assertTrue(cancelled.is_set())
                self.assertFalse(e.get_extra("_get_emoji_reply_sent", False))
                e.bot.call_action.assert_awaited_once()

    async def test_cancellation_propagates_instead_of_starting_more_work(self):
        e = event("你好", messages=[At("1")])
        e.bot.call_action.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await plugin.Main(None, {}).react_to_llm_request(e, None)
        self.assertFalse(e.get_extra("_get_emoji_reply_sent", False))

    async def test_invalid_adapter_skips_optional_reaction(self):
        e = event("你好", messages=[At("1")])
        e.bot = None
        await plugin.Main(None, {}).react_to_llm_request(e, None)


if __name__ == "__main__":
    unittest.main()
