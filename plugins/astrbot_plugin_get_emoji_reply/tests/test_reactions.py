import importlib.util
import logging
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


def load_plugin():
    modules = {name: ModuleType(name) for name in (
        "astrbot", "astrbot.api", "astrbot.api.event", "astrbot.api.provider", "astrbot.api.star"
    )}
    modules["astrbot.api"].AstrBotConfig = dict
    modules["astrbot.api"].logger = logging.getLogger("reaction-test")
    modules["astrbot.api.event"].AstrMessageEvent = object
    modules["astrbot.api.event"].filter = SimpleNamespace(on_llm_request=lambda **kw: lambda fn: fn)
    modules["astrbot.api.provider"].ProviderRequest = object
    modules["astrbot.api.star"].Context = object
    modules["astrbot.api.star"].Star = type("Star", (), {"__init__": lambda self, ctx: None})
    spec = importlib.util.spec_from_file_location("emoji_under_test", Path(__file__).parents[1] / "main.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


plugin = load_plugin()


def event(text="/aa test", **raw_changes):
    extra = {}
    raw = dict(post_type="message", message_type="group", message_id=123, self_id=1, user_id=2)
    raw.update(raw_changes)
    return SimpleNamespace(
        message_obj=SimpleNamespace(message_str=text, raw_message=raw),
        get_platform_name=lambda: "aiocqhttp", is_at_or_wake_command=True,
        get_extra=lambda key, default=None: extra.get(key, default),
        set_extra=lambda key, value: extra.update({key: value}),
        get_self_id=lambda: "1", get_sender_id=lambda: "2",
        bot=SimpleNamespace(call_action=AsyncMock()),
    )


class ReactionTests(unittest.IsolatedAsyncioTestCase):
    def test_legacy_config_enables_default_random_pool(self):
        obj = plugin.Main(None, {"emoji_id": 201})
        self.assertTrue(obj.random_enabled)
        self.assertEqual(obj.emoji_ids, plugin.DEFAULT_EMOJI_IDS)
        self.assertEqual(obj.emoji_id, "201")

    def test_pool_normalizes_and_deduplicates(self):
        obj = plugin.Main(None, {"emoji_ids": ["076", 76, " 124 ", None, True, -1, "abc", "１２４"]})
        self.assertEqual(obj.emoji_ids, ("76", "124"))

    def test_invalid_pools_fall_back(self):
        for pool in ([], [None, "bad"], "124,76", None):
            with self.subTest(pool=pool):
                self.assertEqual(plugin.Main(None, {"emoji_ids": pool, "emoji_id": 201})._choose_emoji_id(), "201")

    def test_invalid_fixed_id_falls_back(self):
        self.assertEqual(plugin.Main(None, {"random_enabled": False, "emoji_id": "bad"})._choose_emoji_id(), "124")

    def test_fixed_mode_does_not_draw_randomly(self):
        with patch.object(plugin.random, "choice", side_effect=AssertionError):
            self.assertEqual(plugin.Main(None, {"random_enabled": False, "emoji_id": 201})._choose_emoji_id(), "201")

    async def test_each_message_uses_a_fresh_pool_choice(self):
        obj = plugin.Main(None, {"emoji_ids": [76, 124]})
        with patch.object(plugin.random, "choice", side_effect=["76", "124"]) as choice:
            for expected in ("76", "124"):
                e = event()
                await obj.react_to_llm_request(e, None)
                e.bot.call_action.assert_awaited_once_with("set_msg_emoji_like", message_id=123, emoji_id=expected, set=True, self_id="1")
            self.assertEqual(choice.call_count, 2)
            choice.assert_called_with(("76", "124"))

    async def test_successful_event_is_not_reacted_to_twice(self):
        obj, e = plugin.Main(None, {}), event()
        await obj.react_to_llm_request(e, None)
        await obj.react_to_llm_request(e, None)
        e.bot.call_action.assert_awaited_once()

    async def test_only_aa_group_messages_are_eligible(self):
        cases = [event(t) for t in ("hello", "/aaa", "/aa_test", "/help")]
        cases += [event(message_type="private"), event(post_type="notice"), event(user_id=1), event(message_id="bad")]
        e = event(); e.is_at_or_wake_command = False; cases.append(e)
        e = event(); e.get_platform_name = lambda: "other"; cases.append(e)
        for e in cases:
            await plugin.Main(None, {}).react_to_llm_request(e, None)
            e.bot.call_action.assert_not_awaited()

    async def test_api_failure_does_not_break_llm_request(self):
        e = event(); e.bot.call_action.side_effect = RuntimeError("unsupported emoji")
        await plugin.Main(None, {}).react_to_llm_request(e, None)
        self.assertFalse(e.get_extra("_get_emoji_reply_sent", False))


if __name__ == "__main__":
    unittest.main()
