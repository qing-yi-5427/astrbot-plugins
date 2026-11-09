import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from astrbot_plugin_t2i_style import capabilities
from conftest import FakeResponse, fake_session


@pytest.mark.parametrize(
    "value, normalized",
    [
        ("http://t2i-lite:8000", "http://t2i-lite:8000/text2img"),
        ("http://t2i-lite:8000/text2img/", "http://t2i-lite:8000/text2img"),
        ("https://example.test/text2img/generate/", "https://example.test/text2img"),
    ],
)
def test_endpoint_normalization(value, normalized):
    assert capabilities.normalize_endpoint(value) == normalized


@pytest.mark.parametrize(
    "value",
    [
        "",
        None,
        "file:///etc/passwd",
        "https://user:secret@host",
        "https://host?a=secret",
        "http://host:bad",
        "http://host/#secret",
        "http://host/\npath",
    ],
)
def test_invalid_endpoint(value):
    with pytest.raises(ValueError):
        capabilities.normalize_endpoint(value)


@pytest.mark.asyncio
async def test_bounded_health_probe_and_no_credentials(monkeypatch):
    calls = fake_session(
        monkeypatch,
        capabilities,
        FakeResponse(json.dumps({"style_protocol": 1}).encode()),
    )
    supported, message = await capabilities.probe_health(
        "http://t2i-lite:8000/text2img"
    )
    assert supported and "style_protocol: 1" in message
    assert calls[1] == (
        "http://t2i-lite:8000/text2img/health",
        {"allow_redirects": False},
    )
    assert calls[0][1]["trust_env"] is False
    assert calls[0][1]["timeout"].total == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(b"{}"),
        FakeResponse(b'{"style_protocol": true}'),
        FakeResponse(b'{"style_protocol": "1"}'),
        FakeResponse(b'{"style_protocol": 2}'),
        FakeResponse(b"[]"),
        FakeResponse(b"not JSON"),
        FakeResponse(status=302),
        FakeResponse(b"x", content_length=16385),
        FakeResponse(b"x" * 16385),
        aiohttp.ClientConnectionError(),
        TimeoutError(),
    ],
)
async def test_unsupported_or_bad_service_never_claims_success(monkeypatch, response):
    fake_session(monkeypatch, capabilities, response)
    supported, message = await capabilities.probe_health(
        "http://t2i-lite:8000/text2img"
    )
    assert not supported
    assert "未启用" in message
    assert "secret" not in message


@pytest.mark.asyncio
async def test_real_candidates_not_unused_base_and_cache():
    strategy = SimpleNamespace(
        endpoints=["http://local:8000/text2img"],
        BASE_RENDER_URL="https://unused.test/text2img",
    )
    probe = AsyncMock(return_value=(True, "supported"))
    checker = capabilities.ServiceCapabilities(lambda: strategy, probe=probe)
    assert await checker.supports(strategy)
    await checker.describe()
    probe.assert_awaited_once_with("http://local:8000/text2img")
    strategy.endpoints = ["http://changed:8000/text2img"]
    assert await checker.supports(strategy)
    assert probe.await_count == 2


@pytest.mark.asyncio
async def test_empty_candidates_use_base_or_explicit_fallback_but_never_all_empty():
    probe = AsyncMock(return_value=(True, "supported"))
    strategy = SimpleNamespace(
        endpoints=[], BASE_RENDER_URL="http://local:8000/text2img"
    )
    checker = capabilities.ServiceCapabilities(probe=probe)
    assert await checker.supports(strategy)
    probe.assert_awaited_once_with("http://local:8000/text2img")
    empty = SimpleNamespace(endpoints=[], BASE_RENDER_URL="")
    assert not await checker.supports(empty)
    fallback = capabilities.ServiceCapabilities(
        fallback_url="http://fallback:8000", probe=probe
    )
    assert await fallback.supports(empty)


@pytest.mark.asyncio
async def test_actual_unsupported_fallback_disables_style_and_concurrent_probe_coalesced():
    async def probe(endpoint):
        await asyncio.sleep(0)
        return (False, "unsupported") if "old" in endpoint else (True, "supported")

    mock = AsyncMock(side_effect=probe)
    strategy = SimpleNamespace(
        endpoints=["http://new:8000/text2img", "http://old:8000/text2img"]
    )
    checker = capabilities.ServiceCapabilities(probe=mock)
    results = await asyncio.gather(*(checker.supports(strategy) for _ in range(20)))
    assert not any(results)
    assert mock.await_count == 2


@pytest.mark.asyncio
async def test_429_per_call_endpoint_supersedes_entire_unused_pool():
    probe = AsyncMock(return_value=(True, "supported"))
    strategy = SimpleNamespace(
        endpoints=["https://old.invalid/text2img"],
        BASE_RENDER_URL="https://old.invalid/text2img",
    )
    checker = capabilities.ServiceCapabilities(probe=probe)
    assert await checker.supports(strategy, endpoint="http://local:8000/text2img")
    probe.assert_awaited_once_with("http://local:8000/text2img")
    assert strategy.endpoints == ["https://old.invalid/text2img"]
