import asyncio
import copy
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_qing_media.features.imagegen import delivery_receipt as receipt

from .conftest import FakeEvent
from .test_client import PNG


class ActionFailed(Exception):
    def __init__(self, message="waitForSelfEcho timeout"):
        self.result = {
            "status": "failed",
            "retcode": 1200,
            "message": message,
            "wording": message,
        }


def history_message(*, now=None, msg_id=42, image=PNG):
    return {
        "message_type": "group",
        "group_id": 123,
        "user_id": 456,
        "message_id": msg_id,
        "time": int(now if now is not None else time.time()),
        "message": [
            {
                "type": "image",
                "data": {
                    "file": receipt.image_fingerprint(image).upper() + ".png",
                    "file_size": str(len(image)),
                    "url": "https://invalid.example/not-downloaded",
                },
            }
        ],
    }


def group_event(history=None, exc=None):
    event = FakeEvent("/i cat")
    event.get_group_id = lambda: "123"
    event.get_self_id = lambda: "456"
    event.bot = SimpleNamespace(
        call_action=AsyncMock(return_value=history or {"messages": []})
    )
    original_send = event.send

    async def send(result):
        if hasattr(result, "chain"):
            event.image_attempts += 1
            raise exc or ActionFailed()
        await original_send(result)

    event.send = send
    return event


@pytest.mark.parametrize(
    "exc,ambiguous",
    [
        (ActionFailed(), True),
        (TimeoutError(), True),
        (ActionFailed("parameter error"), False),
        (ActionFailed("secret-key waitForSelfEcho timeout"), False),
        (RuntimeError("waitForSelfEcho timeout"), False),
    ],
)
def test_only_known_receipt_errors_are_classified(exc, ambiguous):
    assert receipt.receipt_is_ambiguous(exc) is ambiguous


def match(messages, used_ids=None):
    return receipt.matching_receipt(
        {"messages": messages},
        group="123",
        self_id="456",
        fingerprint=receipt.image_fingerprint(PNG),
        size=len(PNG),
        started=100.9,
        ended=120.3,
        used_ids=used_ids or set(),
    )


def test_exact_array_and_cq_formats():
    message = history_message(now=110)
    assert match([message]) == "42"
    message["message"] = (
        f"[CQ:image,file={receipt.image_fingerprint(PNG)}.png,file_size={len(PNG)},url=ignored]"
    )
    assert match([message]) == "42"


@pytest.mark.parametrize(
    "path,value",
    [
        (("group_id",), 124),
        (("user_id",), 457),
        (("message_type",), "private"),
        (("time",), 99),
        (("time",), 123),
        (("time",), float("nan")),
        (("time",), None),
        (("time",), "invalid"),
        (("message_id",), None),
        (("message_id",), True),
        (("message_id",), ""),
        (("message",), None),
        (("message",), [{"type": "text", "data": {"text": "image"}}]),
        (("message", 0, "data", "file_size"), len(PNG) + 1),
        (("message", 0, "data", "file_size"), None),
        (("message", 0, "data", "file"), "0" * 32 + ".png"),
        (
            ("message", 0, "data", "file"),
            "https://example.com/" + receipt.image_fingerprint(PNG) + ".png",
        ),
        (("message", 0, "data", "file"), "other-name.png"),
    ],
)
def test_unrelated_old_or_malformed_records_cannot_confirm(path, value):
    message = history_message(now=110)
    parent = message
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    assert match([message]) is None


def test_ambiguous_duplicate_and_used_message_ids():
    message = history_message(now=110)
    assert (
        match([message, copy.deepcopy(message)]) == "42"
    )  # API duplicate, same identity.
    assert match([message, history_message(now=111, msg_id=43)]) is None
    assert match([message], used_ids={"42"}) is None
    assert match([None, "malformed", message]) == "42"


@pytest.mark.parametrize(
    "history", [None, [], {}, {"messages": None}, {"messages": "text"}]
)
def test_invalid_history_shape(history):
    assert (
        receipt.matching_receipt(
            history,
            group="123",
            self_id="456",
            fingerprint="",
            size=0,
            started=0,
            ended=1,
            used_ids=set(),
        )
        is None
    )


async def test_confirmed_timeout_counts_success_without_resending(
    plugin_module, config, caplog
):
    caplog.set_level("INFO")
    config["show_progress"] = False
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock(return_value=[PNG])
    event = group_event({"messages": [history_message()]})
    result = json.loads(await plugin.generate_image(event, "cat"))
    assert result["status"] == "success" and result["sent"] == result["generated"] == 1
    assert (
        result["delivery_outcome"] == "confirmed" and result["history_confirmed"] == 1
    )
    assert "error" not in result and event.sent == []
    assert event.image_attempts == 1
    event.bot.call_action.assert_awaited_once_with(
        "get_group_msg_history",
        group_id=123,
        count=30,
        self_id=456,
    )
    plugin.client.generate.assert_awaited_once()
    assert "confirmed by group history" in caplog.text
    assert not plugin._busy_sessions


async def test_healthy_send_does_not_query_history(plugin_module, config):
    config["show_progress"] = False
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock(return_value=[PNG])
    event = group_event()
    event.send = AsyncMock()
    result = json.loads(await plugin.generate_image(event, "cat"))
    assert result["status"] == "success" and result["sent"] == 1
    event.bot.call_action.assert_not_awaited()


async def test_unknown_delivery_not_claimed_failed_and_no_rebilling(
    plugin_module, config, caplog
):
    config["show_progress"] = False
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock(return_value=[PNG, b"other-image"])
    event = group_event()
    result_json = await plugin.generate_image(event, "two cats")
    result = json.loads(result_json)
    assert result["status"] == "unconfirmed" and result["delivery_outcome"] == "unknown"
    assert result["generated"] == 2 and result["sent"] == 0
    assert result["unconfirmed"] == result["not_attempted"] == 1
    assert result["error"]["request_outcome"] == "generated"
    assert result["error"]["code"] == "image_send_unconfirmed"
    assert result["error"]["automatic_retry"] is False
    assert "可能已显示" in result["message"]
    assert "发送失败" not in result["message"] and "已发送 0" not in result["message"]
    assert event.sent == [] and event.image_attempts == 1
    assert event.bot.call_action.await_count == 2
    assert await plugin.generate_image(event, "rewritten prompt") == result_json
    plugin.client.generate.assert_awaited_once()
    assert "unconfirmed" in caplog.text


async def test_history_lag_second_read_confirms(plugin_module, config):
    config["show_progress"] = False
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock(return_value=[PNG])
    event = group_event()
    event.bot.call_action.side_effect = [
        {"messages": []},
        {"messages": [history_message()]},
    ]
    result = json.loads(await plugin.generate_image(event, "cat"))
    assert result["status"] == "success" and event.bot.call_action.await_count == 2
    assert event.image_attempts == 1


async def test_direct_command_unknown_uses_truthful_notice(plugin_module, config):
    config["show_progress"] = False
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock(return_value=[PNG])
    event = group_event()
    event.bot.call_action.side_effect = RuntimeError("secret-key; history unsupported")
    await plugin.image_command(event)
    assert event.stopped and not event.call_llm
    assert len(event.sent) == 1 and "发送状态未确认" in event.sent[0].text
    assert "失败" not in event.sent[0].text and "0 张" not in event.sent[0].text
    assert "secret-key" not in event.sent[0].text
    assert event.image_attempts == 1


async def test_partial_unknown_stops_remaining_and_never_confirms_same_bytes_twice(
    plugin_module, config
):
    config["show_progress"] = False
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock(return_value=[PNG, PNG, b"not-attempted"])
    event = group_event({"messages": [history_message()]})
    result = json.loads(await plugin.generate_image(event, "three cats"))
    assert (
        result["status"] == "partial"
        and result["sent"] == 1
        and result["generated"] == 3
    )
    assert result["unconfirmed"] == result["not_attempted"] == 1
    assert event.image_attempts == 2 and event.bot.call_action.await_count == 1


async def test_private_or_non_onebot_timeout_unconfirmed_without_history(
    plugin_module, config
):
    config["show_progress"] = False
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock(return_value=[PNG])
    event = group_event(exc=TimeoutError("secret-key"))
    event.get_group_id = lambda: ""
    result = json.loads(await plugin.generate_image(event, "cat"))
    assert result["status"] == "unconfirmed" and "secret-key" not in json.dumps(result)
    event.bot.call_action.assert_not_awaited()


async def test_history_timeout_bounded(plugin_module, config, monkeypatch):
    monkeypatch.setattr(receipt, "CHECK_TIMEOUT", 0.02)
    config["show_progress"] = False
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock(return_value=[PNG])
    event = group_event()
    entered = asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    event.bot.call_action.side_effect = blocked
    result = json.loads(
        await asyncio.wait_for(plugin.generate_image(event, "cat"), timeout=1)
    )
    assert entered.is_set() and result["status"] == "unconfirmed"
    assert (
        event.image_attempts == 1
        and not plugin._busy_sessions
        and not plugin._slots.locked()
    )


async def test_cancel_during_history_propagates_releases_busy(plugin_module, config):
    config["show_progress"] = False
    plugin = plugin_module.ImageGenPlugin(None, config)
    plugin.client.generate = AsyncMock(return_value=[PNG])
    event = group_event()
    entered = asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    event.bot.call_action.side_effect = blocked
    task = asyncio.create_task(plugin.generate_image(event, "cat"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not plugin._busy_sessions and not plugin._slots.locked()
    assert event.image_attempts == 1
