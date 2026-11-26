"""Two-suite facade: only this module registers AstrBot handlers and tools."""

from sys import maxsize
from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.command import GreedyStr
from .runtime import SuiteRuntime, safe_filter
from .features.mention.main import MentionLLMPlugin as MentionService
from .features.reaction.main import Main as ReactionService
from .features.conversation.main import TavilyResultLimiter as ConversationService
from .features.context_images.main import ContextImageLimiter as ContextImagesService
from .features.memory_compat.main import MemoryRecallFix as MemoryCompatService
from .features.roll.main import RollPlugin as RollService
from .features.raffle.main import RaffleFollowup, RaffleService

LEGACY = {
    "mention": "astrbot_plugin_mention_llm",
    "reaction": "astrbot_plugin_get_emoji_reply",
    "conversation": "tavily_result_limiter",
    "context_images": "astrbot_plugin_context_image_limiter",
    "memory_compat": "astrbot_plugin_memory_recall_fix",
    "roll": "astrbot_plugin_roll",
}
LEGACY_IDS = {
    "mention": "qingyi/astrbot_plugin_mention_llm",
    "reaction": "local/astrbot_plugin_get_emoji_reply",
    "conversation": "qingyi/tavily_result_limiter",
    "context_images": "qing/astrbot_plugin_context_image_limiter",
    "memory_compat": "qingyi/astrbot_plugin_memory_recall_fix",
    "roll": "developer/astrbot_plugin_roll",
}
TOOL_NAME = "astrbot_generate_image"


@register("astrbot_plugin_qing_chat", "qingyi", "对话管理", "1.12.2")
class Main(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context, config)
        self.config = config
        self.modules = SuiteRuntime(
            self,
            "astrbot_plugin_qing_chat",
            LEGACY,
            LEGACY_IDS,
            {
                "mention": (MentionService, False),
                "reaction": (ReactionService, True),
                "conversation": (ConversationService, True),
                "context_images": (ContextImagesService, True),
                "memory_compat": (MemoryCompatService, True),
                "roll": (RollService, False),
                "raffle": (RaffleService, True),
            },
        )

    async def initialize(self):
        await self.modules.initialize()

    @safe_filter(filter.on_plugin_loaded(priority=maxsize), LEGACY.values())
    async def guard_legacy_install(self, metadata):
        await self.modules.check_conflict()

    async def terminate(self):
        await self.modules.terminate()

    @safe_filter(filter.command("抽奖", alias={"raffle"}), LEGACY.values())
    async def raffle_command(self, event: AstrMessageEvent, command: GreedyStr):
        """群内发起、报名、开奖、取消或查询抽奖。"""
        if RaffleService.in_group(event) and await self.modules.allowed(
            "raffle", event
        ):
            await self.modules.call("raffle", "handle", event, command)

    @safe_filter(filter.command("抽奖管理"), LEGACY.values())
    async def raffle_manage_command(self, event: AstrMessageEvent):
        """领取 QQ 群抽奖管理台的限时入口。"""
        if RaffleService.in_group(event) and await self.modules.allowed("raffle", event):
            await self.modules.call("raffle", "handle", event, "管理")

    @safe_filter(
        filter.custom_filter(RaffleFollowup, priority=100),
        LEGACY.values(),
    )
    async def raffle_mention(self, event: AstrMessageEvent):
        if not RaffleService.in_group(event):
            return
        if await self.modules.allowed("raffle", event):
            await self.modules.call("raffle", "handle_followup", event)

    @safe_filter(filter.on_waiting_llm_request(), LEGACY.values())
    async def mention_mark_explicit_mention(self, event: AstrMessageEvent):
        "Mark only mentions that already passed AstrBot's LLM entry checks.\n\nArgs:\n    event: Current QQ group event after command/session permission checks."
        if not await self.modules.allowed("mention", event):
            return
        return await self.modules.call("mention", "mark_explicit_mention", event)

    @safe_filter(filter.on_llm_request(priority=-(maxsize + 1)), LEGACY.values())
    async def reaction_react_to_llm_request(
        self, event: AstrMessageEvent, _request: ProviderRequest
    ):
        "React only to eligible group messages already entering the LLM.\n\nArgs:\n    event: Current QQ group event.\n    _request: Native LLM request; it is never modified by this plugin."
        if not await self.modules.allowed("reaction", event):
            return
        return await self.modules.call(
            "reaction", "react_to_llm_request", event, _request
        )

    @safe_filter(filter.on_platform_loaded(), LEGACY.values())
    async def conversation_bind_idle_send_observers(self):
        if not await self.modules.allowed("conversation", None):
            return
        return await self.modules.call("conversation", "bind_idle_send_observers")

    @safe_filter(filter.on_llm_request(priority=maxsize), LEGACY.values())
    async def conversation_rotate_idle_group(self, event, req):
        if not await self.modules.allowed("conversation", event):
            return
        return await self.modules.call("conversation", "rotate_idle_group", event, req)

    @safe_filter(filter.on_llm_request(), LEGACY.values())
    async def conversation_clear_tavily_history(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        if not await self.modules.allowed("conversation", event):
            return
        return await self.modules.call(
            "conversation", "clear_tavily_history", event, req
        )

    @safe_filter(filter.on_agent_begin(priority=-(maxsize + 1)), LEGACY.values())
    async def conversation_start_chat_budget(self, event, run_context):
        if not await self.modules.allowed("conversation", event):
            return
        return await self.modules.call(
            "conversation", "start_chat_budget", event, run_context
        )

    @safe_filter(filter.on_using_llm_tool(), LEGACY.values())
    async def conversation_mark_tool_stage(self, event, tool, tool_args):
        if not await self.modules.allowed("conversation", event):
            return
        return await self.modules.call(
            "conversation", "mark_tool_stage", event, tool, tool_args
        )

    @safe_filter(filter.on_llm_tool_respond(), LEGACY.values())
    async def conversation_mark_model_stage(self, event, tool, tool_args, tool_result):
        if not await self.modules.allowed("conversation", event):
            return
        return await self.modules.call(
            "conversation", "mark_model_stage", event, tool, tool_args, tool_result
        )

    @safe_filter(filter.on_llm_response(priority=maxsize), LEGACY.values())
    async def conversation_explain_chat_timeout(self, event, response):
        if not await self.modules.allowed("conversation", event):
            return
        return await self.modules.call(
            "conversation", "explain_chat_timeout", event, response
        )

    @safe_filter(filter.on_agent_done(), LEGACY.values())
    async def conversation_finish_chat_budget(self, event, run_context, response):
        if not await self.modules.allowed("conversation", event):
            return
        return await self.modules.call(
            "conversation", "finish_chat_budget", event, run_context, response
        )

    @safe_filter(filter.on_decorating_result(priority=maxsize), LEGACY.values())
    async def conversation_discard_expired_buffer(self, event):
        if not await self.modules.allowed("conversation", event):
            return
        return await self.modules.call("conversation", "discard_expired_buffer", event)

    @safe_filter(filter.on_llm_request(priority=-10000), LEGACY.values())
    async def context_images_limit_history(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        if not await self.modules.allowed("context_images", event):
            return
        return await self.modules.call("context_images", "limit_history", event, req)

    @safe_filter(filter.on_agent_begin(priority=-10000), LEGACY.values())
    async def context_images_limit_assembled_context(
        self, event: AstrMessageEvent, run_context
    ):
        if not await self.modules.allowed("context_images", event):
            return
        return await self.modules.call(
            "context_images", "limit_assembled_context", event, run_context
        )

    @safe_filter(filter.on_agent_done(priority=-10000), LEGACY.values())
    async def context_images_limit_saved_context(
        self, event: AstrMessageEvent, run_context, response
    ):
        if not await self.modules.allowed("context_images", event):
            return
        return await self.modules.call(
            "context_images", "limit_saved_context", event, run_context, response
        )

    @safe_filter(filter.on_astrbot_loaded(), LEGACY.values())
    async def memory_compat_on_loaded(self):
        if not await self.modules.allowed("memory_compat", None):
            return
        return await self.modules.call("memory_compat", "on_loaded")

    @safe_filter(filter.on_llm_request(priority=10000), LEGACY.values())
    async def memory_compat_ensure_current_memory_version(self, event, req):
        if not await self.modules.allowed("memory_compat", event):
            return
        return await self.modules.call(
            "memory_compat", "ensure_current_memory_version", event, req
        )

    @safe_filter(filter.command("memory_fix_status"), LEGACY.values())
    async def memory_compat_status(self, event: AstrMessageEvent):
        if not await self.modules.allowed("memory_compat", event):
            return
        async for result in self.modules.iterate("memory_compat", "status", event):
            yield result

    @safe_filter(filter.command("roll"), LEGACY.values())
    async def roll_roll(self, event: AstrMessageEvent):
        "随机选择一个选项，用法：/roll 选项1 选项2 选项3... 或 /roll a-b"
        if not await self.modules.allowed("roll", event):
            return
        async for result in self.modules.iterate("roll", "roll", event):
            yield result
