from core.formatter import format_report, preview_url
from core.models import Confidence, SearchHit, SearchReport


def test_preview_requires_high_confidence_safe_https():
    unsafe = SearchHit(
        engine="trace.moe",
        kind="anime",
        title="A",
        thumbnail_url="https://example/a.jpg",
        confidence=Confidence.HIGH,
        adult=True,
    )
    possible = SearchHit(
        engine="SauceNAO",
        kind="illustration",
        title="B",
        thumbnail_url="https://example/b.jpg",
        confidence=Confidence.POSSIBLE,
    )
    safe = SearchHit(
        engine="SauceNAO",
        kind="illustration",
        title="C",
        thumbnail_url="https://example/c.jpg",
        confidence=Confidence.HIGH,
    )
    assert (
        preview_url(SearchReport([unsafe, possible, safe])) == "https://example/c.jpg"
    )


def test_report_mentions_multiple_images():
    hit = SearchHit(
        engine="SauceNAO",
        kind="illustration",
        title="Example",
        work_url="https://example/work",
        material="Example Series",
        characters="Alice, Bob",
        similarity=91.2,
        confidence=Confidence.HIGH,
    )
    text = format_report(SearchReport([hit]), multiple_images=True)
    assert "只查询第一张" in text
    assert "91.2%" in text
    assert "所属作品：Example Series" in text
    assert "角色：Alice, Bob" in text
    assert "作品链接：https://example/work" in text


def test_adult_result_hides_title_and_link_in_group():
    hit = SearchHit(
        engine="trace.moe",
        kind="anime",
        title="Sensitive title",
        work_url="https://example/sensitive",
        confidence=Confidence.HIGH,
        adult=True,
    )
    text = format_report(SearchReport([hit]), is_group=True)
    assert "Sensitive title" not in text
    assert "https://example/sensitive" not in text
    assert "已隐藏" in text
