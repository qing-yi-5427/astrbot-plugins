import random
import time
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp

CACHE_TTL = 3600  # 1小时

@register("roll", "Developer", "随机选择插件", "1.0.0")
class RollPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._cache: dict[str, tuple[str, float]] = {}

    async def initialize(self):
        """异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""

    @filter.command("roll")
    async def roll(self, event: AstrMessageEvent):
        """随机选择一个选项，用法：/roll 选项1 选项2 选项3... 或 /roll a-b"""
        sender_id = event.get_sender_id()
        now = time.time()

        message_str = event.message_str
        parts = message_str.split()
        options = parts[1:] if len(parts) > 1 else []

        if not options:
            yield event.chain_result([Comp.Plain("请提供选项！用法：/roll 选项1 选项2 选项3... 或 /roll a-b")])
            return

        cache_key = sender_id + ":" + message_str

        if cache_key in self._cache:
            cached_result, cached_time = self._cache[cache_key]
            if now - cached_time < CACHE_TTL:
                yield event.chain_result([Comp.Plain(f"🎲 随机选择结果：{cached_result}\n玄不救非，氪不改命，下次一定。")])
                return

        if len(options) == 1 and '-' in options[0]:
            range_str = options[0]
            try:
                range_parts = range_str.split('-')
                if len(range_parts) == 2:
                    a = int(range_parts[0])
                    b = int(range_parts[1])
                    if a <= b:
                        selected = str(random.randint(a, b))
                        self._cache[cache_key] = (selected, now)
                        yield event.chain_result([Comp.Plain(f"🎲 随机选择结果：{selected}")])
                        return
            except ValueError:
                pass

        selected = random.choice(options)
        self._cache[cache_key] = (selected, now)
        yield event.chain_result([Comp.Plain(f"🎲 随机选择结果：{selected}")])

    async def terminate(self):
        """异步的插件销毁方法，当插件被卸载/停用时会调用。"""
