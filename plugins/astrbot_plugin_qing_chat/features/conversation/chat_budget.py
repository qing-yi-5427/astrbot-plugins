"""Per-Agent wall-clock budget using native stop signals, not provider mutation."""

import asyncio
from dataclasses import dataclass, field

from .tool_history import bounded_int

STATE_KEY = "tool_history_chat_budget"
NOTICE_SEND_TIMEOUT = 10
STOP_GRACE_SECONDS = 5


@dataclass
class TurnBudget:
    event: object
    runner: object
    owner: asyncio.Task
    seconds: int
    tool_seconds: int
    started_at: float
    phase: str = "model"
    tool_used: bool = False
    tool_pending: bool = False
    expired: bool = False
    exempt: bool = False
    notice: str = ""
    timer: asyncio.Task | None = None
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    deadline_changed: asyncio.Event = field(default_factory=asyncio.Event)
    follow_up_original: object = None
    follow_up_wrapper: object = None

    def restore_follow_up(self):
        """Restore only this runner's temporary queue policy, if still owned."""
        if self.follow_up_wrapper is not None and (
            self.runner.follow_up is self.follow_up_wrapper
        ):
            self.runner.follow_up = self.follow_up_original


class ChatBudget:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.active = True
        self.turns = {}

    async def start(self, event, run_context):
        """Start after session-lock acquisition and native Agent registration.

        Args:
            event: Current message, never another event sharing its session.
            run_context: This Agent's original context wrapper.
        """
        if not self.active or not self.config.get("enable_chat_budget", True):
            return
        if event.get_extra(STATE_KEY) is not None or event.is_stopped():
            return
        from astrbot.core.pipeline.process_stage.follow_up import _ACTIVE_AGENT_RUNNERS
        from astrbot.core.star.session_plugin_manager import SessionPluginManager

        if not await SessionPluginManager.is_plugin_enabled_for_session(
            event.unified_msg_origin, "tavily_result_limiter"
        ):
            return
        runner = _ACTIVE_AGENT_RUNNERS.get(event.unified_msg_origin)
        if (
            runner is None
            or runner.run_context is not run_context
            or getattr(run_context.context, "event", None) is not event
            or not callable(getattr(runner, "request_stop", None))
        ):
            self.logger.warning(
                "Chat budget skipped: incompatible or unrelated local runner"
            )
            return
        seconds = bounded_int(self.config, "chat_budget_seconds", 180, 30, 900)
        tool_seconds = max(
            seconds, bounded_int(self.config, "tool_budget_seconds", 600, 60, 1800)
        )
        state = TurnBudget(
            event,
            runner,
            asyncio.current_task(),
            seconds,
            tool_seconds,
            asyncio.get_running_loop().time(),
        )
        event.set_extra(STATE_KEY, state)
        self.turns[id(event)] = state

        # Declining native follow-up capture preserves the existing session lock:
        # each newly awakened message gets its own turn and its own deadline.
        original = getattr(runner, "follow_up", None)
        if self.config.get("queue_new_requests", True) and callable(original):

            def queue_next_request(*, message_text):
                if self.active and not state.finished.is_set():
                    self.logger.info(
                        "New wake request will use the native session queue; current deadline unchanged"
                    )
                    return None
                return original(message_text=message_text)

            state.follow_up_original = original
            state.follow_up_wrapper = queue_next_request
            runner.follow_up = queue_next_request
            # A ticket captured between runner registration and this begin hook
            # must resume in the queue rather than be consumed by the old turn.
            resolve_pending = getattr(runner, "_resolve_unconsumed_follow_ups", None)
            if callable(resolve_pending):
                resolve_pending()

        # Image generation keeps its own deadline while still declining capture
        # of unrelated newly awakened questions into the image-generation turn.
        state.exempt = bool(
            event.get_extra("imagegen_execution_budget")
            or event.get_extra("imagegen_call_guard_active")
        )
        if not state.exempt:
            state.timer = asyncio.create_task(self._watch(state))
            self.logger.info("Chat budget started: %ss; queue time excluded", seconds)

        def owner_done(_):
            self.complete(event)

        state.owner.add_done_callback(owner_done)

    async def _watch(self, state):
        """Stop expired work, report a bounded explanation and prevent orphan work.

        Args:
            state: Exactly one active Agent turn and its owning pipeline task.
        """
        try:
            loop = asyncio.get_running_loop()
            while (remaining := state.started_at + state.seconds - loop.time()) > 0:
                try:
                    await asyncio.wait_for(
                        state.deadline_changed.wait(), timeout=remaining
                    )
                except asyncio.TimeoutError:
                    pass
                state.deadline_changed.clear()
            event, runner = state.event, state.runner
            if (
                not self.active
                or state.exempt
                or state.finished.is_set()
                or state.owner.done()
                or getattr(state.owner, "cancelling", lambda: 0)()
                or event.is_stopped()
                or event.get_extra("agent_stop_requested")
                or runner.done()
            ):
                return
            state.expired = True
            reason = (
                "工具执行尚未确认结束，取消等待不代表外部操作未执行，请先核对结果。"
                if state.tool_pending
                else "本轮未在预算内得到最终回复，可能是上游处理慢或请求较大，无法据此确定唯一根因。"
            )
            phase = (
                f"执行工具 {state.phase.removeprefix('tool:')}"
                if state.tool_pending
                else {
                    "model": "等待模型响应（含上下文处理和原有重试）",
                    "model_after_tool": "等待模型根据工具结果继续回复",
                }.get(state.phase, "等待本轮回复")
            )
            state.notice = (
                f"本轮{'工具任务' if state.tool_used else '普通聊天'}处理已达到 {state.seconds} 秒上限"
                "（不含排队时间），已停止继续等待。"
                f"\n超时阶段：{phase}。\n{reason}"
                "\n不会自动重跑整轮对话或重复执行已调用的工具。"
            )
            # Native stop cancels the actual provider future/tool iterator and
            # retains native tool-interruption results and history finalization.
            event.set_extra("agent_stop_requested", True)
            runner.request_stop()
            self.logger.warning(
                "Chat budget expired after %ss at %s", state.seconds, state.phase
            )
            try:
                await asyncio.wait_for(
                    event.send(event.plain_result(state.notice)),
                    timeout=NOTICE_SEND_TIMEOUT,
                )
            except Exception as exc:  # noqa: BLE001 - Optional notice must not restart work.
                self.logger.warning(
                    "Chat timeout notice could not be delivered (%s)",
                    type(exc).__name__,
                )
            try:
                await asyncio.wait_for(
                    state.finished.wait(), timeout=STOP_GRACE_SECONDS
                )
            except asyncio.TimeoutError:
                # A broken third-party hook must not leave a stopped turn running.
                event.stop_event()
                state.owner.cancel()
                self.logger.warning(
                    "Stopped Agent did not finalize; cancelled only its owner task"
                )
        finally:
            if not state.exempt or state.finished.is_set() or not self.active:
                self.turns.pop(id(state.event), None)

    def tool_started(self, event, tool):
        state = event.get_extra(STATE_KEY)
        if (
            not isinstance(state, TurnBudget)
            or state.finished.is_set()
            or state.expired
        ):
            return
        name = getattr(tool, "name", "unknown")
        if name == "astrbot_generate_image":
            state.exempt = True
            if state.timer is not None:
                state.timer.cancel()
            return
        if not state.tool_used:
            state.tool_used = True
            state.seconds = state.tool_seconds
            state.deadline_changed.set()
            self.logger.info(
                "Tool task budget: %ss total from original Agent start; elapsed time retained",
                state.seconds,
            )
        state.phase = f"tool:{name}"
        state.tool_pending = True

    def tool_done(self, event):
        state = event.get_extra(STATE_KEY)
        if isinstance(state, TurnBudget) and not state.expired:
            state.phase = "model_after_tool"
            state.tool_pending = False

    def response(self, event, response):
        """Record a truthful deadline result instead of a fictitious user cancel.

        Args:
            event: Original message associated with this turn.
            response: Native final response, including the native abort response.
        """
        state = event.get_extra(STATE_KEY)
        if not isinstance(state, TurnBudget) or not state.expired:
            return
        from astrbot.core.agent.message import Message, TextPart

        runner = state.runner
        messages = runner.run_context.messages
        if runner.was_aborted() and len(messages) >= 2:
            prior = messages[-2:]
            texts = [
                "".join(getattr(p, "text", "") for p in m.content or []) for m in prior
            ]
            if texts == [
                runner.USER_INTERRUPTION_REQUEST,
                runner.USER_INTERRUPTION_MESSAGE,
            ]:
                messages[-2:] = [
                    Message(
                        role="system",
                        content=[
                            TextPart(
                                text="[系统：本轮达到插件处理预算，由插件停止，不是用户取消。]"
                            )
                        ],
                    ),
                    Message(role="assistant", content=[TextPart(text=state.notice)]),
                ]
        if response is not None:
            response.completion_text = state.notice
            response.reasoning_content = ""
            response.result_chain = None

    def discard_expired_result(self, event):
        """Suppress native buffered preambles after the explicit timeout notice.

        Args:
            event: Original event being decorated for an ordinary LLM reply.
        """
        state = event.get_extra(STATE_KEY)
        result = event.get_result()
        if (
            isinstance(state, TurnBudget)
            and state.expired
            and result is not None
            and result.is_llm_result()
        ):
            event.clear_result()
            self.logger.info("Discarded buffered LLM output after budget expiration")

    def complete(self, event):
        state = event.get_extra(STATE_KEY)
        if isinstance(state, TurnBudget):
            state.finished.set()
            state.restore_follow_up()
            if not state.expired and state.timer is not None:
                state.timer.cancel()
            if state.timer is None or state.exempt:
                self.turns.pop(id(event), None)

    async def close(self):
        """Remove watchdogs on reload without cancelling users' running Agents."""
        self.active = False
        for state in self.turns.values():
            state.restore_follow_up()
        tasks = [
            state.timer for state in self.turns.values() if state.timer is not None
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.turns.clear()
