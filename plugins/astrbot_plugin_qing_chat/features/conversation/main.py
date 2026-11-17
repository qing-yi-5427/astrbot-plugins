from ...runtime import FeatureService, event_from

"""Bound Tavily and AnySearch tool output without changing conversation turn retention."""

import json

from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from astrbot.core.tools import web_search_tools
from astrbot.core.tools.web_search_tools import (
    TavilyExtractWebPageTool,
    TavilyWebSearchTool,
)

from .chat_budget import ChatBudget
from .idle_conversation import IdleConversation
from .tool_history import compact_tool_history

DEFAULT_MAX_RESULTS = 10
TAVILY_API_MAX_RESULTS = 20
ANYSEARCH_API_MAX_RESULTS = 10
MAX_SNIPPET_CHARS = 1000
MAX_EXTRACT_CHARS = 6000
TAVILY_TOOL_NAMES = {"web_search_tavily", "tavily_extract_web_page"}
SEARCH_TOOL_NAMES = TAVILY_TOOL_NAMES | {"web_search_anysearch"}
CLEARED_CONTENT = "[历史搜索结果已自动清理；如需原网页内容，请重新搜索。]"


def clear_previous_tavily_results(contexts: list[dict]) -> tuple[int, int]:
    """Compact tool replies without leaving unmatched assistant tool calls."""
    tavily_call_ids = {
        call.get("id")
        for message in contexts
        if isinstance(message, dict) and message.get("role") == "assistant"
        for call in (message.get("tool_calls") or [])
        if isinstance(call, dict)
        and isinstance(call.get("function"), dict)
        and call["function"].get("name") in SEARCH_TOOL_NAMES
        and isinstance(call.get("id"), str)
    }
    count = 0
    removed_chars = 0
    for message in contexts:
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        if message.get("tool_call_id") not in tavily_call_ids:
            continue
        content = message.get("content")
        if not isinstance(content, str) or content == CLEARED_CONTENT:
            continue
        removed_chars += len(content) - len(CLEARED_CONTENT)
        message["content"] = CLEARED_CONTENT
        count += 1
    return count, max(removed_chars, 0)


class TavilyResultLimiter(FeatureService):
    def __init__(self, context, config=None) -> None:
        super().__init__(context, config)
        self.config = config if config is not None else {}
        self._patches = []
        self._active = False
        self._chat_budget = ChatBudget(self.config, self.logger)
        self._idle_conversation = IdleConversation(self)

    def _max_results(self) -> int:
        try:
            configured = int(self.config.get("max_results", DEFAULT_MAX_RESULTS))
        except (TypeError, ValueError, OverflowError):
            configured = DEFAULT_MAX_RESULTS
        return min(max(configured, 1), TAVILY_API_MAX_RESULTS)

    def _search_wrapper(self, original, api_limit):
        async def limited_search(tool, context, **kwargs):
            if not self._active or not await self.is_event_enabled(event_from(context)):
                return await original(tool, context, **kwargs)
            configured_limit = min(self._max_results(), api_limit)
            try:
                requested = int(kwargs.get("max_results", configured_limit))
            except (TypeError, ValueError, OverflowError):
                requested = configured_limit
            limit = min(max(requested, 1), configured_limit)
            kwargs["max_results"] = limit
            output = await original(tool, context, **kwargs)
            if not isinstance(output, str):
                return output
            try:
                payload = json.loads(output)
            except (TypeError, ValueError):
                return output
            if not isinstance(payload, dict) or not isinstance(
                payload.get("results"), list
            ):
                return output
            results = payload["results"][:limit]
            for result in results:
                if isinstance(result, dict) and isinstance(result.get("snippet"), str):
                    snippet = result["snippet"]
                    if len(snippet) > MAX_SNIPPET_CHARS:
                        result["snippet"] = (
                            snippet[:MAX_SNIPPET_CHARS] + "… [摘要已截断]"
                        )
            payload["results"] = results
            return json.dumps(payload, ensure_ascii=False)

        return limited_search

    async def initialize(self) -> None:
        if self._active:
            return
        if not self._chat_budget.active:
            self._chat_budget = ChatBudget(self.config, self.logger)
        await self._idle_conversation.initialize()
        self._active = True
        search_tools = [(TavilyWebSearchTool, TAVILY_API_MAX_RESULTS)]
        anysearch = getattr(web_search_tools, "AnySearchWebSearchTool", None)
        if anysearch is not None:
            search_tools.append((anysearch, ANYSEARCH_API_MAX_RESULTS))
        else:
            self.logger.info(
                "AnySearch tool unavailable in this AstrBot version; Tavily only"
            )
        for tool_class, api_limit in search_tools:
            original = tool_class.call
            wrapper = self._search_wrapper(original, api_limit)
            self._patches.append((tool_class, original, wrapper))
            tool_class.call = wrapper

        original_extract = TavilyExtractWebPageTool.call

        async def limited_extract(tool, context, **kwargs):
            output = await original_extract(tool, context, **kwargs)
            if (
                not self._active
                or not await self.is_event_enabled(event_from(context))
                or not isinstance(output, str)
                or len(output) <= MAX_EXTRACT_CHARS
            ):
                return output
            return (
                output[:MAX_EXTRACT_CHARS]
                + "\n[网页正文已截断；如需后续内容，请打开原网页]"
            )

        self._patches.append(
            (TavilyExtractWebPageTool, original_extract, limited_extract)
        )
        TavilyExtractWebPageTool.call = limited_extract
        self.logger.info(
            "Search limits active: Tavily=%d, AnySearch=%s results, %d snippet chars, %d extract chars; auto-clear=%s",
            self._max_results(),
            min(self._max_results(), ANYSEARCH_API_MAX_RESULTS)
            if anysearch
            else "unavailable",
            MAX_SNIPPET_CHARS,
            MAX_EXTRACT_CHARS,
            bool(self.config.get("auto_clear_history", True)),
        )

    async def bind_idle_send_observers(self):
        self._idle_conversation.bind_clients()

    async def rotate_idle_group(self, event, req):
        await self._idle_conversation.before_request(event, req)

    async def clear_tavily_history(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        if not isinstance(req.contexts, list):
            return
        req.contexts, compacted, saved = compact_tool_history(req.contexts, self.config)
        if compacted:
            self.logger.info(
                "Compacted %d old execution results (%d chars saved)", compacted, saved
            )
        if not self.config.get("auto_clear_history", True):
            return
        count, removed_chars = clear_previous_tavily_results(req.contexts)
        if count:
            self.logger.info(
                "Cleared %d historical search tool results (%d chars) from LLM context for %s",
                count,
                removed_chars,
                event.unified_msg_origin,
            )

    async def start_chat_budget(self, event, run_context):
        await self._chat_budget.start(event, run_context)

    async def mark_tool_stage(self, event, tool, tool_args):
        self._chat_budget.tool_started(event, tool)

    async def mark_model_stage(self, event, tool, tool_args, tool_result):
        self._chat_budget.tool_done(event)

    async def explain_chat_timeout(self, event, response):
        self._chat_budget.response(event, response)

    async def finish_chat_budget(self, event, run_context, response):
        self._chat_budget.complete(event)

    async def discard_expired_buffer(self, event):
        self._chat_budget.discard_expired_result(event)

    async def terminate(self) -> None:
        self._active = False
        await self._idle_conversation.close()
        await self._chat_budget.close()
        for tool_class, original, wrapper in reversed(self._patches):
            # Do not overwrite a wrapper installed later by another plugin.
            if tool_class.call is wrapper:
                tool_class.call = original
        self._patches.clear()
