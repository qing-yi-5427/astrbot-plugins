"""Limit context image uploads without limiting conversation turns."""

from astrbot.api import AstrBotConfig, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.message import TextPart
from astrbot.core.agent.context.manager import ContextManager

from .limiter import DEFAULT_LIMIT_MB, MB, configured_limit_bytes, trim_context_images

class ContextImageLimiter(star.Star):
    def __init__(self, context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self._active = False
        self._process_hook = None
        self._original_process = None

    def _limit_bytes(self) -> int:
        return configured_limit_bytes(self.config.get("max_total_image_mb", DEFAULT_LIMIT_MB))

    async def initialize(self) -> None:
        if self._active:
            return
        self._active = True
        self._original_process = ContextManager.process
        original_process = self._original_process

        async def process_with_image_budget(manager, messages, *args, **kwargs):
            # v4.28 invokes this for EVERY model step, after current/tool images
            # have been materialized. Leave AstrBot's compaction logic intact.
            if self._active:
                self._trim(messages, "before-context-processing")
            result = await original_process(manager, messages, *args, **kwargs)
            if self._active:
                self._trim(result, "before-model")
            return result

        self._process_hook = process_with_image_budget
        ContextManager.process = process_with_image_budget
        self.logger.info(
            "Context image limiter active: %.3f MB; oldest images first; text/turns unchanged",
            self._limit_bytes() / MB,
        )

    def _trim(self, messages, phase: str) -> None:
        if not isinstance(messages, list):
            return
        limit = self._limit_bytes()
        result = trim_context_images(
            messages, limit, typed_text_factory=lambda text: TextPart(text=text)
        )
        if result.removed_count:
            # Never log URLs, image bytes or chat text.
            self.logger.info(
                "Context image limit [%s]: removed %d oldest image(s); %.3f -> %.3f MB (limit %.3f MB); messages/text retained",
                phase,
                result.removed_count,
                result.before_bytes / MB,
                result.after_bytes / MB,
                limit / MB,
            )

    @filter.on_llm_request(priority=-10000)
    async def limit_history(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        # Covers request hooks, including non-agent plugin requests with history.
        self._trim(req.contexts, "request-history")

    @filter.on_agent_begin(priority=-10000)
    async def limit_assembled_context(self, event: AstrMessageEvent, run_context) -> None:
        # At this point AstrBot has encoded current local/remote input images.
        # Saving uses these same Message models; cleanup persists on normal save.
        self._trim(run_context.messages, "agent-begin")

    @filter.on_agent_done(priority=-10000)
    async def limit_saved_context(self, event: AstrMessageEvent, run_context, response) -> None:
        self._trim(run_context.messages, "agent-done")

    async def terminate(self) -> None:
        self._active = False
        # Do not overwrite another plugin's subsequently installed wrapper.
        # If our adapter is inside its chain it remains dormant, not destructive.
        if ContextManager.process is self._process_hook:
            ContextManager.process = self._original_process
