"""Per-reply model metadata without global-provider or configuration guesses."""

# Preserve the former plugin key so a gradual migration shares the same event data.
MODEL_KEY = "_qing_reply_model_footer"
UNKNOWN_MODEL = "未提供模型信息"


def field(value, key, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def clean_model(value):
    if not isinstance(value, str):
        return ""
    value = "".join(ch if ch.isprintable() else " " for ch in value)
    return " ".join(value.split())[:160]


def response_model(response):
    raw = field(response, "raw_completion")
    return clean_model(field(raw, "model")) or clean_model(field(raw, "model_version"))


def requested_model(runner, *, include_model=True):
    # This runner has already selected its candidate. A fallback must never inherit
    # the primary provider's explicit model override from the shared request.
    requested = (
        clean_model(field(field(runner, "req"), "model")) if include_model else ""
    )
    if requested:
        return requested
    get_model = getattr(field(runner, "provider"), "get_model", None)
    if not callable(get_model):
        return ""
    try:
        return clean_model(get_model())
    except Exception:  # noqa: BLE001
        # Metadata inspection must not interrupt an otherwise valid LLM request.
        return ""


def record_response(event, response, requested_model=""):
    if (
        response is None
        or field(response, "is_chunk", False)
        or field(response, "role") in ("err", "error")
    ):
        return
    model = response_model(response) or clean_model(requested_model)
    if model:
        event.set_extra(MODEL_KEY, model)
