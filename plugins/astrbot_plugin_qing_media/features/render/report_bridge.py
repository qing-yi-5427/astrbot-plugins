"""Make group reports follow session themes using prepared, privacy-filtered data."""

import html
import inspect
import re
from contextvars import ContextVar
from functools import wraps
from html.parser import HTMLParser

REPORT_PLUGIN = "astrbot_plugin_qq_group_daily_analysis"


class VisibleFragment(HTMLParser):
    """Keep text/mention identity, never duplicate the HTML's avatar data/URLs."""

    ALLOWED = frozenset(
        {
            "p",
            "div",
            "span",
            "strong",
            "em",
            "b",
            "i",
            "a",
            "br",
            "ul",
            "ol",
            "li",
            "code",
            "blockquote",
        }
    )
    VOID = frozenset({"img", "br", "hr", "input", "meta", "link", "source", "wbr"})
    BLOCKED = frozenset(
        {"script", "style", "svg", "template", "iframe", "object", "noscript"}
    )

    def __init__(self, value):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []
        self.feed(value)
        self.close()
        while self.stack:
            _, emitted, blocked = self.stack.pop()
            if emitted and not blocked:
                self.parts.append("</" + emitted + ">")

    def handle_starttag(self, tag, attrs):
        if len(self.stack) > 128:
            raise ValueError("Report fragment nesting limit")
        attrs = dict(attrs)
        blocked = (
            (self.stack[-1][2] if self.stack else False)
            or tag in self.BLOCKED
            or "hidden" in attrs
            or attrs.get("aria-hidden") == "true"
            or bool(
                re.search(
                    r"display\s*:\s*none|visibility\s*:\s*hidden",
                    attrs.get("style") or "",
                    re.IGNORECASE,
                )
            )
        )
        emitted = tag if not blocked and tag in self.ALLOWED else ""
        if emitted:
            mention = (
                ' class="user-capsule"'
                if tag == "span"
                and "user-capsule" in (attrs.get("class") or "").split()
                else ""
            )
            self.parts.append("<" + tag + mention + ">")
        if tag not in self.VOID:
            self.stack.append((tag, emitted, blocked))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                for _, emitted, blocked in reversed(self.stack[index:]):
                    if emitted and not blocked:
                        self.parts.append("</" + emitted + ">")
                del self.stack[index:]
                break

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_data(self, value):
        if not self.stack or not self.stack[-1][2]:
            self.parts.append(html.escape(value, quote=False))


def visible_fragment(value):
    if not isinstance(value, str):
        raise TypeError("Report fragment must be text")
    return "".join(VisibleFragment(value).parts)


def fields(value, names):
    return {name: value[name] for name in names if name in value}


class Snapshot:
    """Capture only the values actually passed to the report's visible templates."""

    def __init__(self):
        self.data = {"schema": 1}
        self.invalid = False

    def capture(self, name, data):
        if name == "topic_item.html":
            self.data["topics"] = [
                {
                    "title": item["topic"]["topic"],
                    **fields(item, ("index", "contributors", "detail")),
                }
                for item in data.get("topics", [])
            ]
            for item in self.data["topics"]:
                for key in ("contributors", "detail"):
                    if key in item:
                        item[key] = (
                            html.escape(item[key], quote=False)
                            if key == "contributors"
                            and not data.get("hide_user_names", False)
                            else visible_fragment(item[key])
                        )
        elif name == "user_title_item.html":
            self.data["people"] = [
                fields(item, ("name", "title", "mbti", "profile_display", "reason"))
                for item in data.get("titles", [])
            ]
            for item in self.data["people"]:
                if "reason" in item:
                    item["reason"] = (
                        visible_fragment(item["reason"])
                        if data.get("hide_user_names", False)
                        else html.escape(item["reason"], quote=False)
                    )
        elif name == "quote_item.html":
            self.data["quotes"] = [
                fields(item, ("sender", "content", "reason"))
                for item in data.get("quotes", [])
            ]
            for item in self.data["quotes"]:
                if "reason" in item:
                    item["reason"] = visible_fragment(item["reason"])
        elif name == "activity_chart.html":
            self.data["hours"] = [
                fields(item, ("hour", "count")) for item in data.get("chart_data", [])
            ]
        elif name == "chat_quality_item.html":
            self.data["quality"] = fields(data, ("title", "subtitle", "summary"))
            self.data["quality"]["dimensions"] = [
                fields(item, ("name", "percentage", "comment"))
                for item in data.get("dimensions", [])
            ]
        elif name == "image_template.html":
            self.data["summary"] = fields(
                data,
                (
                    "current_date",
                    "current_datetime",
                    "message_count",
                    "participant_count",
                    "total_characters",
                    "emoji_count",
                    "most_active_period",
                    "total_tokens",
                    "prompt_tokens",
                    "completion_tokens",
                ),
            )

    def ready(self):
        return not self.invalid and "summary" in self.data and "hours" in self.data


class ReportBridge:
    def __init__(
        self,
        resolver,
        theme_resolver,
        capability_check,
        endpoint_resolver=None,
        session_check=None,
    ):
        self.session_check = session_check
        self.resolver = resolver
        self.theme_resolver = theme_resolver
        self.capability_check = capability_check
        self.endpoint_resolver = endpoint_resolver or (lambda session: None)
        self.active = True
        self.scope = ContextVar("t2i_report_scope", default=None)
        self.patches = []
        self.owners = {}

    def attach(self, metadata):
        if (
            not self.active
            or getattr(metadata, "name", None) != REPORT_PLUGIN
            or not getattr(metadata, "activated", True)
        ):
            return False
        instance = getattr(metadata, "star_cls", None)
        generator = getattr(instance, "report_generator", None)
        if generator is not None:
            for old in tuple(self.owners):
                if old is not generator:
                    self.detach_generator(old)
        return self.attach_generator(generator) if generator is not None else False

    def attach_generator(self, generator):
        if not self.active or generator in self.owners:
            return False
        templates = getattr(generator, "html_templates", None)
        original = getattr(generator, "generate_image_report", None)
        original_template = getattr(templates, "render_template", None)
        if not callable(original) or not callable(original_template):
            return False
        signature = inspect.signature(original)
        if not {"html_render_func", "group_id"} <= set(signature.parameters):
            return False
        bridge = self
        lease = {"active": True}

        @wraps(original_template)
        def capture_template(template_name, *args, **kwargs):
            result = original_template(template_name, *args, **kwargs)
            scope = bridge.scope.get() if bridge.active and lease["active"] else None
            if result and scope is not None and scope["owner"] is generator:
                try:
                    scope["snapshot"].capture(template_name, kwargs)
                except (KeyError, TypeError, ValueError, AttributeError):
                    scope["snapshot"].invalid = True
            return result

        @wraps(original)
        async def themed_report(*args, **kwargs):
            if not bridge.active or not lease["active"]:
                return await original(*args, **kwargs)
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            render = bound.arguments["html_render_func"]
            namespace = bound.arguments.get("avatar_cache_namespace")
            group_id = str(bound.arguments["group_id"])
            session = (
                f"{namespace}:GroupMessage:{group_id}"
                if isinstance(namespace, str) and namespace
                else None
            )
            if bridge.session_check is not None and not await bridge.session_check(
                session
            ):
                return await original(*args, **kwargs)
            scope = {"owner": generator, "snapshot": Snapshot()}

            @wraps(render)
            async def render_with_theme(tmpl, data, *render_args, **render_kwargs):
                endpoint = bridge.endpoint_resolver(session)
                supported = await bridge.capability_check(endpoint=endpoint)
                if (
                    bridge.active
                    and lease["active"]
                    and supported
                    and scope["snapshot"].ready()
                ):
                    style = dict(bridge.resolver(session))
                    data = {
                        **data,
                        "t2i_style": style,
                        "t2i_report": scope["snapshot"].data,
                    }
                    tmpl = ""  # The new protocol already has all visible data.
                    theme = bridge.theme_resolver(style["template"])
                    if theme is not None:
                        data["t2i_theme"] = theme
                    # Use the same session endpoint as the selected style when the
                    # native Star.html_render callback exposes the UMO parameter.
                    if session and "umo" in inspect.signature(render).parameters:
                        render_kwargs = {**render_kwargs, "umo": session}
                return await render(tmpl, data, *render_args, **render_kwargs)

            bound.arguments["html_render_func"] = render_with_theme
            token = bridge.scope.set(scope)
            try:
                return await original(*bound.args, **bound.kwargs)
            finally:
                bridge.scope.reset(token)

        for owner, name, old, new in (
            (templates, "render_template", original_template, capture_template),
            (generator, "generate_image_report", original, themed_report),
        ):
            setattr(owner, name, new)
            self.patches.append((owner, name, old, new))
        self.owners[generator] = lease
        return True

    def detach_generator(self, generator):
        lease = self.owners.pop(generator, None)
        if lease is None:
            return
        lease["active"] = False
        targets = (generator, getattr(generator, "html_templates", None))
        for owner, name, original, wrapper in reversed(self.patches):
            if owner in targets and getattr(owner, name, None) is wrapper:
                setattr(owner, name, original)
        self.patches = [patch for patch in self.patches if patch[0] not in targets]

    def uninstall(self):
        self.active = False
        for owner, name, original, wrapper in reversed(self.patches):
            if getattr(owner, name, None) is wrapper:
                setattr(owner, name, original)
        self.patches.clear()
        self.owners.clear()
