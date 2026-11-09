"""Framework-independent configuration, persistence and administrator commands."""

import asyncio
import copy
import json
import os
import re
import stat
import tempfile
from pathlib import Path

from .avatars import download_qq_avatar, read_local_avatar, validate_base64

TEMPLATES = {
    "codex": "原版",
    "miku": "初音",
    "genshin": "原神",
    "starrail": "星穹铁道",
    "zzz": "绝区零",
    "rio": "莉音",
}
THEME_ALIASES = {label: key for key, label in TEMPLATES.items()} | {"铁道": "starrail"}
# Empty personalization fields mean theme defaults, not the old Codex brand.
# They remain as internal sentinels to mask custom values from lower scopes.
DEFAULTS = {
    "template": "codex",
    "brand_name": "",
    "subtitle": "",
    "show_avatar": True,
    "show_footer": True,
    "avatar_base64": "",
}
MAX_STATE_BYTES = 16 * 1024 * 1024
MAX_SESSIONS = 256
HELP = (
    "/t2i list | status | sync | rollback | use <id> | name <文本> | subtitle <文本> | "
    "avatar <QQ号|default> | reset\n"
    "默认仅当前会话；全局设置：/t2i global <上述命令>。所有命令仅限 AstrBot 管理员。"
)


def validate_override(value, templates=None, *, allow_unavailable=False):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError("Invalid style fields")
    result = dict(value)
    for key, val in result.items():
        if key == "template":
            known = TEMPLATES if templates is None else templates
            if (
                not isinstance(val, str)
                or not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", val)
                or (not allow_unavailable and val not in known)
            ):
                raise ValueError("未知模板，请先使用 /t2i list")
        elif key in {"brand_name", "subtitle"}:
            limit = 64 if key == "brand_name" else 96
            if (
                not isinstance(val, str)
                or len(val) > limit
                or any(not c.isprintable() for c in val)
            ):
                raise ValueError(f"文本必须为不含控制字符的字符串，长度上限 {limit}")
        elif key in {"show_avatar", "show_footer"}:
            if type(val) is not bool:
                raise ValueError("显示开关必须为布尔值")
        elif key == "avatar_base64":
            result[key] = validate_base64(val)
    return result


def config_defaults(config, templates=None):
    aliases = {
        "theme": "template",
        "brand": "brand_name",
        "subtitle": "subtitle",
        "show_avatar": "show_avatar",
        "show_footer": "show_footer",
    }
    values = dict(DEFAULTS)
    for source, target in aliases.items():
        if source in config:
            values[target] = config[source]
    if "brand_name" in config:
        values["brand_name"] = config["brand_name"]
    if isinstance(values["template"], str):
        values["template"] = THEME_ALIASES.get(values["template"], values["template"])
    return validate_override(values, templates, allow_unavailable=templates is not None)


class StyleStore:
    def __init__(self, data_dir, config=None, templates=None):
        self.path = Path(data_dir) / "styles.json"
        self.config = dict(config or {})
        self.templates = dict(TEMPLATES if templates is None else templates)
        self.allow_unavailable = templates is not None
        self.defaults = config_defaults(self.config, templates)
        self.state = {"version": 1, "global": {}, "sessions": {}}
        self.lock = asyncio.Lock()

    async def initialize(self):
        avatar_path = self.config.get("avatar_path", "")
        if not isinstance(avatar_path, str):
            raise TypeError("avatar_path 必须为字符串")
        if avatar_path:
            self.defaults["avatar_base64"] = await asyncio.to_thread(
                read_local_avatar, avatar_path
            )
        self.state = await asyncio.to_thread(self._load)

    def _load(self):
        flags = (
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            fd = os.open(self.path, flags)
        except FileNotFoundError:
            return {"version": 1, "global": {}, "sessions": {}}
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_STATE_BYTES:
                raise ValueError("Invalid style storage file")
            raw = stream.read(MAX_STATE_BYTES + 1)
        if len(raw) > MAX_STATE_BYTES:
            raise ValueError("Style storage exceeded size limit")
        state = json.loads(raw)
        if (
            not isinstance(state, dict)
            or set(state) != {"version", "global", "sessions"}
            or type(state["version"]) is not int
            or state["version"] != 1
            or not isinstance(state["sessions"], dict)
            or len(state["sessions"]) > MAX_SESSIONS
        ):
            raise ValueError("Invalid style storage schema")
        state["global"] = validate_override(
            state["global"], self.templates, allow_unavailable=self.allow_unavailable
        )
        for session, style in state["sessions"].items():
            if not isinstance(session, str) or not 0 < len(session) <= 512:
                raise ValueError("Invalid session identity")
            state["sessions"][session] = validate_override(
                style, self.templates, allow_unavailable=self.allow_unavailable
            )
        return state

    def effective(self, session=None):
        # Copy-on-write state makes this synchronous read atomic on the event loop.
        state = self.state
        result = self.defaults | state["global"]
        if session is not None:
            result |= state["sessions"].get(session, {})
        return result

    def render_style(self, session=None):
        # Omit untouched personalization: the service owns per-theme defaults.
        effective = self.effective(session)
        if effective["template"] not in self.templates:
            effective["template"] = "codex"
        return {
            key: value
            for key, value in effective.items()
            if key not in {"brand_name", "subtitle", "avatar_base64"} or value != ""
        }

    def _save(self, state):
        payload = json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        if len(payload) > MAX_STATE_BYTES:
            raise ValueError("样式存储已达容量上限，请清除无用会话设置")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(
            prefix=".styles-", suffix=".tmp", dir=self.path.parent
        )
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            # The temporary is created above in the dedicated plugin data directory.
            if temporary.exists():
                temporary.unlink()

    async def update(self, session, patch=None, *, remove_avatar=False, reset=False):
        if session is not None and (
            not isinstance(session, str) or not 0 < len(session) <= 512
        ):
            raise ValueError("缺少有效的当前会话标识")
        patch = await asyncio.to_thread(validate_override, patch or {}, self.templates)
        async with self.lock:
            state = copy.deepcopy(self.state)
            if session is None:
                target = state["global"]
            else:
                if (
                    session not in state["sessions"]
                    and len(state["sessions"]) >= MAX_SESSIONS
                ):
                    raise ValueError("会话样式数量已达上限")
                target = state["sessions"].setdefault(session, {})
            if reset:
                target.clear()
            if remove_avatar:
                target.pop("avatar_base64", None)
            target.update(patch)
            if session is not None and not target:
                state["sessions"].pop(session, None)
            # A cancelled caller must not leave disk newer than in-memory state.
            task = asyncio.create_task(asyncio.to_thread(self._save, state))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                self.state = state
                raise
            self.state = state


class StyleController:
    def __init__(
        self,
        store,
        downloader=download_qq_avatar,
        capability_status=None,
        repository_action=None,
        repository_status=None,
    ):
        self.store = store
        self.downloader = downloader
        self.capability_status = capability_status
        self.repository_action = repository_action
        self.repository_status = repository_status

    async def service_status(self, session=None):
        if self.capability_status is None:
            return "服务能力尚未检测，设置已保存但不能确认生效。"
        return await self.capability_status(session)

    async def execute(self, event, command):
        # Defense in depth: do not rely solely on command filter registration.
        if not event.is_admin():
            return "仅 AstrBot 管理员可以查看或修改文转图样式。"
        if not isinstance(command, str) or len(command) > 1024:
            return "指令无效或过长。"
        words = command.strip().split(maxsplit=1)
        action = words[0] if words else "status"
        tail = words[1].strip() if len(words) > 1 else ""
        scope = "session"
        if action in {"global", "全局", "session", "会话"}:
            scope = "global" if action in {"global", "全局"} else "session"
            words = tail.split(maxsplit=1)
            action = words[0] if words else "status"
            tail = words[1].strip() if len(words) > 1 else ""
        aliases = {
            "列表": "list",
            "状态": "status",
            "使用": "use",
            "名称": "name",
            "副标题": "subtitle",
            "头像": "avatar",
            "重置": "reset",
            "帮助": "help",
            "同步": "sync",
            "回退": "rollback",
        }
        action = aliases.get(action, action)
        session = (
            None if scope == "global" else getattr(event, "unified_msg_origin", None)
        )
        if scope == "session" and (not isinstance(session, str) or not session):
            return "缺少当前会话标识，未修改任何设置。"
        if action == "help":
            return HELP
        if action in {"sync", "rollback"} and not tail:
            if self.repository_action is None:
                return "此版本尚未配置 Git 主题同步。"
            return await self.repository_action(rollback=action == "rollback")
        if action == "list":
            return "可用模板：\n" + "\n".join(
                f"{key}：{label}" for key, label in self.store.templates.items()
            )
        if action == "status":
            style = self.store.effective(session)
            label = "全局" if scope == "global" else "当前会话"
            repository = (
                "\n" + self.repository_status()
                if self.repository_status is not None
                else ""
            )
            unavailable = (
                "（暂不可用，输出回退到 codex）"
                if style["template"] not in self.store.templates
                else ""
            )
            return (
                f"{label}有效样式（WebUI默认 < 管理员全局覆盖 < 会话覆盖）：\n"
                f"模板：{style['template']}{unavailable}\n品牌：{style['brand_name'] or '继承主题默认'}\n"
                f"副标题：{style['subtitle'] or '继承主题默认'}\n显示头像：{style['show_avatar']}\n"
                f"显示页脚：{style['show_footer']}\n头像：{'自定义' if style['avatar_base64'] else '主题默认'}\n"
                f"{await self.service_status(session)}{repository}\n仅影响 codex-brand 的 LLM 文转图，不修改模型提供商。"
            )
        try:
            if action == "use":
                aliases = {
                    label: key for key, label in self.store.templates.items()
                } | THEME_ALIASES
                await self.store.update(session, {"template": aliases.get(tail, tail)})
            elif action in {"name", "subtitle"}:
                value = "" if tail in {"default", "theme", "默认", "主题"} else tail
                await self.store.update(
                    session, {"brand_name" if action == "name" else "subtitle": value}
                )
            elif action == "avatar":
                if tail in {"default", "theme", "默认", "主题"}:
                    await self.store.update(session, {"avatar_base64": ""})
                elif tail in {"inherit", "继承"}:
                    await self.store.update(session, remove_avatar=True)
                else:
                    avatar = await self.downloader(tail)
                    await self.store.update(session, {"avatar_base64": avatar})
            elif action == "reset" and not tail:
                await self.store.update(session, reset=True)
            else:
                return HELP
        except ValueError as exc:
            return f"未修改设置：{exc}"
        except OSError:
            return "保存样式失败，未修改设置。请查看管理员日志。"
        saved = "已保存全局覆盖。" if scope == "global" else "已保存当前会话设置。"
        return saved + "\n" + await self.service_status(session)
