"""Compatibility fixes isolated from LivingMemory's official update files."""

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .compat import CompatibilityPatch


@register("astrbot_plugin_memory_recall_fix", "qingyi", "LivingMemory 召回兼容修复", "1.0.0")
class MemoryRecallFix(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.patch = CompatibilityPatch()

    async def initialize(self):
        if self.config.get("enabled", True):
            self.patch.apply()

    @filter.on_astrbot_loaded()
    async def on_loaded(self):
        if self.config.get("enabled", True):
            self.patch.apply()

    @filter.on_llm_request(priority=10000)
    async def ensure_current_memory_version(self, event, req):
        # No request, persona, tool set or conversation is modified here.
        if self.config.get("enabled", True):
            self.patch.apply()
        elif self.patch.applied:
            self.patch.restore()

    @filter.command("memory_fix_status")
    async def status(self, event: AstrMessageEvent):
        self.patch.apply() if self.config.get("enabled", True) else self.patch.restore()
        yield event.plain_result(
            f"记忆兼容修复：{self.patch.state}\n{self.patch.reason}\n"
            "官方记忆插件源码未修改；升级后会重新校验兼容性。"
        )

    async def terminate(self):
        self.patch.restore()
        logger.info("[memory_recall_fix] Compatibility fixes unloaded")

