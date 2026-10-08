import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

try:
    from astrbot.api.provider import LLMResponse
    from astrbot_plugin_model_footer.main import ModelFooterPlugin
    from astrbot.core.pipeline.result_decorate.stage import ResultDecorateStage
    from astrbot.core.utils.t2i.network_strategy import NetworkRenderStrategy
    from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
except ModuleNotFoundError:
    ModelFooterPlugin = None

from astrbot_plugin_model_footer.bridge import MODEL_KEY


@unittest.skipIf(ModelFooterPlugin is None, 'Requires AstrBot runtime')
class AstrBotCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_hook_and_terminate_with_real_astrbot_types(self):
        plugin = ModelFooterPlugin(MagicMock())
        plugin.logger = MagicMock()
        owners = [(ResultDecorateStage, 'process'), (NetworkRenderStrategy, 'render_custom_template'), (ToolLoopAgentRunner, '_iter_llm_responses')]
        originals = [getattr(owner, name) for owner, name in owners]
        try:
            await plugin.initialize()
            for (owner, name), original in zip(owners, originals):
                self.assertIsNot(getattr(owner, name), original)
            values = {}
            event = SimpleNamespace(set_extra=lambda key, value: values.update({key: value}))
            response = LLMResponse(role='assistant', completion_text='test', raw_completion=SimpleNamespace(model='actual-gpt'))
            await plugin.remember_response_model(event, response)
            self.assertEqual(values[MODEL_KEY], 'actual-gpt')
        finally:
            await plugin.terminate()
        for (owner, name), original in zip(owners, originals):
            self.assertIs(getattr(owner, name), original)
