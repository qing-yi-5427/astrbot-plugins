"""Offline deadline tests; no actual provider or QQ connections."""

import asyncio
import logging
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from astrbot_plugin_qing_chat.features.conversation import chat_budget as module


class Event:
    def __init__(self):
        self.unified_msg_origin = "offline:group:1"
        self.extra = {}
        self.sent = []
        self.notified = asyncio.Event()
        self.stopped = False
        self.result = None

    def get_extra(self, key):
        return self.extra.get(key)

    def set_extra(self, key, value):
        self.extra[key] = value

    def is_stopped(self):
        return self.stopped

    def stop_event(self):
        self.stopped = True

    def plain_result(self, text):
        return text

    async def send(self, text):
        self.sent.append(text)
        self.notified.set()

    def get_result(self):
        return self.result

    def clear_result(self):
        self.result = None


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.event = Event()
        self.stopped = asyncio.Event()
        self.runner = SimpleNamespace(
            run_context=SimpleNamespace(
                context=SimpleNamespace(event=self.event), messages=[]
            ),
            done=lambda: False,
            request_stop=self.stopped.set,
            request_max_retries=5,
            provider=SimpleNamespace(client=SimpleNamespace(max_retries=2)),
            follow_up=lambda *, message_text: message_text,
        )
        self.original_follow_up = self.runner.follow_up
        registry = ModuleType("astrbot.core.pipeline.process_stage.follow_up")
        registry._ACTIVE_AGENT_RUNNERS = {self.event.unified_msg_origin: self.runner}
        sessions = ModuleType("astrbot.core.star.session_plugin_manager")
        self.enabled = AsyncMock(return_value=True)
        sessions.SessionPluginManager = SimpleNamespace(
            is_plugin_enabled_for_session=self.enabled
        )
        messages = ModuleType("astrbot.core.agent.message")
        messages.Message = lambda **kwargs: SimpleNamespace(**kwargs)
        messages.TextPart = lambda text: SimpleNamespace(text=text)
        self.modules = patch.dict(
            sys.modules,
            {
                registry.__name__: registry,
                sessions.__name__: sessions,
                messages.__name__: messages,
            },
        )
        self.modules.start()
        self.time = patch.object(module, "bounded_int", return_value=0.03)
        self.time.start()
        self.config = {"enable_chat_budget": True, "chat_budget_seconds": 180}
        self.budget = module.ChatBudget(self.config, logging.getLogger("budget-test"))

    async def asyncTearDown(self):
        await self.budget.close()
        self.time.stop()
        self.modules.stop()

    async def start(self):
        await self.budget.start(self.event, self.runner.run_context)
        return self.event.get_extra(module.STATE_KEY)

    async def test_timeout_reports_once_and_does_not_change_retry_settings(self):
        state = await self.start()
        await asyncio.wait_for(self.event.notified.wait(), 1)
        self.assertTrue(self.stopped.is_set())
        self.assertTrue(state.expired)
        self.assertEqual(len(self.event.sent), 1)
        self.assertIn("不含排队时间", self.event.sent[0])
        self.assertIn("无法据此确定唯一根因", self.event.sent[0])
        self.assertEqual(self.runner.request_max_retries, 5)
        self.assertEqual(self.runner.provider.client.max_retries, 2)
        self.budget.complete(self.event)
        await state.timer

    async def test_queue_time_is_not_counted_and_normal_completion_is_silent(self):
        await asyncio.sleep(0.05)
        self.assertEqual(self.event.sent, [])
        state = await self.start()
        self.budget.complete(self.event)
        await asyncio.gather(state.timer, return_exceptions=True)
        await asyncio.sleep(0.04)
        self.assertEqual(self.event.sent, [])
        self.assertFalse(self.stopped.is_set())

    async def test_tool_timeout_reports_uncertain_side_effects_without_replaying(self):
        state = await self.start()
        tool = SimpleNamespace(name="astrbot_execute_shell")
        self.budget.tool_started(self.event, tool)
        await asyncio.wait_for(self.event.notified.wait(), 1)
        self.assertIn(tool.name, self.event.sent[0])
        self.assertIn("取消等待不代表外部操作未执行", self.event.sent[0])
        self.assertIn("不会自动重跑", self.event.sent[0])
        self.budget.complete(self.event)
        await state.timer

    async def test_tool_return_does_not_restart_budget(self):
        state = await self.start()
        await asyncio.sleep(0.02)
        self.budget.tool_started(self.event, SimpleNamespace(name="read"))
        self.budget.tool_done(self.event)
        await asyncio.wait_for(self.event.notified.wait(), 0.025)
        self.assertIn("根据工具结果继续回复", self.event.sent[0])
        self.budget.complete(self.event)
        await state.timer

    async def test_first_tool_raises_total_deadline_without_resetting_elapsed_time(
        self,
    ):
        with patch.object(module, "bounded_int", side_effect=[0.03, 0.10]):
            state = await self.start()
        started_at = state.started_at
        await asyncio.sleep(0.02)
        self.budget.tool_started(self.event, SimpleNamespace(name="read"))
        self.budget.tool_done(self.event)
        await asyncio.sleep(0.03)
        self.assertEqual(self.event.sent, [])
        self.budget.tool_started(self.event, SimpleNamespace(name="search"))
        self.budget.tool_done(self.event)
        self.assertEqual(state.started_at, started_at)
        self.assertEqual(state.seconds, 0.10)
        await asyncio.wait_for(self.event.notified.wait(), 0.075)
        self.assertIn("工具任务", self.event.sent[0])
        self.budget.complete(self.event)
        await state.timer

    async def test_tool_budget_cannot_shorten_chat_budget(self):
        with patch.object(module, "bounded_int", side_effect=[0.10, 0.03]):
            state = await self.start()
        self.budget.tool_started(self.event, SimpleNamespace(name="read"))
        self.assertEqual(state.seconds, 0.10)
        self.budget.complete(self.event)

    async def test_new_wake_requests_decline_capture_and_restore_native_method(self):
        state = await self.start()
        self.assertIsNone(self.runner.follow_up(message_text="/aa new question"))
        self.assertEqual(state.seconds, 0.03)
        self.budget.complete(self.event)
        self.assertIs(self.runner.follow_up, self.original_follow_up)

    async def test_queue_option_can_preserve_native_follow_up_capture(self):
        self.config["queue_new_requests"] = False
        await self.start()
        self.assertIs(self.runner.follow_up, self.original_follow_up)
        self.assertEqual(
            self.runner.follow_up(message_text="clarification"), "clarification"
        )
        self.budget.complete(self.event)

    async def test_timeout_discards_only_buffered_llm_results(self):
        state = await self.start()
        llm_result = SimpleNamespace(is_llm_result=lambda: True)
        self.event.result = llm_result
        self.budget.discard_expired_result(self.event)
        self.assertIs(self.event.result, llm_result)
        state.expired = True
        self.budget.discard_expired_result(self.event)
        self.assertIsNone(self.event.result)
        tool_result = SimpleNamespace(is_llm_result=lambda: False)
        self.event.result = tool_result
        self.budget.discard_expired_result(self.event)
        self.assertIs(self.event.result, tool_result)
        self.assertFalse(self.event.stopped)

    async def test_explicit_image_budget_is_exempt(self):
        self.event.set_extra("imagegen_execution_budget", 448)
        await self.start()
        state = self.event.get_extra(module.STATE_KEY)
        self.assertTrue(state.exempt)
        self.assertIsNone(state.timer)
        self.assertIsNone(self.runner.follow_up(message_text="/aa new question"))
        await asyncio.sleep(0.05)
        self.assertEqual(self.event.sent, [])

    async def test_image_tool_exempts_already_started_budget(self):
        state = await self.start()
        self.budget.tool_started(
            self.event, SimpleNamespace(name="astrbot_generate_image")
        )
        await asyncio.sleep(0.05)
        self.assertTrue(state.exempt)
        self.assertEqual(self.event.sent, [])
        self.assertFalse(self.stopped.is_set())
        self.assertIn(id(self.event), self.budget.turns)
        await self.budget.close()
        self.assertIs(self.runner.follow_up, self.original_follow_up)

    async def test_user_stop_and_reload_do_not_send_false_timeout(self):
        state = await self.start()
        self.event.set_extra("agent_stop_requested", True)
        await state.timer
        self.assertEqual(self.event.sent, [])
        self.assertFalse(self.stopped.is_set())

    async def test_disabled_session_and_unrelated_context_are_untouched(self):
        self.enabled.return_value = False
        self.assertIsNone(await self.start())
        self.enabled.return_value = True
        other = SimpleNamespace(context=SimpleNamespace(event=self.event), messages=[])
        await self.budget.start(self.event, other)
        self.assertIsNone(self.event.get_extra(module.STATE_KEY))

    async def test_reload_cleans_watchers_without_stopping_running_turn(self):
        state = await self.start()
        await self.budget.close()
        self.assertTrue(state.timer.done())
        self.assertEqual(self.budget.turns, {})
        self.assertIs(self.runner.follow_up, self.original_follow_up)
        self.assertFalse(self.stopped.is_set())
        self.assertEqual(self.event.sent, [])

    async def test_notice_failure_is_safe_and_does_not_retry(self):
        self.event.send = AsyncMock(
            side_effect=RuntimeError("secret must not be logged")
        )

        def stopped():
            self.stopped.set()
            self.budget.complete(self.event)

        self.runner.request_stop = stopped
        with self.assertLogs("budget-test", level="WARNING") as records:
            state = await self.start()
            await state.timer
        self.event.send.assert_awaited_once()
        self.assertNotIn("secret", " ".join(records.output))
        self.assertTrue(self.stopped.is_set())

    async def test_unresponsive_hook_cancels_only_its_owner_after_grace(self):
        ready = asyncio.Event()

        async def owner():
            await self.start()
            ready.set()
            await asyncio.Event().wait()

        with patch.object(module, "STOP_GRACE_SECONDS", 0.01):
            task = asyncio.create_task(owner())
            await ready.wait()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
        self.assertEqual(len(self.event.sent), 1)
        self.assertTrue(self.event.stopped)
        self.assertTrue(self.stopped.is_set())

    async def test_history_records_timeout_not_a_fictitious_user_cancel(self):
        state = await self.start()
        state.expired, state.notice = True, "timeout notice"
        self.runner.USER_INTERRUPTION_REQUEST = "native-user-cancel"
        self.runner.USER_INTERRUPTION_MESSAGE = "native-cancelled"
        self.runner.was_aborted = lambda: True
        original = SimpleNamespace(
            role="user", content=[SimpleNamespace(text="real question")]
        )
        self.runner.run_context.messages = [original] + [
            SimpleNamespace(role=role, content=[SimpleNamespace(text=text)])
            for role, text in (
                ("user", "native-user-cancel"),
                ("assistant", "native-cancelled"),
            )
        ]
        response = SimpleNamespace(
            completion_text="stale", reasoning_content="stale", result_chain="stale"
        )
        self.budget.response(self.event, response)
        self.assertIs(self.runner.run_context.messages[0], original)
        self.assertEqual(self.runner.run_context.messages[-2].role, "system")
        self.assertEqual(
            self.runner.run_context.messages[-1].content[0].text, "timeout notice"
        )
        self.assertEqual(response.completion_text, "timeout notice")
        self.assertIsNone(response.result_chain)


if __name__ == "__main__":
    unittest.main()
