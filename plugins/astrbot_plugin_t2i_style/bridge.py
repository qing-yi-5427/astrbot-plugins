"""Unified request-scoped styles and model metadata with revocable wrappers."""

from contextvars import ContextVar

from .core import TEMPLATES
from .models import (
    MODEL_KEY,
    UNKNOWN_MODEL,
    clean_model,
    field,
    record_response,
    requested_model,
)

BRAND_MARKER = '<div class="brand-name">Amadeus</div>'
_RENDER_EVENT = ContextVar("t2i_style_render_event", default=None)


class StyleBridge:
    def __init__(
        self,
        resolver,
        capability_check=None,
        theme_resolver=None,
        theme_capability_check=None,
    ):
        self.resolver = resolver
        self.capability_check = capability_check
        self.theme_resolver = theme_resolver
        self.theme_capability_check = theme_capability_check
        self.active = False
        self._patches = []
        self._lifetime = None

    def install(self, stage_class, network_class, runner_class=None):
        if self.active:
            return
        original_process = stage_class.process
        original_render = network_class.render_custom_template
        original_responses = (
            runner_class._iter_llm_responses if runner_class is not None else None
        )
        # Each installation has its own permanently revocable lease. Reinstalling
        # cannot reactivate wrappers retained by another plugin's original chain.
        lifetime = {"active": True}
        self._lifetime = lifetime
        self.active = True
        resolver = self.resolver
        capability_check = self.capability_check
        theme_resolver = self.theme_resolver
        theme_capability_check = self.theme_capability_check

        async def process_with_event(stage, event, *args, **kwargs):
            iterator = original_process(stage, event, *args, **kwargs)
            try:
                while True:
                    token = _RENDER_EVENT.set(event) if lifetime["active"] else None
                    try:
                        item = await anext(iterator)
                    except StopAsyncIteration:
                        break
                    finally:
                        if token is not None:
                            _RENDER_EVENT.reset(token)
                    yield item
            finally:
                token = _RENDER_EVENT.set(event) if lifetime["active"] else None
                try:
                    await iterator.aclose()
                finally:
                    if token is not None:
                        _RENDER_EVENT.reset(token)

        async def render_with_style(strategy, tmpl_str, tmpl_data, *args, **kwargs):
            event = _RENDER_EVENT.get() if lifetime["active"] else None
            result = event.get_result() if event is not None else None
            if (
                result is not None
                and result.is_llm_result()
                and isinstance(tmpl_str, str)
                and BRAND_MARKER in tmpl_str
                and isinstance(tmpl_data, dict)
            ):
                session = getattr(event, "unified_msg_origin", None)
                endpoint = kwargs.get("endpoint", args[2] if len(args) > 2 else None)
                supported = (
                    (await capability_check(strategy, endpoint=endpoint))
                    if capability_check is not None
                    else True
                )
                style = None
                theme = None
                if supported and isinstance(session, str) and session:
                    style = dict(resolver(session))
                    theme = (
                        theme_resolver(style["template"])
                        if theme_resolver is not None
                        else None
                    )
                    if (
                        theme is not None
                        and theme_capability_check is not None
                        and not await theme_capability_check(
                            strategy, endpoint=endpoint
                        )
                    ):
                        theme = None
                        if style["template"] not in TEMPLATES:
                            style["template"] = "codex"
                # A plugin may be disabled while the capability request is in flight.
                if lifetime["active"]:
                    tmpl_data = dict(tmpl_data)
                    tmpl_data["model_name"] = (
                        clean_model(event.get_extra(MODEL_KEY)) or UNKNOWN_MODEL
                    )
                    if style is not None:
                        tmpl_data["t2i_style"] = style
                    if theme is not None:
                        tmpl_data["t2i_theme"] = theme
            return await original_render(strategy, tmpl_str, tmpl_data, *args, **kwargs)

        async def responses_with_model(runner, *args, **kwargs):
            requested = (
                requested_model(runner, include_model=kwargs.get("include_model", True))
                if lifetime["active"]
                else ""
            )
            iterator = original_responses(runner, *args, **kwargs)
            try:
                async for response in iterator:
                    if lifetime["active"]:
                        context = field(field(runner, "run_context"), "context")
                        event = field(context, "event")
                        if event is not None:
                            record_response(event, response, requested)
                    yield response
            finally:
                await iterator.aclose()

        patches = [
            (stage_class, "process", original_process, process_with_event),
            (
                network_class,
                "render_custom_template",
                original_render,
                render_with_style,
            ),
        ]
        if runner_class is not None:
            patches.append(
                (
                    runner_class,
                    "_iter_llm_responses",
                    original_responses,
                    responses_with_model,
                )
            )
        for owner, name, original, wrapper in patches:
            setattr(owner, name, wrapper)
            self._patches.append((owner, name, original, wrapper))

    def uninstall(self):
        self.active = False
        if self._lifetime is not None:
            self._lifetime["active"] = False
        for owner, name, original, wrapper in reversed(self._patches):
            # Do not overwrite later plugins or inspect their private closures.
            # Retained wrappers are inert even if a later uninstall restores one.
            if getattr(owner, name) is wrapper:
                setattr(owner, name, original)
        self._patches.clear()
        self._lifetime = None
