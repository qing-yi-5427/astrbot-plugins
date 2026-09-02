import pytest

from core.onebot import fetch_onebot_reply_image


class Reply:
    def __init__(self, message_id):
        self.id = message_id
        self.chain = []


class Bot:
    async def call_action(self, action, **kwargs):
        assert action == "get_msg"
        assert kwargs["message_id"] == 123
        return {
            "message": [
                {"type": "text", "data": {"text": "quoted"}},
                {"type": "image", "data": {"url": "https://example/a.jpg"}},
                {"type": "image", "data": {"file": "file-id"}},
            ]
        }


class Event:
    bot = Bot()


@pytest.mark.asyncio
async def test_onebot_reply_fallback_extracts_first_image():
    result = await fetch_onebot_reply_image(Event(), [Reply("123")])
    assert result is not None
    assert result.source == "https://example/a.jpg"
    assert result.total_count == 2
