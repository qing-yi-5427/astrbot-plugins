"""Run inside the deployed AstrBot environment; all search I/O is mocked."""
import importlib.util
import json
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from astrbot.core.tools import web_search_tools as web

spec = importlib.util.spec_from_file_location('limiter_under_test', Path(__file__).parents[1] / 'main.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class LimiterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.plugin = object.__new__(module.TavilyResultLimiter)
        self.plugin.config = {'max_results': 4, 'auto_clear_history': True}
        self.plugin._patches = []
        self.plugin._active = False
        self.plugin.logger = logging.getLogger('limiter_test')
        self.originals = [c.call for c in (web.TavilyWebSearchTool, web.AnySearchWebSearchTool, web.TavilyExtractWebPageTool)]
        await self.plugin.initialize()

    async def asyncTearDown(self):
        await self.plugin.terminate()

    async def search(self, tool_class, helper, **kwargs):
        rows = [web.SearchResult(title=f'Title {i}', url=f'https://example.com/{i}', snippet='测' * 1500) for i in range(25)]
        mock = AsyncMock(return_value=rows)
        with patch.object(web, '_get_runtime', return_value=({}, {'websearch_tavily_key': ['test']}, 'test')), patch.object(web, helper, mock):
            output = await tool_class().call(None, query='test', **kwargs)
        return json.loads(output), mock.call_args.args[1]

    async def test_anysearch_limits_and_parameters(self):
        result, sent = await self.search(web.AnySearchWebSearchTool, '_anysearch_search', max_results=18, tag='finance.quote', params={'symbol':'AAPL'}, zone='intl', language='zh-CN')
        self.assertEqual(sent['max_results'], 4)
        self.assertEqual(sent['params'], {'symbol':'AAPL'})
        self.assertEqual(sent['tag'], 'finance.quote')
        self.assertEqual(sent['zone'], 'intl')
        self.assertEqual(sent['language'], 'zh-CN')
        self.assertEqual(len(result['results']), 4)
        row = result['results'][0]
        self.assertEqual(row['snippet'], '测' * 1000 + '… [摘要已截断]')
        self.assertEqual(row['url'], 'https://example.com/0')
        self.assertEqual(row['title'], 'Title 0')
        self.assertIn('index', row)

    async def test_tavily_regression_and_fewer_requested(self):
        for cls, helper in [(web.AnySearchWebSearchTool, '_anysearch_search'), (web.TavilyWebSearchTool, '_tavily_search')]:
            result, sent = await self.search(cls, helper, max_results=2)
            self.assertEqual(sent['max_results'], 2)
            self.assertEqual(len(result['results']), 2)

    async def test_provider_caps_and_invalid_values(self):
        self.plugin.config['max_results'] = 20
        result, sent = await self.search(web.AnySearchWebSearchTool, '_anysearch_search', max_results=30)
        self.assertEqual(len(result['results']), 10)
        self.assertEqual(sent['max_results'], 10)
        result, sent = await self.search(web.TavilyWebSearchTool, '_tavily_search', max_results=30)
        self.assertEqual(len(result['results']), 20)
        for value, expected in [(None, 10), ('invalid', 10), (0, 1), (-2, 1)]:
            self.plugin.config['max_results'] = value
            result, sent = await self.search(web.AnySearchWebSearchTool, '_anysearch_search', max_results='invalid')
            self.assertEqual(sent['max_results'], expected)
            self.assertEqual(len(result['results']), expected)

    async def test_errors_and_non_json_preserved(self):
        for output in ['Error: unavailable', '{bad json', '[]', '{"results":null}', None, {'status':'error'}]:
            wrapper = self.plugin._search_wrapper(AsyncMock(return_value=output), 10)
            self.assertEqual(await wrapper(None, None), output)

    async def test_extract_limit(self):
        with patch.object(web, '_get_runtime', return_value=({}, {'websearch_tavily_key':['test']}, 'test')), patch.object(web, '_tavily_extract', AsyncMock(return_value=[{'url':'https://example.com', 'raw_content':'a'*9000}])):
            output = await web.TavilyExtractWebPageTool().call(None, url='https://example.com')
        self.assertTrue(output.endswith('[网页正文已截断；如需后续内容，请打开原网页]'))
        self.assertLess(len(output), 6100)

    async def test_history_structure_and_other_tools(self):
        names = ['web_search_anysearch', 'web_search_tavily', 'tavily_extract_web_page', 'memory_search']
        calls = [{'id':str(i), 'function':{'name':name}} for i, name in enumerate(names)]
        contexts = [{'role':'assistant', 'tool_calls':calls}] + [{'role':'tool', 'tool_call_id':str(i), 'content':'x'*2000} for i in range(4)] + [{'role':'user', 'content':'keep'}]
        count, _ = module.clear_previous_tavily_results(contexts)
        self.assertEqual(count, 3)
        self.assertEqual(contexts[0]['tool_calls'], calls)
        self.assertEqual(contexts[4]['content'], 'x'*2000)
        self.assertEqual(contexts[-1]['content'], 'keep')
        self.assertEqual(module.clear_previous_tavily_results(contexts), (0, 0))
        self.plugin.config['auto_clear_history'] = False
        contexts[1]['content'] = 'keep disabled'
        await self.plugin.clear_tavily_history(SimpleNamespace(unified_msg_origin='test'), SimpleNamespace(contexts=contexts))
        self.assertEqual(contexts[1]['content'], 'keep disabled')

    async def test_reload_restores_originals(self):
        patched = web.AnySearchWebSearchTool.call
        await self.plugin.initialize()
        self.assertIs(web.AnySearchWebSearchTool.call, patched)
        await self.plugin.terminate()
        for cls, original in zip((web.TavilyWebSearchTool, web.AnySearchWebSearchTool, web.TavilyExtractWebPageTool), self.originals):
            self.assertIs(cls.call, original)
        await self.plugin.terminate()
        await self.plugin.initialize()
        result, _ = await self.search(web.AnySearchWebSearchTool, '_anysearch_search')
        self.assertEqual(len(result['results']), 4)

    async def test_older_astrbot_without_anysearch(self):
        await self.plugin.terminate()
        cls = web.AnySearchWebSearchTool
        del web.AnySearchWebSearchTool
        try:
            await self.plugin.initialize()
            result, _ = await self.search(web.TavilyWebSearchTool, '_tavily_search')
            self.assertEqual(len(result['results']), 4)
        finally:
            web.AnySearchWebSearchTool = cls

if __name__ == '__main__':
    unittest.main()
