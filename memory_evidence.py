"""Keep retrieval status separate from evidence and preserve entity clues."""

import re


def focus_recall_query(query: str) -> str:
    text = re.sub(r"^/?aa\s+", "", query.strip(), flags=re.I)
    patterns = (
        r"^(?:请|麻烦)?(?:介绍|说说|讲讲|回忆)(?:一下)?你(?:的)?(?:记忆里|记忆中|印象里|记得)(?:的)?(.+?)[。？！!?]*$",
        r"^(?:你)?(?:还)?记得(.+?)(?:吗|么)[。？！!?]*$",
        r"^([\w\u4e00-\u9fff]{2,24})(?:是谁|是什么人)[。？！!?]*$",
    )
    for pattern in patterns:
        match = re.match(pattern, text)
        if match and len(match[1].strip()) >= 2:
            return match[1].strip()
    return text


def evidence_kind(text: str) -> str:
    """Classify only explicit assistant recall status or identity caveats."""
    uncertainty = r"无(?:任何)?(?:证据|依据)|不能据此确认|不足以确认|不构成可靠佐证|只是玩笑|无法确认|未确认.{0,12}身份"
    identity = r"身份|性别|全名|关联|猜测|就是|少女|群友"
    if re.search(uncertainty, text) and re.search(identity, text):
        return "caveat"
    actor = r"我(?:表示|说明|承认|坦白|明确|回应|回复|也|没有|没|不|无法|不能|记忆里|这边|目前)|机器人|助手|记忆库|\bbot\b"
    status = r"资料不足|缺乏.{0,12}(?:信息|资料)|没.{0,12}(?:可靠|具体).{0,8}(?:信息|资料)|没有.{0,16}(?:资料|信息|记忆)|(?:未|没|没有).{0,8}检索到|召回结果.{0,20}(?:仅|只)|无法.{0,12}(?:召回|回忆)|记忆清零"
    if re.search(actor, text, re.I) and re.search(status, text):
        return "status"
    if re.search(r"\b(?:I|the assistant|the bot)\b", text, re.I) and re.search(
        r"cannot recall|could not recall|couldn't recall|no (?:reliable )?(?:memory|information)|insufficient (?:information|evidence)|memory search (?:failed|returned no)",
        text,
        re.I,
    ):
        return "status"
    return "fact"


def split_evidence(text: str) -> tuple[str, list[str], list[str]]:
    facts, caveats, statuses = [], [], []
    for part in re.split(r"(?<=[。！？!?；;])\s*|\n+|\s+\|\s+", str(text or "")):
        if not part.strip():
            continue
        kind = evidence_kind(part)
        (caveats if kind == "caveat" else statuses if kind == "status" else facts).append(part.strip())
    return (str(text or "").strip() if not caveats and not statuses else "\n".join(facts)), caveats, statuses


def contextual_mentions(query: str, summary: str) -> list[str]:
    """Return matched names/topics, never infer a nickname-to-person identity."""
    focused = focus_recall_query(query)
    return list(dict.fromkeys(
        token for token in re.findall(r"[\w\u4e00-\u9fff-]+", focused)
        if 2 <= len(token) <= 32 and token.casefold() in summary.casefold()
    ))[:8]


def is_embedding_transport_error(exc: Exception) -> bool:
    name = type(exc).__name__
    return name in {"APITimeoutError", "APIConnectionError", "ConnectTimeout", "ReadTimeout", "ConnectError"} or str(exc).strip().lower() in {
        "request timed out.", "request timed out", "connection error.", "connection error",
    }
