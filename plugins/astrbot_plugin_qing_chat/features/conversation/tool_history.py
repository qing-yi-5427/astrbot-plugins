"""Deterministic excerpts of old execution results; no model calls or turn removal."""

import json

DEFAULT_TOOLS = (
    "astrbot_execute_shell",
    "astrbot_shell_session",
    "astrbot_file_read_tool",
    "astrbot_execute_python",
)
PROTECTED_TOOLS = {"astrbot_generate_image", "recall_long_term_memory", "memory_search"}
MARKER = (
    "仅为历史工具结果的首尾节选，中间内容已省略；不是完整输出，勿据此自动重复执行操作。"
)


def bounded_int(config, name, default, minimum, maximum):
    """Read a bounded integer shared by the history and deadline settings.

    Args:
        config: Plugin configuration mapping.
        name: Configuration key.
        default: Fallback for malformed values.
        minimum: Smallest allowed value.
        maximum: Largest allowed value.

    Returns:
        A validated integer.
    """
    value = config.get(name, default)
    try:
        value = default if isinstance(value, bool) else int(value)
    except (TypeError, ValueError, OverflowError):
        value = default
    return min(max(value, minimum), maximum)


def compact_tool_history(contexts, config):
    """Shorten only matched, old, allowlisted text results in a request copy.

    Args:
        contexts: Standard AstrBot chat history dictionaries.
        config: Settings for enablement, recent results, length and tool names.

    Returns:
        A request-scoped history list, changed result count and removed characters.
    """
    if not config.get("compact_tool_history", True) or not isinstance(contexts, list):
        return contexts, 0, 0
    names = config.get("compact_tool_names", list(DEFAULT_TOOLS))
    if not isinstance(names, (list, tuple)):
        names = DEFAULT_TOOLS
    allowed = {name for name in names if isinstance(name, str)} - PROTECTED_TOOLS
    keep = bounded_int(config, "keep_recent_tool_results", 8, 0, 100)
    limit = bounded_int(config, "old_tool_result_chars", 1200, 500, 10000)
    calls, duplicates = {}, set()
    for message in contexts:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        rows = message.get("tool_calls")
        if not isinstance(rows, list):
            continue
        for call in rows:
            if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
                continue
            identity = call.get("id")
            name = call["function"].get("name")
            if (
                not isinstance(identity, str)
                or not identity
                or not isinstance(name, str)
            ):
                continue
            if identity in calls:
                duplicates.add(identity)
            calls[identity] = name
    candidates = [
        (index, message)
        for index, message in enumerate(contexts)
        if isinstance(message, dict)
        and message.get("role") == "tool"
        and isinstance(message.get("tool_call_id"), str)
        and message.get("tool_call_id") not in duplicates
        and calls.get(message.get("tool_call_id")) in allowed
        and isinstance(message.get("content"), str)
    ]
    old = candidates[:-keep] if keep else candidates
    result, count, removed = contexts, 0, 0
    for index, message in old:
        content = message["content"]
        if len(content) <= limit:
            continue
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict) and parsed.get("_tool_history_compacted") is True:
            continue
        excerpt = {
            "_tool_history_compacted": True,
            "original_chars": len(content),
            "note": MARKER,
        }
        if isinstance(parsed, dict):
            # Preserve short status/identity fields verbatim, never invent facts.
            for key in (
                "session_id",
                "pid",
                "status",
                "exit_code",
                "cursor",
                "has_more",
                "session_closed",
                "path",
                "file_path",
                "http_status",
                "error",
            ):
                value = parsed.get(key)
                if key in parsed and (
                    value is None
                    or isinstance(value, (int, float, bool))
                    or isinstance(value, str)
                    and len(value) <= 256
                ):
                    excerpt[key] = value
        size = max(0, limit - len(json.dumps(excerpt, ensure_ascii=False)) - 50)
        while size > 0:
            head = size * 2 // 3
            excerpt["excerpt"] = (
                content[:head] + "\n…[中间内容已省略]…\n" + content[-(size - head) :]
            )
            serialized = json.dumps(excerpt, ensure_ascii=False)
            if len(serialized) <= limit:
                break
            size -= max(16, len(serialized) - limit)
        else:
            # Unusually large metadata is not silently corrupted to fit a cap.
            continue
        if result is contexts:
            result = list(contexts)
        result[index] = {**message, "content": serialized}
        count += 1
        removed += len(content) - len(serialized)
    return result, count, removed
