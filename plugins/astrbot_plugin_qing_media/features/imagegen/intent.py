"""Recognize explicit image instructions in the current user message only."""

import re

_PREFIX = re.compile(r"^(?:[!/＃#]aa|aa)\s+", re.I)
_ASK_PREFIX = re.compile(
    r"^(?:(?:请|麻烦|帮我|给我|替我|为我|我想要|我想|现在|重新|再|来|一下|你|能不能|能否|可以|能|要)\s*)*"
)
_META = re.compile(
    r"怎么|如何|教程|教我|插件|脚本|代码|方法|原理|文案|描述这|评价|解释",
    re.I,
)
_PROMPT_ONLY = re.compile(r"(?:只要|只给|仅需|仅给|只写).{0,8}(?:提示词|prompt)", re.I)
_CANCEL = re.compile(
    r"(?:不要|不用|别|不必|无需|不想|停止|取消|先别).{0,8}(?:画|绘|生成|生图|制作)|"
    r"(?:don't|do not|never|stop|cancel)\s+(?:draw|generate|create|make)",
    re.I,
)
_ABILITY = re.compile(
    r"^(?:你|bot|机器人)?(?:会|能|可以|能不能|是否可以|是否能|能否)"
    r"(?:画图|绘画|生图|生成(?:图片|图像)|制作(?:图片|图像))(?:吗|么)?[?？]?$",
    re.I,
)
_QUOTED = re.compile(
    r"```[\s\S]*?(?:```|\Z)|~~~[\s\S]*?(?:~~~|\Z)|"
    r"(?m:^[ \t]*>[^\n]*)|`[^`]*(?:`|\Z)|"
    r"“[^”]*(?:”|\Z)|‘[^’]*(?:’|\Z)|「[^」]*(?:」|\Z)|『[^』]*(?:』|\Z)|"
    r'"(?:[^"\\]|\\.)*(?:"|\Z)|(?<!\w)\'[^\'\n]*(?:\'|$)'
)
_INDIRECT = re.compile(
    r"(?:他|她|他们|她们|有人|别人|群友|朋友|网友|用户)(?:刚才|之前|曾经)?"
    r"(?:说|问|写|发|要求|让我)|"
    r"(?:示例|例句|原文|台词|提示词|指令|命令|教程|代码|脚本|插件|文案|方法|原理)"
    r"[^。！？!?；;\n]{0,16}[:：]|"
    r"怎么|如何|教我|解释|讨论|评价|假如|假设|如果|例如|比如|举例|转述|复述|翻译|分析|"
    r"代码|脚本|插件|教程|方法|原理",
    re.I,
)
_STOP_IMAGE = re.compile(
    r"^(?:算了[，,]?\s*)?(?:不要|不用|别|不必|无需|不想|停止|取消|先别)"
    r"(?:再|继续)?(?:画图|生图|生成(?:图片|图像)?|画|绘制|绘画|制作(?:图片|图像))"
    r"(?:了|啦|吧)?\s*(?:$|[，,。！？!?；;\n])|"
    r"^(?:don't|do not|never|stop|cancel)\s+"
    r"(?:draw(?:ing)?|generat(?:e|ing)|creat(?:e|ing)|mak(?:e|ing))"
    r"(?:\s+(?:an?\s+|any\s+)?(?:images?|pictures?))?(?:\s+(?:now|anymore))?"
    r"\s*(?:$|[，,。.!！?？；;\n])",
    re.I,
)
_CANDIDATE_START = re.compile(
    _ASK_PREFIX.pattern[1:]
    + r"(?:画|绘|生成|制作|产出|创作|做|出|根据|按照|按|结合|参考|以)|"
    r"(?:(?:please|can you|could you|would you|help me)\s+)*"
    r"(?:draw|paint|generate|create|make|based on|using|according to)\b|"
    r"(?:算了|不要|不用|别|不必|无需|不想|停止|取消|先别)|"
    r"(?:don't|do not|never|stop|cancel)\b",
    re.I,
)


def explicit_image_prompt(message: str) -> str | None:
    """Find a current instruction after optional descriptive clauses.

    Args:
        message: Plain text from the triggering message, without recalled history.

    Returns:
        The complete current message without its wake prefix, or None. Quoted
        examples, indirect requests and standalone cancellation clauses do not
        authorize generation.
    """
    text = _PREFIX.sub("", message.strip(), count=1)
    if (
        not text
        or len(text) > 32000
        or _CANCEL.match(text)
        or _PROMPT_ONLY.search(text)
    ):
        return None
    # Keep offsets stable: punctuation inside quotations/code cannot introduce
    # a new instruction, but quoted subjects remain in the original prompt.
    visible = _QUOTED.sub(lambda match: "_" + " " * (len(match.group()) - 1), text)
    requested = False
    sentence_start = 0
    for boundary in re.finditer(r"(?:^|[，,。！？!?；;\n]|\.(?=\s|$))\s*", visible):
        if any(char in boundary.group() for char in "。！？!?；;."):
            sentence_start = boundary.end()
        start = boundary.end()
        # Most chat clauses cannot start an instruction. Reject them before
        # slicing/scanning their remaining text, including very long messages.
        if not _CANDIDATE_START.match(visible, start):
            continue
        candidate = visible[start:]
        if _INDIRECT.search(visible[sentence_start:start]):
            continue
        # A subject restriction is not a blanket cancellation; a standalone
        # "stop drawing" is, even after a request.
        if _STOP_IMAGE.match(candidate):
            return None
        if _direct_image_prompt(candidate):
            requested = True
    return text if requested else None


def _direct_image_prompt(text: str) -> str | None:
    """Recognize an instruction at the start of a candidate clause.

    Args:
        text: A suffix of the current message at an unquoted clause boundary.

    Returns:
        The candidate when it is an explicit image request, otherwise None.
    """
    if (
        not text
        or len(text) > 32000
        or _CANCEL.match(text)
        or _PROMPT_ONLY.search(text)
        or _ABILITY.fullmatch(
            re.split(r"[。.!！?？；;\n]", text, maxsplit=1)[0].strip()
        )
    ):
        return None
    body = _ASK_PREFIX.sub("", text, count=1)
    # Explicit instructions may put their reference/context before the action.
    # Do not infer an action from conversation history, a nickname or an image topic.
    if re.match(r"^(?:根据|按照|按|结合|参考|以)", body):
        action = re.search(
            r"(?:[，,:：]\s*|(?:记忆|印象|理解|要求|描述|风格|内容|资料|信息)\s*)"
            r"((?:(?:请|帮我|给我|为我)\s*)?(?:画|绘制|绘画|创作|生成|制作)(?!的|过|法|师|家|展|质|面)\s*\S.*)$",
            body,
        )
        if action:
            body = _ASK_PREFIX.sub("", action.group(1), count=1)
    # Inspect the requested action, rather than restrictions or text to paint in
    # later clauses: "画一张猫，不要画狗" remains an explicit drawing request.
    head = re.split(r"[，,;；。\n]", body, maxsplit=1)[0]
    if _META.search(head):
        return None
    if re.match(r"^(?:画图|绘画)(?:是|很|真|需要|让|使|会|能|的时候)", body):
        return None
    if re.match(r"^(?:画|绘制|绘画)\s*\S", body):
        if not re.match(
            r"^(?:画)(?:个|一个|一下|一|出)?(?:重点|饼|句号|勾|上句号)", body
        ):
            return text
    if re.match(r"^(?:生成|制作|产出|创作|做|出)", body) and re.search(
        r"图(?:片|像|画|标)?|照片|插画|海报|壁纸|头像|漫画|logo|icon|"
        r"gif(?:动图|表情包)?(?=$|[\s，,。.!！?？；;])",
        body[:80],
        re.I,
    ):
        return text
    # A named subject with an explicit visual requirement is also an image
    # instruction, even when the user omits the word "image" after "one".
    if (
        re.match(r"^生成\s*一张\s*\S", head)
        and not re.search(
            r"表格|清单|报表|发票|借条|账单|证|票|卡|单据|Excel|PPT", head, re.I
        )
        and re.search(
            r"[，,;；]\s*(?:要有|带有|包含|加上).{1,40}(?:要素|风格|构图)", body
        )
    ):
        return text
    english = re.sub(
        r"^(?:(?:please|can you|could you|would you|help me)\s+)+", "", text, flags=re.I
    )
    if re.match(r"^(?:based on|using|according to)\b", english, re.I):
        action = re.search(
            r"\b((?:draw|paint|generate|create|make)\s+.+)$", english, re.I
        )
        if action:
            english = action.group(1)
    if re.match(r"^draw\s+\S", english, re.I):
        if re.match(
            r"^draw\s+(?:(?:a|the|some)\s+)?(?:conclusions?|comparisons?|distinctions?|money)\b",
            english,
            re.I,
        ):
            return None
        return text
    if re.match(r"^(?:generate|create|make|paint)\b", english, re.I) and re.search(
        r"\b(?:image|picture|photo|illustration|poster|wallpaper|avatar|logo|icon|gif)\b",
        english[:120],
        re.I,
    ):
        return text
    return None
