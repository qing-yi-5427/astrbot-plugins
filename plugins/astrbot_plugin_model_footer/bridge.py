"""Request-scoped renderer metadata; no network, files or model calls."""

from contextvars import ContextVar

MODEL_KEY = "_qing_reply_model_footer"
UNKNOWN_MODEL = "未提供模型信息"
_RENDER_EVENT = ContextVar("qing_model_footer_render_event", default=None)


def field(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def clean_model(value):
    if not isinstance(value, str):
        return ""
    # Keep names readable; remove control/formatting characters from metadata.
    value = "".join(ch if ch.isprintable() else " " for ch in value)
    return " ".join(value.split())[:160]


def response_model(response):
    raw = field(response, "raw_completion")
    return clean_model(field(raw, "model")) or clean_model(field(raw, "model_version"))


def record_response(event, response, requested_model=""):
    if not response or field(response, "is_chunk", False) or field(response, "role") == "err":
        return
    model = response_model(response) or clean_model(requested_model)
    if model:
        event.set_extra(MODEL_KEY, model)


class FooterBridge:
    def __init__(self):
        self.active = False
        self._patches = []

    def _patch(self, owner, name, wrapper):
        original = getattr(owner, name)
        self._patches.append((owner, name, original, wrapper))
        setattr(owner, name, wrapper)

    def install(self, stage_class, network_class, runner_class):
        if self.active:
            return
        original_process = stage_class.process
        original_render = network_class.render_custom_template
        original_responses = runner_class._iter_llm_responses
        self.active = True

        async def process_with_event(stage, event, *args, **kwargs):
            iterator = original_process(stage, event, *args, **kwargs)
            try:
                while True:
                    # Reset before yielding: downstream stages must not inherit
                    # this reply's metadata. Each concurrent task has its own scope.
                    token = _RENDER_EVENT.set(event if self.active else None)
                    try:
                        item = await anext(iterator)
                    except StopAsyncIteration:
                        break
                    finally:
                        _RENDER_EVENT.reset(token)
                    yield item
            finally:
                token = _RENDER_EVENT.set(event if self.active else None)
                try:
                    await iterator.aclose()
                finally:
                    _RENDER_EVENT.reset(token)

        async def render_with_model(strategy, tmpl_str, tmpl_data, *args, **kwargs):
            event = _RENDER_EVENT.get() if self.active else None
            result = event.get_result() if event else None
            if result is not None and result.is_llm_result() and isinstance(tmpl_data, dict):
                model = clean_model(event.get_extra(MODEL_KEY)) or UNKNOWN_MODEL
                tmpl_data = dict(tmpl_data)
                tmpl_data["model_name"] = model
            return await original_render(strategy, tmpl_str, tmpl_data, *args, **kwargs)

        async def responses_with_model(runner, *args, **kwargs):
            # This runs after fallback provider selection. Never read the global
            # default provider at send time (it may have changed mid-request).
            requested = field(field(runner, "req"), "model") if kwargs.get("include_model", True) else None
            if not requested:
                provider = field(runner, "provider")
                get_model = getattr(provider, "get_model", None)
                requested = get_model() if callable(get_model) else ""
            iterator = original_responses(runner, *args, **kwargs)
            try:
                async for response in iterator:
                    if self.active:
                        context = field(field(runner, "run_context"), "context")
                        event = field(context, "event")
                        if event:
                            record_response(event, response, requested)
                    yield response
            finally:
                await iterator.aclose()

        self._patch(stage_class, "process", process_with_event)
        self._patch(network_class, "render_custom_template", render_with_model)
        self._patch(runner_class, "_iter_llm_responses", responses_with_model)

    def uninstall(self):
        self.active = False
        for owner, name, original, wrapper in reversed(self._patches):
            if getattr(owner, name) is wrapper:
                setattr(owner, name, original)
        self._patches.clear()
