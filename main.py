from __future__ import annotations

import asyncio
import time
from pathlib import Path

import aiohttp
import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .core.cache import ResultCache
from .core.config import PluginSettings
from .core.engines import SauceNaoEngine, TraceMoeEngine
from .core.formatter import format_report, preview_url
from .core.image_resolver import (
    ImageResolutionError,
    ImageSelection,
    materialize_image,
    select_image_component,
)
from .core.onebot import fetch_onebot_reply_image
from .core.orchestrator import SearchOrchestrator
from .core.rate_limit import Cooldown
from .core.reaction import add_reaction
from .core.trigger import TriggerMatcher

PLUGIN_NAME = "astrbot_plugin_qing_image_source"
VERSION = "0.1.5"


@register(PLUGIN_NAME, "qingyi", "搜索二次元插画与动画截图来源", VERSION)
class QingImageSourcePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.settings = PluginSettings.from_mapping(config)
        self.matcher = TriggerMatcher(self.settings.trigger)
        self.cooldown = Cooldown(self.settings.search.cooldown_seconds)
        self.semaphore = asyncio.Semaphore(self.settings.search.max_concurrency)
        self.session: aiohttp.ClientSession | None = None
        self.cache: ResultCache | None = None
        self.orchestrator: SearchOrchestrator | None = None

    async def initialize(self):
        timeout = aiohttp.ClientTimeout(total=self.settings.search.timeout_seconds)
        self.session = aiohttp.ClientSession(timeout=timeout)
        try:
            data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        except TypeError:
            data_dir = Path(StarTools.get_data_dir()) / PLUGIN_NAME
        data_dir.mkdir(parents=True, exist_ok=True)
        self.cache = ResultCache(
            data_dir / "search_cache.json",
            enabled=self.settings.cache.enabled,
            ttl_seconds=self.settings.cache.ttl_hours * 3600,
        )
        general_proxy = self.settings.network.proxy_url
        sauce = SauceNaoEngine(
            self.session,
            api_key=self.settings.search.saucenao_api_key
            if self.settings.search.enable_saucenao
            else "",
            high_threshold=self.settings.search.sauce_high,
            possible_threshold=self.settings.search.sauce_possible,
            hide=self.settings.safety.saucenao_hide,
            proxy=self.settings.network.saucenao_proxy_url or general_proxy,
        )
        trace = TraceMoeEngine(
            self.session,
            api_key=self.settings.search.tracemoe_api_key,
            high_threshold=self.settings.search.trace_high,
            possible_threshold=self.settings.search.trace_possible,
            enabled=self.settings.search.enable_tracemoe,
            proxy=self.settings.network.tracemoe_proxy_url or general_proxy,
        )
        self.orchestrator = SearchOrchestrator(
            sauce,
            trace,
            self.cache,
            max_results=self.settings.search.max_results,
        )
        logger.info("[%s] v%s 初始化完成", PLUGIN_NAME, VERSION)

    async def terminate(self):
        if self.session and not self.session.closed:
            await self.session.close()

    def _ready(self) -> bool:
        return self.orchestrator is not None and self.cache is not None

    def _plain_result(self, event: AstrMessageEvent, text: str):
        result = event.plain_result(text)
        if self.settings.output.force_text_message:
            result.use_t2i(False)
        return result

    def _chain_result(
        self, event: AstrMessageEvent, chain: list[Comp.BaseMessageComponent]
    ):
        result = event.chain_result(chain)
        if self.settings.output.force_text_message:
            result.use_t2i(False)
        return result

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10)
    async def on_message(self, event: AstrMessageEvent):
        if not self._ready():
            return
        components = list(event.get_messages() or [])
        match = self.matcher.match(
            components,
            is_group=bool(event.get_group_id()),
            self_id=str(event.get_self_id()),
        )
        if not match.matched:
            return
        selection = select_image_component(components)
        if self.settings.trigger.enable_onebot_reply_fallback and (
            selection is None or selection.origin != "reply"
        ):
            onebot_reply = await fetch_onebot_reply_image(event, components)
            if onebot_reply is not None:
                source = onebot_reply.source
                image_component = Comp.Image(
                    file=source,
                    url=source if source.startswith(("http://", "https://")) else "",
                )
                selection = ImageSelection(
                    image_component,
                    "reply-onebot",
                    onebot_reply.total_count
                    + (selection.total_count if selection is not None else 0),
                )
        if selection is None:
            return

        event.stop_event()
        if self.settings.reaction.enabled:
            reacted = await add_reaction(
                event,
                emoji_id=self.settings.reaction.emoji_id,
                emoji_type=self.settings.reaction.emoji_type,
            )
            if not reacted:
                logger.debug("[%s] 当前平台未添加消息表情回应", PLUGIN_NAME)

        key = (str(event.unified_msg_origin), str(event.get_sender_id()))
        remaining = await self.cooldown.acquire(key)
        if remaining > 0:
            yield self._plain_result(event, f"操作太快，请 {remaining:.1f} 秒后再试。")
            return

        try:
            image = await materialize_image(
                selection.component,
                max_bytes=self.settings.safety.max_image_mb * 1024 * 1024,
                max_pixels=self.settings.safety.max_pixels,
            )
            async with self.semaphore:
                started = time.monotonic()
                report = await self.orchestrator.search(image, match.route)  # type: ignore[union-attr]
            logger.info(
                "[%s] search hash=%s route=%s cache=%s hits=%d duration_ms=%d",
                PLUGIN_NAME,
                image.sha256[:12],
                match.route,
                report.cache_hit,
                len(report.hits),
                int((time.monotonic() - started) * 1000),
            )
            text = format_report(
                report,
                multiple_images=selection.total_count > 1,
                is_group=bool(event.get_group_id()),
                allow_adult_links_in_private=self.settings.safety.allow_adult_links_in_private,
            )
            chain: list[Comp.BaseMessageComponent] = [Comp.Plain(text)]
            thumbnail = preview_url(report)
            if thumbnail:
                chain.append(Comp.Image.fromURL(thumbnail))
            yield self._chain_result(event, chain)
        except ImageResolutionError as exc:
            yield self._plain_result(event, f"无法处理这张图片：{exc}")
        except Exception as exc:  # noqa: BLE001 - keep plugin failures inside event boundary
            logger.exception("[%s] 搜图失败: %s", PLUGIN_NAME, exc)
            yield self._plain_result(event, "搜图失败，请稍后再试。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("搜图状态")
    async def search_status(self, event: AstrMessageEvent):
        """查看搜图引擎、缓存与最近额度状态。"""
        if not self._ready():
            yield self._plain_result(event, "插件尚未完成初始化。")
            return
        cache_stats = await self.cache.stats()  # type: ignore[union-attr]
        quotas = self.orchestrator.last_quota  # type: ignore[union-attr]
        quota_text = (
            "；".join(
                f"{engine}: "
                + ", ".join(f"{key}={value}" for key, value in data.items())
                for engine, data in quotas.items()
            )
            or "暂无（完成一次查询后更新）"
        )
        text = "\n".join(
            [
                f"动漫搜图 v{VERSION}",
                "SauceNAO："
                + ("已配置" if self.settings.search.saucenao_api_key else "未配置 Key"),
                "trace.moe："
                + ("已启用" if self.settings.search.enable_tracemoe else "已关闭"),
                f"缓存：{'开启' if cache_stats['enabled'] else '关闭'}，{cache_stats['entries']} 条",
                f"最近额度：{quota_text}",
            ]
        )
        yield self._plain_result(event, text)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("搜图清缓存")
    async def clear_search_cache(self, event: AstrMessageEvent):
        """清除搜图结果缓存。"""
        if self.cache is None:
            yield self._plain_result(event, "缓存尚未初始化。")
            return
        count = await self.cache.clear()
        yield self._plain_result(event, f"已清除 {count} 条搜图缓存。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("动漫搜图帮助")
    async def search_help(self, event: AstrMessageEvent):
        """显示动漫搜图插件帮助。"""
        keywords = (
            self.settings.trigger.auto_keywords
            + self.settings.trigger.saucenao_keywords
            + self.settings.trigger.tracemoe_keywords
        )
        yield self._plain_result(
            event,
            "动漫搜图使用方法：\n"
            "1. 发送关键词并附带图片；或引用图片后发送关键词。\n"
            "2. 引用图片优先，多图只查询第一张。\n"
            f"3. 当前关键词：{', '.join(dict.fromkeys(keywords))}",
        )
