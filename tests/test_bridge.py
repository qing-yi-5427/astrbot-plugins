import asyncio
import unittest
from types import SimpleNamespace

from astrbot_plugin_model_footer.bridge import (
    FooterBridge, MODEL_KEY, UNKNOWN_MODEL, _RENDER_EVENT,
    clean_model, record_response, response_model,
)


class Event:
    def __init__(self, model=None, llm=True):
        self.extra = {MODEL_KEY: model}
        self.llm = llm
        self.data = {"text": "Original reply"}
        self.rendered = []

    def get_extra(self, name):
        return self.extra.get(name)

    def set_extra(self, name, value):
        self.extra[name] = value

    def get_result(self):
        return SimpleNamespace(is_llm_result=lambda: self.llm)


def response(model=None, **kwargs):
    return SimpleNamespace(raw_completion={"model": model}, role="assistant", is_chunk=False, **kwargs)


class ModelTests(unittest.TestCase):
    def test_raw_response_model_preferred(self):
        event = Event()
        record_response(event, response("actual-gpt"), "requested-alias")
        self.assertEqual(event.get_extra(MODEL_KEY), "actual-gpt")

    def test_real_requested_model_when_no_raw_name(self):
        event = Event()
        record_response(event, response(), "mimo-chat")
        self.assertEqual(event.get_extra(MODEL_KEY), "mimo-chat")

    def test_object_and_gemini_responses(self):
        self.assertEqual(response_model(SimpleNamespace(raw_completion=SimpleNamespace(model="gpt"))), "gpt")
        self.assertEqual(response_model(SimpleNamespace(raw_completion=SimpleNamespace(model_version="gemini-version"))), "gemini-version")

    def test_ignore_errors_chunks_and_missing_names(self):
        event = Event("correct")
        for item in (None, SimpleNamespace(role="err"), SimpleNamespace(is_chunk=True), response()):
            record_response(event, item)
        self.assertEqual(event.get_extra(MODEL_KEY), "correct")

    def test_sanitize_and_cap_names(self):
        self.assertEqual(clean_model("  GPT\n6\x00 sol\u202e "), "GPT 6 sol")
        self.assertEqual(clean_model({"token": "secret"}), "")
        self.assertEqual(len(clean_model("x"*200)), 160)


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        class Network:
            async def render_custom_template(self, tmpl_str, tmpl_data, return_url=False, options=None):
                await asyncio.sleep(0.002)
                return dict(tmpl_data), return_url, options

        class Stage:
            async def process(self, event):
                if getattr(event, 'fail', False):
                    raise RuntimeError('failure')
                event.rendered.append(await Network().render_custom_template(
                    tmpl_str='template', tmpl_data=event.data, return_url=True, options={'quality': 85}))
                yield 'stage yielded'
                event.rendered.append(await Network().render_custom_template('template', event.data))

        class Runner:
            async def _iter_llm_responses(self, *, include_model=True):
                for item in self.responses:
                    await asyncio.sleep(0)
                    yield item

        self.Network, self.Stage, self.Runner = Network, Stage, Runner
        self.originals = (Stage.process, Network.render_custom_template, Runner._iter_llm_responses)
        self.bridge = FooterBridge()
        self.bridge.install(Stage, Network, Runner)

    async def asyncTearDown(self):
        self.bridge.uninstall()
        self.assertIsNone(_RENDER_EVENT.get())

    async def consume(self, event):
        async for _ in self.Stage().process(event):
            self.assertIsNone(_RENDER_EVENT.get())

    async def test_per_reply_concurrency_and_data_is_not_modified(self):
        events = [Event('gpt-sol'), Event('mimo')]
        await asyncio.gather(*(self.consume(e) for e in events))
        for event in events:
            self.assertEqual(len(event.rendered), 2)
            self.assertTrue(all(ret[0]['model_name'] == event.get_extra(MODEL_KEY) for ret in event.rendered))
            self.assertNotIn('model_name', event.data)
            self.assertEqual(event.rendered[0][1:], (True, {'quality': 85}))

    async def test_plugin_results_and_unscoped_render_keep_old_behavior(self):
        event = Event('gpt', llm=False)
        await self.consume(event)
        self.assertNotIn('model_name', event.rendered[0][0])
        direct = await self.Network().render_custom_template('template', {'text': 'direct'})
        self.assertNotIn('model_name', direct[0])

    async def test_unknown_model_is_explicit_not_default_guess(self):
        event = Event()
        await self.consume(event)
        self.assertEqual(event.rendered[0][0]['model_name'], UNKNOWN_MODEL)

    async def test_scopes_reset_on_exception_and_early_close(self):
        event = Event('model')
        iterator = self.Stage().process(event)
        await anext(iterator)
        self.assertIsNone(_RENDER_EVENT.get())
        await iterator.aclose()
        event.fail = True
        with self.assertRaises(RuntimeError):
            await self.consume(event)
        self.assertIsNone(_RENDER_EVENT.get())

    async def test_cancellation_resets_scope(self):
        entered = asyncio.Event()
        class SlowStage:
            async def process(self, event):
                entered.set()
                await asyncio.Event().wait()
                yield
        self.bridge.uninstall()
        self.bridge.install(SlowStage, self.Network, self.Runner)
        async def operation():
            try:
                async for _ in SlowStage().process(Event('cancelled')):
                    pass
            finally:
                self.assertIsNone(_RENDER_EVENT.get())
        task = asyncio.create_task(operation())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_fallback_uses_fallback_model_not_primary_override(self):
        event = Event()
        runner = self.Runner()
        runner.req = SimpleNamespace(model='primary-override')
        runner.provider = SimpleNamespace(get_model=lambda: 'fallback-mimo')
        runner.run_context = SimpleNamespace(context=SimpleNamespace(event=event))
        runner.responses = [response()]
        async for _ in runner._iter_llm_responses(include_model=False):
            pass
        self.assertEqual(event.get_extra(MODEL_KEY), 'fallback-mimo')

    async def test_request_override_and_final_raw_response(self):
        event = Event()
        runner = self.Runner()
        runner.req = SimpleNamespace(model='explicit-request')
        runner.provider = SimpleNamespace(get_model=lambda: 'wrong-default')
        runner.run_context = SimpleNamespace(context=SimpleNamespace(event=event))
        runner.responses = [response()]
        async for _ in runner._iter_llm_responses():
            pass
        self.assertEqual(event.get_extra(MODEL_KEY), 'explicit-request')
        runner.responses = [response('upstream-real-model')]
        async for _ in runner._iter_llm_responses():
            pass
        self.assertEqual(event.get_extra(MODEL_KEY), 'upstream-real-model')

    async def test_disable_restores_all_adapters(self):
        self.bridge.uninstall()
        self.assertEqual((self.Stage.process, self.Network.render_custom_template, self.Runner._iter_llm_responses), self.originals)

    async def test_later_plugin_wrapper_is_not_overwritten(self):
        wrapped = self.Network.render_custom_template
        async def later(*args, **kwargs):
            return await wrapped(*args, **kwargs)
        self.Network.render_custom_template = later
        self.bridge.uninstall()
        self.assertIs(self.Network.render_custom_template, later)
        ret = await self.Network().render_custom_template('template', {'text': 'test'})
        self.assertNotIn('model_name', ret[0])
