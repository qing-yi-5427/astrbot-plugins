from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.pipeline.result_decorate.stage import ResultDecorateStage
from astrbot.core.utils.t2i.network_strategy import NetworkRenderStrategy

from .bridge import FooterBridge, record_response


class ModelFooterPlugin(star.Star):
    def __init__(self, context):
        super().__init__(context)
        self.bridge = FooterBridge()

    async def initialize(self):
        self.bridge.install(ResultDecorateStage, NetworkRenderStrategy, ToolLoopAgentRunner)
        self.logger.info("Model footer enabled: per-reply model metadata; no default-provider guessing")

    @filter.on_llm_response(priority=-10000)
    async def remember_response_model(self, event: AstrMessageEvent, response: LLMResponse):
        # Also covers non-tool-loop agents when their raw response names a model.
        record_response(event, response)

    async def terminate(self):
        self.bridge.uninstall()
