import copy
import unittest
from types import SimpleNamespace

from astrbot_plugin_context_image_limiter.limiter import (
    MB, REMOVED_IMAGE_TEXT, configured_limit_bytes, image_data_bytes, trim_context_images,
)


def picture(size):
    prefix = "data:image/png;base64,"
    return {"type": "image_url", "image_url": {"url": prefix + "A" * (size - len(prefix))}}


def user(*parts):
    return {"role": "user", "content": list(parts)}


class LimiterTests(unittest.TestCase):
    def test_default_and_fractional_configuration(self):
        self.assertEqual(configured_limit_bytes(10), 10 * MB)
        self.assertEqual(configured_limit_bytes("0.5"), 500_000)
        for value in (None, "invalid", -1, 0, True, float("nan"), float("inf"), 1e308):
            with self.subTest(value=value):
                self.assertEqual(configured_limit_bytes(value), 10 * MB)

    def test_under_limit_and_equal_limit_do_not_mutate(self):
        for cap in (100, 101):
            messages = [user(picture(100))]
            original = copy.deepcopy(messages)
            result = trim_context_images(messages, cap)
            self.assertEqual(result.removed_count, 0)
            self.assertEqual(messages, original)

    def test_removes_oldest_images_not_largest_or_newest(self):
        messages = [user(picture(100)), user(picture(800)), user(picture(300))]
        result = trim_context_images(messages, 900)
        self.assertEqual((result.before_bytes, result.after_bytes, result.removed_count), (1200, 300, 2))
        self.assertEqual(image_data_bytes(messages[-1]["content"][0]), 300)
        self.assertEqual(len(messages), 3)

    def test_text_audio_and_tool_structure_are_preserved(self):
        text = {"type": "text", "text": "保留中文和全部正文"}
        audio = {"type": "audio_url", "audio_url": {"url": "data:audio/wav;base64,AAAA"}}
        tool_call = {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1", "function": {"name": "search", "arguments": "{}"}}]}
        tool_reply = {"role": "tool", "tool_call_id": "call-1", "content": "keep result"}
        messages = [user(text, picture(100), audio), tool_call, tool_reply]
        saved = copy.deepcopy(messages)
        trim_context_images(messages, 50)
        self.assertEqual(messages[0]["content"][0], saved[0]["content"][0])
        self.assertEqual(messages[0]["content"][2], saved[0]["content"][2])
        self.assertEqual(messages[1:], saved[1:])

    def test_oversized_current_image_and_image_only_message(self):
        messages = [user(picture(500))]
        result = trim_context_images(messages, 100)
        self.assertEqual(result.after_bytes, 0)
        self.assertEqual(messages[0]["content"], [{"type": "text", "text": REMOVED_IMAGE_TEXT}])

    def test_duplicate_images_are_counted_for_each_upload(self):
        image = picture(100)
        messages = [user(copy.deepcopy(image)), user(copy.deepcopy(image))]
        result = trim_context_images(messages, 100)
        self.assertEqual(result.before_bytes, 200)
        self.assertEqual(result.removed_count, 1)

    def test_typed_messages_use_typed_replacements(self):
        image = SimpleNamespace(type="image_url", image_url=SimpleNamespace(url="data:image/png;base64," + "A" * 100))
        text = SimpleNamespace(type="text", text="preserved")
        messages = [SimpleNamespace(role="user", content=[image, text])]
        trim_context_images(messages, 1, lambda value: SimpleNamespace(type="text", text=value))
        self.assertEqual(messages[0].content[0].type, "text")
        self.assertIs(messages[0].content[1], text)

    def test_response_and_anthropic_image_forms(self):
        messages = [user({"type": "input_image", "image_url": "data:image/png;base64,AAAA"}), user({"type": "image", "source": {"type": "base64", "data": "A" * 100}})]
        trim_context_images(messages, 0)
        self.assertEqual(messages[0]["content"][0]["type"], "input_text")
        self.assertEqual(messages[1]["content"][0]["type"], "text")

    def test_url_size_only_no_download_and_utf8_byte_count(self):
        value = {"type": "image_url", "image_url": {"url": "https://invalid.example/图片.png"}}
        self.assertEqual(image_data_bytes(value), len(value["image_url"]["url"].encode()))

    def test_idempotent_and_empty_context(self):
        messages = [user(picture(100)), {"role": "user", "content": "plain"}]
        trim_context_images(messages, 0)
        saved = copy.deepcopy(messages)
        self.assertEqual(trim_context_images(messages, 0).removed_count, 0)
        self.assertEqual(messages, saved)
        self.assertEqual(trim_context_images([], 10).before_bytes, 0)

    def test_negative_budget_rejected(self):
        with self.assertRaises(ValueError):
            trim_context_images([], -1)


if __name__ == "__main__":
    unittest.main()
