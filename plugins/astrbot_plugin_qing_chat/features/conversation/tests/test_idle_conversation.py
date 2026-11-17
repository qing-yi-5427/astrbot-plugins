"""Idle-rotation regression tests; every database and OneBot action is offline."""

import asyncio
import copy
import logging
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from astrbot.builtin_stars.astrbot.group_chat_context import (
    GROUP_HISTORY_FOOTER,
    GROUP_HISTORY_HEADER,
)
from astrbot.core.agent.message import TextPart
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.session_plugin_manager import SessionPluginManager
from astrbot_plugin_qing_chat.features.conversation import idle_conversation as module


class Event:
    def __init__(self, group="123"):
        self.group = group
        self.unified_msg_origin = "offline:GroupMessage:" + group
        self.extra = {
            "_group_context_record_id": "trigger",
            "_group_context_raw_idx": 3,
        }

    def get_extra(self, key):
        return self.extra.get(key)

    def set_extra(self, key, value):
        self.extra[key] = value

    def get_group_id(self):
        return self.group

    def get_platform_id(self):
        return "offline"

    def get_platform_name(self):
        return "aiocqhttp"


class IdleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 1800000000.0
        self.clock = patch.object(module.time, "time", lambda: self.now)
        self.clock.start()
        self.store = {}
        self.current = {}
        self.saved = {}

        async def create(umo, platform_id, persona_id=None):
            cid = "new-" + str(len(self.saved))
            self.saved[cid] = SimpleNamespace(
                cid=cid, persona_id=persona_id, history="[]"
            )
            self.current[umo] = cid
            return cid

        async def switch(umo, cid):
            self.current[umo] = cid

        self.manager = SimpleNamespace(
            get_curr_conversation_id=AsyncMock(
                side_effect=lambda umo: self.current[umo]
            ),
            new_conversation=AsyncMock(side_effect=create),
            get_conversation=AsyncMock(side_effect=lambda umo, cid: self.saved[cid]),
            switch_conversation=AsyncMock(side_effect=switch),
        )
        self.group_context = SimpleNamespace(on_req_llm=AsyncMock())
        self.original_send = AsyncMock(return_value={"message_id": 42})
        self.bot = SimpleNamespace(call_action=self.original_send)
        self.platform = SimpleNamespace(
            meta=lambda: SimpleNamespace(name="aiocqhttp", id="offline"), bot=self.bot
        )

        async def get(key, default):
            return copy.deepcopy(self.store.get(key, default))

        async def put(key, value):
            self.store[key] = copy.deepcopy(value)

        self.owner = SimpleNamespace(
            config={},
            logger=logging.getLogger("idle-test"),
            get_kv_data=AsyncMock(side_effect=get),
            put_kv_data=AsyncMock(side_effect=put),
            context=SimpleNamespace(
                platform_manager=SimpleNamespace(platform_insts=[self.platform]),
                conversation_manager=self.manager,
                get_config=lambda **_: {"agent_runner": {"runner_type": "local"}},
                get_registered_star=lambda _: SimpleNamespace(
                    star_cls=SimpleNamespace(group_chat_context=self.group_context)
                ),
            ),
        )
        self.enabled = patch.object(
            SessionPluginManager,
            "is_plugin_enabled_for_session",
            AsyncMock(return_value=True),
        )
        self.session_enabled = self.enabled.start()
        self.idle = module.IdleConversation(self.owner)
        await self.idle.initialize()

    async def asyncTearDown(self):
        await self.idle.close()
        self.enabled.stop()
        self.clock.stop()

    def request(self, event, cid="old"):
        conv = SimpleNamespace(
            cid=cid,
            persona_id="original-persona",
            history='[{"role":"user","content":"old text"}]',
        )
        self.saved[cid] = conv
        self.current[event.unified_msg_origin] = cid
        return ProviderRequest(
            prompt="current question",
            contexts=[{"role": "user", "content": "old text"}],
            conversation=conv,
            session_id=event.unified_msg_origin,
            system_prompt="unchanged persona",
        )

    async def test_first_install_starts_observation_without_guessing_old_send_times(
        self,
    ):
        event = Event()
        req = self.request(event)
        self.now += 8 * 3600 - 1
        await self.idle.before_request(event, req)
        self.manager.new_conversation.assert_not_awaited()
        self.assertEqual(req.conversation.cid, "old")

    async def test_idle_rotation_preserves_old_history_persona_current_images_quotes_and_memory(
        self,
    ):
        event = Event()
        req = self.request(event)
        original = req.conversation
        images = ["offline://current-image"]
        quote = TextPart(text="<Quoted Message>explicit quote</Quoted Message>")
        memory = TextPart(
            text="<RAG-Faiss-Memory>remembered background</RAG-Faiss-Memory>"
        ).mark_as_temp()
        req.image_urls = images
        req.extra_user_content_parts = [
            TextPart(
                text=GROUP_HISTORY_HEADER + "old background" + GROUP_HISTORY_FOOTER
            ),
            quote,
            memory,
        ]
        self.now += 8 * 3600
        await self.idle.before_request(event, req)
        self.assertNotEqual(req.conversation.cid, "old")
        self.assertIs(self.saved["old"], original)
        self.assertIn("old text", original.history)
        self.assertEqual(req.contexts, [])
        self.assertEqual(req.prompt, "current question")
        self.assertIs(req.image_urls, images)
        self.assertEqual(req.system_prompt, "unchanged persona")
        self.assertEqual(req.conversation.persona_id, "original-persona")
        self.assertEqual(req.session_id, event.unified_msg_origin)
        self.assertEqual(req.extra_user_content_parts, [quote, memory])
        self.assertIsNone(event.get_extra("_group_context_record_id"))
        self.assertEqual(event.get_extra("_group_context_raw_idx"), -1)
        self.group_context.on_req_llm.assert_awaited_once()

    async def test_successful_send_updates_clock_and_preserves_original_arguments_and_return(
        self,
    ):
        self.now += 8 * 3600
        payload = [{"type": "image", "data": {"file": "offline"}}]
        result = await self.bot.call_action(
            "send_group_msg", group_id=123, message=payload
        )
        self.assertIs(result, self.original_send.return_value)
        self.original_send.assert_awaited_once_with(
            "send_group_msg", group_id=123, message=payload
        )
        req = self.request(Event())
        await self.idle.before_request(Event(), req)
        self.manager.new_conversation.assert_not_awaited()
        self.assertEqual(
            self.store[module.STORE_KEY]["last_sent"]["offline:123"], self.now
        )

    async def test_failed_sends_no_receipt_private_and_reactions_do_not_update_clock(
        self,
    ):
        self.original_send.side_effect = RuntimeError("offline rejected")
        with self.assertRaises(RuntimeError):
            await self.bot.call_action("send_group_msg", group_id=123, message="text")
        self.original_send.side_effect = None
        for response in [
            None,
            {},
            {"retcode": 1200},
            {"message_id": 42, "retcode": 1200},
            {"message_id": 42, "status": "failed"},
        ]:
            self.original_send.return_value = response
            await self.bot.call_action("send_group_msg", group_id=123, message="text")
        self.original_send.return_value = {"message_id": 42}
        await self.bot.call_action("send_private_msg", user_id=123, message="text")
        await self.bot.call_action("set_msg_emoji_like", group_id=123, message_id=42)
        await self.bot.call_action("get_group_member_info", group_id=123)
        self.assertEqual(self.idle.state["last_sent"], {})

    async def test_forward_and_generic_group_sends_are_counted(self):
        for action in ["send_group_forward_msg", "send_msg"]:
            self.now += 1
            await self.bot.call_action(
                action, group_id=123, message_type="group", message="offline"
            )
            self.assertEqual(self.idle.state["last_sent"]["offline:123"], self.now)

    async def test_group_clocks_are_independent_and_regular_user_activity_does_not_extend_them(
        self,
    ):
        self.now += 8 * 3600
        await self.bot.call_action("send_group_msg", group_id=456, message="offline")
        first, second = Event("123"), Event("456")
        req1, req2 = self.request(first, "old1"), self.request(second, "old2")
        await self.idle.before_request(first, req1)
        await self.idle.before_request(second, req2)
        self.assertNotEqual(req1.conversation.cid, "old1")
        self.assertEqual(req2.conversation.cid, "old2")

    async def test_reload_restores_observer_and_keeps_durable_idle_time(self):
        self.now += 100
        await self.bot.call_action("send_group_msg", group_id=123, message="offline")
        await self.idle.close()
        self.assertIs(self.bot.call_action, self.original_send)
        self.idle = module.IdleConversation(self.owner)
        self.now += 8 * 3600
        await self.idle.initialize()
        req = self.request(Event())
        await self.idle.before_request(Event(), req)
        self.assertNotEqual(req.conversation.cid, "old")

    async def test_persistence_failure_cannot_turn_successful_send_into_failure(self):
        self.owner.put_kv_data.side_effect = RuntimeError("private data")
        with self.assertLogs("idle-test", level="WARNING") as logs:
            result = await self.bot.call_action(
                "send_group_msg", group_id=123, message="offline"
            )
        self.assertEqual(result, {"message_id": 42})
        self.assertNotIn("private data", str(logs.output))

    async def test_same_event_or_queued_request_does_not_create_repeated_empty_conversations(
        self,
    ):
        event = Event()
        req = self.request(event)
        second = self.request(Event())
        self.now += 8 * 3600
        await asyncio.gather(
            self.idle.before_request(event, req),
            self.idle.before_request(Event(), second),
        )
        await self.idle.before_request(event, req)
        self.manager.new_conversation.assert_awaited_once()
        later = Event()
        req2 = ProviderRequest(
            prompt="next", conversation=req.conversation, contexts=[]
        )
        self.now += 1
        await self.idle.before_request(later, req2)
        self.manager.new_conversation.assert_awaited_once()

    async def test_current_conversation_must_match_before_switching(self):
        event = Event()
        req = self.request(event)
        self.current[event.unified_msg_origin] = "manually-selected"
        self.now += 8 * 3600
        await self.idle.before_request(event, req)
        self.manager.new_conversation.assert_not_awaited()

    async def test_failed_fresh_lookup_restores_old_selection_without_deleting_history(
        self,
    ):
        event = Event()
        req = self.request(event)
        self.manager.get_conversation.side_effect = RuntimeError(
            "offline lookup failure"
        )
        self.now += 8 * 3600
        with self.assertRaises(RuntimeError):
            await self.idle.before_request(event, req)
        self.assertEqual(self.current[event.unified_msg_origin], "old")
        self.assertEqual(req.conversation.cid, "old")
        self.assertIn("old text", req.contexts[0]["content"])

    async def test_manually_new_empty_conversation_is_not_replaced(self):
        event = Event()
        req = self.request(event)
        req.contexts = []
        req.conversation.history = "[]"
        self.now += 8 * 3600
        await self.idle.before_request(event, req)
        self.manager.new_conversation.assert_not_awaited()
        self.assertEqual(
            self.idle.state["last_reset"][event.unified_msg_origin], self.now
        )

    async def test_disabled_session_private_message_remote_runner_and_disabled_setting_are_untouched(
        self,
    ):
        event = Event()
        req = self.request(event)
        self.now += 8 * 3600
        self.session_enabled.return_value = False
        await self.idle.before_request(event, req)
        self.session_enabled.return_value = True
        self.owner.context.get_config = lambda **_: {
            "agent_runner": {"runner_type": "remote"}
        }
        await self.idle.before_request(event, req)
        self.owner.context.get_config = lambda **_: {}
        event.group = ""
        await self.idle.before_request(event, req)
        event.group = "123"
        self.owner.config["enable_idle_new_conversation"] = False
        await self.idle.before_request(event, req)
        self.manager.new_conversation.assert_not_awaited()

    async def test_custom_hours_and_backward_clock_are_safe(self):
        event = Event()
        req = self.request(event)
        self.owner.config["idle_new_conversation_hours"] = 2
        self.now -= 1
        await self.idle.before_request(event, req)
        self.now += 2 * 3600
        await self.idle.before_request(event, req)
        self.manager.new_conversation.assert_not_awaited()
        self.now += 1
        await self.idle.before_request(event, req)
        self.manager.new_conversation.assert_awaited_once()

    async def test_conversation_keyed_session_id_follows_new_conversation(self):
        event = Event()
        req = self.request(event)
        req.session_id = "old"
        self.now += 8 * 3600
        await self.idle.before_request(event, req)
        self.assertEqual(req.session_id, req.conversation.cid)

    async def test_binding_is_idempotent_and_unload_does_not_overwrite_later_wrappers(
        self,
    ):
        installed = self.bot.call_action
        self.idle.bind_clients()
        self.assertIs(self.bot.call_action, installed)
        later = AsyncMock()
        self.bot.call_action = later
        await self.idle.close()
        self.assertIs(self.bot.call_action, later)


if __name__ == "__main__":
    unittest.main()
