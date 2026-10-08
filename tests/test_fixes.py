"""Exercise guards and memory behavior against official LivingMemory code."""

import asyncio
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace, ModuleType
from unittest.mock import AsyncMock

import pytest

OFFICIAL = Path(__file__).resolve().parents[2] / "astrbot-livingmemory-fix" / "original"
pkg = ModuleType("astrbot_plugin_livingmemory")
pkg.__path__ = [str(OFFICIAL)]
sys.modules.setdefault("astrbot_plugin_livingmemory", pkg)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_memory_recall_fix.compat import CompatibilityPatch
from astrbot_plugin_memory_recall_fix.memory_evidence import focus_recall_query, split_evidence


@pytest.fixture
def patch():
    manager = CompatibilityPatch()
    for entry in manager.manifest["patches"]:
        importlib.import_module(entry["module"])
    assert manager.apply(), manager.reason
    try:
        yield manager
    finally:
        manager.restore()


@pytest.mark.parametrize("source,expected", [
    ("/aa 介绍你记忆里的小糖豆", "小糖豆"),
    ("aa 小糖豆是谁", "小糖豆"),
    ("你还记得项目计划吗？", "项目计划"),
    ("今天好累", "今天好累"),
    ("Emily 喜欢 BLG", "Emily 喜欢 BLG"),
])
def test_focus_keeps_entities_and_ordinary_topics(source, expected):
    assert focus_recall_query(source) == expected


@pytest.mark.parametrize("source,kept", [
    ("我没有可靠的具体信息。", False),
    ("我表示资料不足，无法介绍小糖豆。", False),
    ("Emily说她缺乏资料。", True),
    ("Emily指出小糖豆不是女孩。", True),
    ("我喜欢生物。今天看了电影。", True),
])
def test_status_is_not_user_fact(source, kept):
    content, _, _ = split_evidence(source)
    assert bool(content) is kept
    if kept:
        assert content == source


def test_guard_rejects_changed_upstream_and_restores(patch):
    from astrbot_plugin_livingmemory.core.tools.memory_search_tool import MemorySearchTool
    from astrbot_plugin_livingmemory.core.managers.memory_engine_crud import MemoryEngineCrudMixin
    async def future_call(self, context, query, k=5, include_source=False):
        return "new official implementation"
    original = next(entry[2] for entry in patch.applied
                    if entry[0] is MemorySearchTool and entry[1] == "call")
    MemorySearchTool.call = future_call
    try:
        assert patch.apply() is False
        assert patch.state == "incompatible"
        assert MemorySearchTool.call is future_call
        assert not hasattr(MemoryEngineCrudMixin.search_memories, "_memory_recall_fix")
        assert not hasattr(MemoryEngineCrudMixin, "_rerank_final_results")
    finally:
        MemorySearchTool.call = original


def test_reapplying_and_unloading_preserve_official_files(patch):
    before = {p: p.read_bytes() for p in OFFICIAL.rglob("*.py")}
    assert patch.apply()
    assert len(patch.applied) == 10
    patch.restore()
    assert all(p.read_bytes() == content for p, content in before.items())
    assert patch.apply()


def result(i, score, content, metadata=None):
    from astrbot_plugin_livingmemory.core.retrieval.hybrid_retriever import HybridResult
    return HybridResult(i, score, 0, 0, 0, content, metadata or {})


@pytest.mark.asyncio
async def test_final_rerank_preserves_document_hit_after_graph_fusion(patch):
    from astrbot_plugin_livingmemory.core.managers.memory_engine_crud import MemoryEngineCrudMixin
    provider = SimpleNamespace(rerank=AsyncMock(return_value=[
        SimpleNamespace(index=1, relevance_score=.99),
        SimpleNamespace(index=0, relevance_score=.1),
    ]))
    engine = object.__new__(MemoryEngineCrudMixin)
    engine.config = {"rerank_enabled": True, "rerank_candidates": 20}
    engine.rerank_provider_resolver = lambda: provider
    rows = [result(108, 1, "最新聊天", {"contextual_mentions": ["小糖豆"]}),
            result(71, .4, "Emily转专业成为生物研究生", {"contextual_mentions": ["小糖豆"], "participants": ["Emily"]})]
    selected = await engine._rerank_final_results("小糖豆", rows, 1)
    assert [r.doc_id for r in selected] == [71]
    documents = provider.rerank.await_args.kwargs["documents"]
    assert "Emily" in documents[1] and "小糖豆" in documents[1]
    assert "最终" not in rows[0].content


@pytest.mark.asyncio
async def test_rerank_failure_keeps_available_memories(patch):
    from astrbot_plugin_livingmemory.core.managers.memory_engine_crud import MemoryEngineCrudMixin
    engine = object.__new__(MemoryEngineCrudMixin)
    engine.config = {"rerank_enabled": True}
    engine.rerank_provider_resolver = lambda: SimpleNamespace(rerank=AsyncMock(side_effect=RuntimeError("offline")))
    rows = [result(71, .9, "有效事实")]
    assert await engine._rerank_final_results("话题", rows, 5) == rows


def test_filtered_facts_and_mentions_match_injection(patch):
    from astrbot_plugin_livingmemory.core.managers.memory_engine_crud import MemoryEngineCrudMixin
    from astrbot_plugin_livingmemory.core.utils.formatting import _memory_injection_content, _memory_metadata_rows
    engine = object.__new__(MemoryEngineCrudMixin)
    row = result(71, .3, "Emily转专业成为生物研究生。", {
        "recall_atom_policy_applied": True,
        "persona_summary": "过去讨论小糖豆。过期的学校传闻。",
        "participants": ["Emily"],
        "key_facts": ["过期的学校传闻。"],
    })
    cleaned = engine._clean_recall_candidates([row], "小糖豆")[0]
    assert cleaned.metadata["contextual_mentions"] == ["小糖豆"]
    assert cleaned.metadata["key_facts"] == [cleaned.content]
    assert _memory_injection_content(cleaned.content, cleaned.metadata) == cleaned.content
    assert "过期" not in str(_memory_metadata_rows(cleaned.metadata))


def test_summary_filters_bot_uncertainty_but_keeps_user_correction(patch):
    from astrbot_plugin_livingmemory.core.processors.memory_processor_build import MemoryProcessorBuildMixin
    processor = object.__new__(MemoryProcessorBuildMixin)
    content, metadata = processor._build_storage_format("fallback", {
        "summary": "我表示资料不足，无法介绍小糖豆。Emily指出小糖豆不是女孩。",
        "key_facts": ["我没有可靠的具体信息。", "Emily指出小糖豆不是女孩。"],
    }, True)
    assert "资料不足" not in content and "没有可靠" not in content
    assert "不是女孩" in content
    assert metadata["recall_status_notes"]
    assert metadata["key_facts"] == ["Emily指出小糖豆不是女孩。"]


def test_status_only_summary_is_not_replaced_with_raw_chat(patch):
    from astrbot_plugin_livingmemory.core.processors.memory_processor_build import MemoryProcessorBuildMixin
    processor = object.__new__(MemoryProcessorBuildMixin)
    content, metadata = processor._build_storage_format("raw bot uncertainty", {
        "summary": "我表示资料不足。", "key_facts": ["我没有可靠的具体信息。"],
    }, False)
    assert metadata["recall_status_only"] is True
    assert not content


def test_caveat_only_summary_does_not_reintroduce_unfiltered_chat(patch):
    from astrbot_plugin_livingmemory.core.processors.memory_processor_build import MemoryProcessorBuildMixin
    processor = object.__new__(MemoryProcessorBuildMixin)
    caveat = "我说明这个关联只是玩笑，不足以确认身份。"
    content, metadata = processor._build_storage_format("raw unverified identity", {
        "summary": caveat, "key_facts": [caveat],
    }, False)
    assert content == caveat
    assert metadata["recall_caveats"] == [caveat]
    assert "raw" not in content


@pytest.mark.asyncio
async def test_embedding_transport_retry_creates_one_document(patch, monkeypatch):
    from astrbot_plugin_livingmemory.core.retrieval.vector_retriever import VectorRetriever
    import astrbot_plugin_livingmemory.core.retrieval.vector_retriever as module
    insert = AsyncMock(side_effect=[RuntimeError("Request timed out."), 71])
    retriever = VectorRetriever(SimpleNamespace(insert=insert))
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    assert await retriever.add_document("事实") == 71
    assert insert.await_count == 2


@pytest.mark.asyncio
async def test_non_transport_failure_does_not_retry(patch):
    from astrbot_plugin_livingmemory.core.retrieval.vector_retriever import VectorRetriever
    insert = AsyncMock(side_effect=ValueError("wrong dimension"))
    retriever = VectorRetriever(SimpleNamespace(insert=insert))
    with pytest.raises(ValueError, match="wrong dimension"):
        await retriever.add_document("事实")
    assert insert.await_count == 1
