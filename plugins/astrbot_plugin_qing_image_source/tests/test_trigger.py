from core.config import TriggerSettings
from core.image_resolver import select_image_component
from core.trigger import TriggerMatcher


class Plain:
    def __init__(self, text):
        self.text = text


class At:
    def __init__(self, qq):
        self.qq = qq


class Image:
    pass


class Reply:
    def __init__(self, chain):
        self.chain = chain


def test_keyword_matches_without_at_by_default():
    matcher = TriggerMatcher(TriggerSettings())
    result = matcher.match([Plain("帮我搜图一下")], is_group=True, self_id="42")
    assert result.matched
    assert result.route == "auto"


def test_required_at_rejects_other_target():
    matcher = TriggerMatcher(TriggerSettings(require_at_in_group=True))
    assert not matcher.match(
        [At("99"), Plain("搜图")], is_group=True, self_id="42"
    ).matched
    assert matcher.match([At("42"), Plain("搜图")], is_group=True, self_id="42").matched


def test_force_engine_keyword():
    matcher = TriggerMatcher(TriggerSettings())
    result = matcher.match([Plain("TRACE")], is_group=False, self_id="42")
    assert result.matched
    assert result.route == "tracemoe"


def test_reply_image_has_priority():
    current = Image()
    quoted = Image()
    result = select_image_component([current, Reply([quoted])])
    assert result is not None
    assert result.component is quoted
    assert result.origin == "reply"
    assert result.total_count == 2
