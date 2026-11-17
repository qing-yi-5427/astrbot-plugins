"""Private feature lifecycle and policy; this module registers no AstrBot plugin."""

import asyncio
import copy
import inspect
from contextvars import ContextVar

from astrbot.api import logger

_EVENT = ContextVar("qing_suite_runner_event", default=None)


def current_event():
    return _EVENT.get()


def event_from(context):
    return getattr(getattr(context, "context", None), "event", None) or current_event()


def safe_filter(decorator, legacy_names):
    """Do not register duplicate commands/tools before legacy plugins stop.

    A mistakenly installed suite stays dormant and must be reloaded after the
    old plugins are disabled. In particular, its image tool must not replace the
    old active tool merely by importing the new Python module.
    """

    def register(function):
        from astrbot.core.star.star import star_registry

        if any(m.name in legacy_names and m.activated for m in star_registry):
            return function
        return decorator(function)

    return register


def hide_image_tool(req):
    """Filter only this request, never the shared tool registry/persona."""
    tools = getattr(getattr(req, "func_tool", None), "tools", None)
    if isinstance(tools, list) and any(
        getattr(t, "name", None) == "astrbot_generate_image" for t in tools
    ):
        scoped = copy.copy(req.func_tool)
        scoped.tools = [
            t for t in tools if getattr(t, "name", None) != "astrbot_generate_image"
        ]
        req.func_tool = scoped


class FeatureService:
    """An ordinary object, deliberately not a Star subclass or a registered entry."""

    def __init__(self, context, config=None):
        self.context = context
        self.logger = logger
        self.config = config if config is not None else {}
        self._suite = None
        self._section = None
        self._legacy_id = None

    def bind_suite(self, suite, section, legacy_id):
        self._suite = suite
        self._section = section
        self._legacy_id = legacy_id
        self.logger = suite.owner.logger

    async def is_event_enabled(self, event=None):
        return self._suite is None or await self._suite.allowed(self._section, event)

    async def is_session_enabled(self, session=None):
        return await self.is_event_enabled(session)

    async def get_kv_data(self, key, default=None):
        from astrbot.core import sp

        if not self._legacy_id:
            raise RuntimeError("Feature state namespace is not bound")
        return await sp.get_async("plugin", self._legacy_id, key, default)

    async def put_kv_data(self, key, value):
        from astrbot.core import sp

        if not self._legacy_id:
            raise RuntimeError("Feature state namespace is not bound")
        await sp.put_async("plugin", self._legacy_id, key, value)

    async def initialize(self):
        pass

    async def terminate(self):
        pass


class SuiteRuntime:
    def __init__(self, owner, name, legacy, legacy_ids, factories):
        self.owner = owner
        self.name = name
        self.legacy = legacy
        self.legacy_ids = dict(legacy_ids)
        self.factories = factories
        self.services = {}
        self.errors = {}
        self.active = False
        self.blocked = False
        self._runner_patch = None
        self._lease = None

    def conflicts(self):
        getter = getattr(self.owner.context, "get_registered_star", None)
        if getter is None:
            return []
        found = []
        for old in self.legacy.values():
            metadata = getter(old)
            if metadata is not None and getattr(metadata, "activated", False):
                found.append(old)
        return found

    async def check_conflict(self):
        if self.conflicts():
            await self.terminate()
            self.blocked = True
            self.owner.logger.warning(
                "%s remains dormant: legacy plugins are still active; disable them then reload this suite",
                self.name,
            )
            return True
        return False

    async def initialize(self):
        if self.active:
            return
        if await self.check_conflict():
            return
        self.blocked = False
        self.errors.clear()
        self.active = True
        for section, (factory, takes_config) in self.factories.items():
            config = self.owner.config.get(section, {})
            if not isinstance(config, dict) or config.get("enabled", True) is not True:
                continue
            service = None
            try:
                service = (
                    factory(self.owner.context, config)
                    if takes_config
                    else factory(self.owner.context)
                )
                service.config = config
                metadata = self.owner.context.get_registered_star(self.legacy[section])
                legacy_id = (
                    getattr(metadata, "plugin_id", None) or self.legacy_ids[section]
                )
                self.legacy_ids[section] = legacy_id
                service.bind_suite(self, section, legacy_id)
                self.services[section] = service
                await service.initialize()
            except asyncio.CancelledError:
                if service is not None:
                    await service.terminate()
                await self.terminate()
                raise
            except Exception as exc:
                self.errors[section] = type(exc).__name__
                self.owner.logger.error(
                    "%s module %s initialization failed (%s); other modules continue",
                    self.name,
                    section,
                    type(exc).__name__,
                )
                if service is not None:
                    try:
                        await service.terminate()
                    except Exception:
                        self.owner.logger.error(
                            "%s module %s cleanup failed", self.name, section
                        )
                self.services.pop(section, None)
        if self.name.endswith("qing_chat") and "context_images" in self.services:
            self._install_runner_scope()
        self.owner.logger.info(
            "%s ready: %d modules, %d unavailable",
            self.name,
            len(self.services),
            len(self.errors),
        )

    async def allowed(self, section, event=None):
        if not self.active or self.blocked or section not in self.services:
            return False
        config = self.owner.config.get(section, {})
        if not isinstance(config, dict) or config.get("enabled", True) is not True:
            return False
        session = (
            event
            if isinstance(event, str)
            else getattr(event, "unified_msg_origin", None)
        )
        selected = (
            getattr(event, "plugins_name", None) if not isinstance(event, str) else None
        )
        cfg = self.owner.context.get_config(umo=session) if session else None
        if selected is None and cfg is not None:
            selected = cfg.get("plugin_set", ["*"])
        if selected is not None and selected != ["*"]:
            if self.name not in selected:
                return False
            # Migration retains legacy names in explicit profile lists, allowing
            # different old modules to remain individually selected. A new list
            # containing only the suite opts into its configured module switches.
            if (
                set(selected).intersection(self.legacy.values())
                and self.legacy[section] not in selected
            ):
                return False
        if session:
            from astrbot.core.star.session_plugin_manager import SessionPluginManager

            for name in (self.name, self.legacy[section]):
                if not await SessionPluginManager.is_plugin_enabled_for_session(
                    session, name
                ):
                    return False
        return True

    async def call(self, section, method, *args):
        try:
            return await getattr(self.services[section], method)(*args)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.errors[section] = type(exc).__name__
            self.owner.logger.error(
                "%s module %s callback %s failed (%s)",
                self.name,
                section,
                method,
                type(exc).__name__,
            )
            if method == "generate_image":
                import json

                return json.dumps(
                    {
                        "status": "error",
                        "message": "生图模块内部处理异常，请检查后台日志；不要自动重复生成。",
                        "error": {
                            "code": "module_error",
                            "stage": "unknown",
                            "request_outcome": "unknown",
                        },
                    },
                    ensure_ascii=False,
                )

    async def iterate(self, section, method, *args):
        try:
            async for result in getattr(self.services[section], method)(*args):
                yield result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.errors[section] = type(exc).__name__
            self.owner.logger.error(
                "%s module %s command %s failed (%s)",
                self.name,
                section,
                method,
                type(exc).__name__,
            )
            if args:
                yield args[0].plain_result("此功能处理异常，请检查后台日志。")

    def _install_runner_scope(self):
        from astrbot.core.agent.runners.tool_loop_agent_runner import (
            ToolLoopAgentRunner,
        )

        original = ToolLoopAgentRunner.step
        lease = {"active": True}

        async def scoped(runner, *args, **kwargs):
            event = getattr(
                getattr(getattr(runner, "run_context", None), "context", None),
                "event",
                None,
            )
            iterator = original(runner, *args, **kwargs)
            try:
                while True:
                    token = _EVENT.set(event) if lease["active"] else None
                    try:
                        item = await anext(iterator)
                    except StopAsyncIteration:
                        break
                    finally:
                        if token is not None:
                            _EVENT.reset(token)
                    yield item
            finally:
                token = _EVENT.set(event) if lease["active"] else None
                try:
                    await iterator.aclose()
                finally:
                    if token is not None:
                        _EVENT.reset(token)

        self._lease = lease
        self._runner_patch = (ToolLoopAgentRunner, original, scoped)
        ToolLoopAgentRunner.step = scoped

    async def terminate(self):
        self.active = False
        if self._lease is not None:
            self._lease["active"] = False
        if self._runner_patch is not None:
            cls, original, wrapper = self._runner_patch
            if cls.step is wrapper:
                cls.step = original
        self._runner_patch = None
        self._lease = None
        for section, service in reversed(list(self.services.items())):
            try:
                pending = service.terminate()
                if inspect.isawaitable(pending):
                    await pending
            except Exception as exc:
                self.owner.logger.error(
                    "%s module %s shutdown failed (%s)",
                    self.name,
                    section,
                    type(exc).__name__,
                )
        self.services.clear()
