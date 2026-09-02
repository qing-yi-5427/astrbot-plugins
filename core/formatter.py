from __future__ import annotations

from urllib.parse import urlparse

from .models import Confidence, SearchHit, SearchReport


def _format_time(seconds: float | None) -> str:
    if seconds is None:
        return ""
    total = max(0, int(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def _safe_preview(hit: SearchHit) -> str:
    if hit.adult or hit.confidence is not Confidence.HIGH:
        return ""
    try:
        parsed = urlparse(hit.thumbnail_url)
    except ValueError:
        return ""
    return hit.thumbnail_url if parsed.scheme == "https" and parsed.netloc else ""


def preview_url(report: SearchReport) -> str:
    for hit in report.hits:
        url = _safe_preview(hit)
        if url:
            return url
    return ""


def format_report(
    report: SearchReport,
    *,
    multiple_images: bool = False,
    is_group: bool = False,
    allow_adult_links_in_private: bool = False,
) -> str:
    if not report.hits:
        lines = ["没有找到可信度足够的来源。"]
        if report.warnings:
            lines.append("提示：" + "；".join(dict.fromkeys(report.warnings)))
        return "\n".join(lines)

    labels = {
        Confidence.HIGH: "高可信",
        Confidence.POSSIBLE: "可能",
        Confidence.LOW: "低可信",
    }
    lines = ["🔎 搜图结果" + ("（缓存）" if report.cache_hit else "")]
    if multiple_images:
        lines.append("检测到多张图片，本次只查询第一张。")
    for index, hit in enumerate(report.hits, 1):
        score = f" · {hit.similarity:.1f}%" if hit.similarity is not None else ""
        lines.append(f"\n{index}. [{labels[hit.confidence]} · {hit.engine}{score}]")
        if hit.adult and (is_group or not allow_adult_links_in_private):
            lines.append("内容：疑似成人内容，标题、链接和预览已隐藏")
            continue
        lines.append(f"作品：{hit.title}")
        if hit.creator:
            lines.append(f"作者：{hit.creator}")
        if hit.episode:
            lines.append(f"集数：{hit.episode}")
        if hit.from_seconds is not None:
            end = _format_time(hit.to_seconds)
            span = _format_time(hit.from_seconds)
            lines.append(f"时间：{span}" + (f" - {end}" if end else ""))
        if hit.adult:
            lines.append("内容：疑似成人内容，已隐藏预览")
        if hit.source_url:
            lines.append(f"来源：{hit.source_url}")
        else:
            lines.append("来源：该引擎未返回可点击原链接")
    if report.warnings:
        lines.append("\n提示：" + "；".join(dict.fromkeys(report.warnings)))
    return "\n".join(lines)
