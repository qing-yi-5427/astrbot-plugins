import unittest
from unittest.mock import MagicMock, patch

try:
    from astrbot.core.agent.message import Message
    from astrbot.core.agent.context.manager import ContextManager
    from astrbot_plugin_context_image_limiter.main import ContextImageLimiter
except ModuleNotFoundError:
    ContextImageLimiter = None


def image_message(size=100):
    prefix = "data:image/png;base64,"
    return Message.model_validate({"role": "user", "content": [
        {"type": "text", "text": "Keep this text"},
        {"type": "image_url", "image_url": {"url": prefix + "A" * (size-len(prefix))}},
    ]})


@unittest.skipIf(ContextImageLimiter is None, "Runs inside the AstrBot image")
class AstrBotIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.plugin = ContextImageLimiter(MagicMock(), {"max_total_image_mb": 0.0001})
        self.plugin.logger = MagicMock()

    async def asyncTearDown(self):
        await self.plugin.terminate()

    async def test_typed_messages_still_serialize_and_keep_tool_ids(self):
        messages = [image_message(), image_message(), Message(role="tool", tool_call_id="keep-id", content="result")]
        self.plugin._trim(messages, 'test')
        dumped = [m.model_dump() for m in messages]
        self.assertEqual(len(dumped), 3)
        self.assertEqual(dumped[2]['tool_call_id'], 'keep-id')
        self.assertEqual(dumped[0]['content'][0]['text'], 'Keep this text')
        self.assertEqual(dumped[0]['content'][1]['type'], 'text')
        self.assertEqual(dumped[1]['content'][1]['type'], 'image_url')

    async def test_every_step_caps_new_tool_images_and_passes_arguments(self):
        async def original(manager, messages, trusted_token_usage=0):
            self.assertEqual(trusted_token_usage, 321)
            self.assertEqual(sum(p.type == 'image_url' for m in messages for p in (m.content or []) if not isinstance(m.content, str)), 1)
            return messages
        with patch.object(ContextManager, 'process', original):
            await self.plugin.initialize()
            manager = object.__new__(ContextManager)
            messages = [image_message(), image_message()]
            await manager.process(messages, trusted_token_usage=321)
            messages.append(image_message())  # next tool-loop step
            await manager.process(messages, trusted_token_usage=321)
            self.assertEqual(messages[-1].content[-1].type, 'image_url')
            self.assertEqual(messages[-2].content[-1].type, 'text')
            await self.plugin.terminate()
            self.assertIs(ContextManager.process, original)

    async def test_cleanup_after_processor_and_disable_restores(self):
        async def original(manager, messages):
            return messages + [image_message(200)]
        with patch.object(ContextManager, 'process', original):
            await self.plugin.initialize()
            manager = object.__new__(ContextManager)
            result = await manager.process([image_message(100)])
            self.assertTrue(all(p.type != 'image_url' for m in result for p in m.content))
            await self.plugin.terminate()
            self.assertIs(ContextManager.process, original)

    async def test_other_plugin_wrapper_is_not_clobbered_on_disable(self):
        async def original(manager, messages):
            return messages
        with patch.object(ContextManager, 'process', original):
            await self.plugin.initialize()
            our_wrapper = ContextManager.process
            async def later_wrapper(manager, messages):
                return await our_wrapper(manager, messages)
            ContextManager.process = later_wrapper
            await self.plugin.terminate()
            self.assertIs(ContextManager.process, later_wrapper)
            messages = [image_message(200)]
            await object.__new__(ContextManager).process(messages)
            self.assertEqual(messages[0].content[-1].type, 'image_url')
