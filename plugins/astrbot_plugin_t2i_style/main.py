"""AstrBot 4.28.2/4.29.4 adapter; no tools or global config mutations."""

import asyncio
import json
from functools import partial

import aiohttp
from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse
from astrbot.core import html_renderer
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.pipeline.result_decorate.stage import ResultDecorateStage
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.utils.t2i.network_strategy import NetworkRenderStrategy

from .bridge import StyleBridge
from .capabilities import ServiceCapabilities, probe_health
from .core import TEMPLATES, StyleController, StyleStore
from .models import record_response
from .repository import RepositoryError, ThemeRepository


class T2IStylePlugin(star.Star):
    def __init__(self, context, config):
        super().__init__(context)
        self.config = config
        self.store = None
        self.controller = None
        self.bridge = None
        self.repository_task = None

    async def initialize(self):
        if self.bridge is not None and self.bridge.active:
            return
        # Use only this plugin's dedicated data directory, never AstrBot config.
        data_dir = star.StarTools.get_data_dir("astrbot_plugin_t2i_style")
        self.repository = ThemeRepository(data_dir, self.config, self._validate_theme)
        await asyncio.to_thread(self.repository.load)
        self.store = StyleStore(
            data_dir, self.config, templates=TEMPLATES | self.repository.names()
        )
        await self.store.initialize()
        self.capabilities = ServiceCapabilities(
            strategy_getter=lambda: getattr(html_renderer, "network_strategy", None),
            fallback_url=self.config.get("service_url", ""),
        )
        self.theme_capabilities = ServiceCapabilities(
            strategy_getter=lambda: getattr(html_renderer, "network_strategy", None),
            fallback_url=self.config.get("service_url", ""),
            probe=partial(probe_health, protocol_key="theme_protocol"),
        )
        self.controller = StyleController(
            self.store,
            capability_status=self._service_status,
            repository_action=self._update_themes,
            repository_status=self.repository.describe,
        )
        self.bridge = StyleBridge(
            self.store.render_style,
            self.capabilities.supports,
            theme_resolver=self.repository.bundle,
            theme_capability_check=self.theme_capabilities.supports,
        )
        self.bridge.install(
            ResultDecorateStage, NetworkRenderStrategy, ToolLoopAgentRunner
        )
        self.logger.info(
            "T2I styles and per-reply model footer ready; protocol capability required for styles"
        )
        if self.repository.enabled:
            self.repository_task = asyncio.create_task(self._repository_loop())

    async def _validate_theme(self, bundle):
        """Try the theme on every configured default renderer before enabling it."""
        config = self.context.get_config()
        endpoint = config.get("t2i_endpoint") if hasattr(config, "get") else None
        supported, _ = await self.theme_capabilities.check(endpoint=endpoint or None)
        if not supported:
            raise RepositoryError(
                "文转图服务需升级到支持 theme_protocol: 1 的版本（T2I 2.1.0+）"
            )
        timeout = aiohttp.ClientTimeout(total=65, connect=4)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            catalog = None
            for url in self.theme_capabilities.endpoints(endpoint=endpoint or None):
                async with session.post(
                    url + "/themes/validate",
                    json={"theme": bundle},
                    allow_redirects=False,
                ) as response:
                    raw = bytearray()
                    async for chunk in response.content.iter_chunked(4096):
                        raw.extend(chunk)
                        if len(raw) > 16384:
                            raise RepositoryError("主题验证响应过大")
                    result = json.loads(raw)
                    if response.status != 200 or result.get("status") != "ok":
                        raise RepositoryError(
                            str(result.get("error", "本地主题验证失败"))[:1200]
                        )
                    catalog = result.get("theme", {})
                    if catalog.get("id") != bundle["id"]:
                        raise RepositoryError("文转图服务返回的主题 ID 不一致")
        return catalog

    async def _update_themes(self, rollback=False):
        """Commit a validated cache and refresh the command catalog together."""
        try:
            reply = await self.repository.update(rollback=rollback)
        finally:
            self.store.templates = TEMPLATES | self.repository.names()
        if self.repository.last_error:
            self.logger.warning(
                "Theme repository update failed; the previous snapshot remains active"
            )
        else:
            self.logger.info(
                f"Theme repository snapshot: {self.repository.snapshot['revision'][:12] or 'built-in'}"
            )
        return reply

    async def _repository_loop(self):
        """Sync outside the chat pipeline and stop cleanly on plugin unload."""
        if self.config.get("theme_sync_on_start", True):
            await self._update_themes()
        interval = max(
            0, min(1440, int(self.config.get("theme_sync_interval_minutes", 30)))
        )
        while interval:
            await asyncio.sleep(interval * 60)
            await self._update_themes()

    async def _service_status(self, session=None):
        config = self.context.get_config(session)
        endpoint = config.get("t2i_endpoint") if hasattr(config, "get") else None
        text = await self.capabilities.describe(endpoint=endpoint or None)
        if self.repository.enabled:
            text += "\nGit 主题：" + await self.theme_capabilities.describe(
                endpoint=endpoint or None
            )
        return text

    @filter.command("t2i", alias={"图样式"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def t2i(self, event: AstrMessageEvent, command: GreedyStr):
        # Stop propagation even on failures so control input never reaches LLMs.
        event.stop_event()
        if self.controller is None:
            yield event.plain_result("样式插件尚未初始化。")
            return
        reply = await self.controller.execute(event, command)
        yield event.plain_result(reply)

    @filter.on_llm_response(priority=-10000)
    async def remember_response_model(
        self, event: AstrMessageEvent, response: LLMResponse
    ):
        # Covers normal/non-tool-loop agents without looking up any default model.
        if self.bridge is not None and self.bridge.active:
            record_response(event, response)

    async def terminate(self):
        if self.repository_task is not None:
            self.repository_task.cancel()
            await asyncio.gather(self.repository_task, return_exceptions=True)
            self.repository_task = None
        if self.bridge is not None:
            self.bridge.uninstall()
