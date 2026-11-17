"""Pure offline history compaction tests, including pairing and protected data."""

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from astrbot_plugin_qing_chat.features.conversation.tool_history import (
    bounded_int,
    compact_tool_history,
)


class HistoryTests(unittest.TestCase):
    def history(self, count=12, name="astrbot_execute_shell", content=None):
        rows = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "原始用户问题"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,KEEP"},
                    },
                ],
            }
        ]
        for i in range(count):
            rows.extend(
                [
                    {
                        "role": "assistant",
                        "content": "assistant prose",
                        "tool_calls": [
                            {
                                "id": str(i),
                                "function": {"name": name, "arguments": "KEEP ARGS"},
                            },
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": str(i),
                        "content": content
                        or json.dumps(
                            {
                                "session_id": "session-original",
                                "status": "completed",
                                "exit_code": 0,
                                "stdout": "BEGIN" + "很长的执行内容" * 1000 + "END",
                                "stderr": "",
                                "has_more": False,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ]
            )
        return rows

    def test_default_keeps_eight_recent_and_copies_only_changed_results(self):
        history = self.history()
        before = deepcopy(history)
        result, count, saved = compact_tool_history(history, {})
        self.assertEqual(count, 4)
        self.assertGreater(saved, 20000)
        self.assertEqual(history, before)
        self.assertEqual(len(result), len(history))
        self.assertIs(result[0], history[0])
        for i, row in enumerate(history):
            if row.get("role") != "tool":
                self.assertIs(result[i], row)
        for row in result:
            if row.get("role") != "tool":
                continue
            number = int(row["tool_call_id"])
            if number < 4:
                self.assertLessEqual(len(row["content"]), 1200)
                parsed = json.loads(row["content"])
                self.assertTrue(parsed["_tool_history_compacted"])
                self.assertEqual(parsed["session_id"], "session-original")
                self.assertEqual(parsed["status"], "completed")
                self.assertEqual(parsed["exit_code"], 0)
                self.assertIn("BEGIN", parsed["excerpt"])
                self.assertIn("END", parsed["excerpt"])
                self.assertIn("不是完整输出", parsed["note"])
            else:
                self.assertIs(row, history[2 * number + 2])
        self.assertEqual(compact_tool_history(result, {}), (result, 0, 0))

    def test_recent_count_and_result_length_are_configurable(self):
        history = self.history(4, content="HEAD" + "x" * 5000 + "TAIL")
        result, count, _ = compact_tool_history(
            history,
            {
                "keep_recent_tool_results": 2,
                "old_tool_result_chars": 600,
            },
        )
        self.assertEqual(count, 2)
        for row in result[2:6:2]:
            self.assertLessEqual(len(row["content"]), 600)
            self.assertIn("TAIL", json.loads(row["content"])["excerpt"])

    def test_protected_and_unknown_tools_remain_verbatim(self):
        for name in (
            "astrbot_generate_image",
            "recall_long_term_memory",
            "memory_search",
            "custom_tool",
        ):
            with self.subTest(name=name):
                history = self.history(2, name=name)
                result = compact_tool_history(history, {"keep_recent_tool_results": 0})
                self.assertEqual(result, (history, 0, 0))
                # Even an accidental allowlist addition cannot compact image/identity data.
                if name != "custom_tool":
                    self.assertEqual(
                        compact_tool_history(
                            history,
                            {
                                "keep_recent_tool_results": 0,
                                "compact_tool_names": [name],
                            },
                        ),
                        (history, 0, 0),
                    )

    def test_custom_allowlist_can_be_explicitly_extended(self):
        history = self.history(1, name="custom_tool")
        _, count, _ = compact_tool_history(
            history,
            {
                "keep_recent_tool_results": 0,
                "compact_tool_names": ["custom_tool"],
            },
        )
        self.assertEqual(count, 1)

    def test_disabled_short_and_multimodal_results_are_untouched(self):
        history = self.history()
        self.assertEqual(
            compact_tool_history(history, {"compact_tool_history": False}),
            (history, 0, 0),
        )
        short = self.history(1, content='{"status":"error","exit_code":1}')
        self.assertEqual(
            compact_tool_history(short, {"keep_recent_tool_results": 0}), (short, 0, 0)
        )
        short[-1]["content"] = [{"type": "image_url", "image_url": {"url": "KEEP"}}]
        self.assertEqual(
            compact_tool_history(short, {"keep_recent_tool_results": 0}), (short, 0, 0)
        )

    def test_unmatched_ambiguous_and_malformed_calls_are_not_rewritten(self):
        history = self.history(1)
        history[1]["tool_calls"].append(deepcopy(history[1]["tool_calls"][0]))
        self.assertEqual(
            compact_tool_history(history, {"keep_recent_tool_results": 0}),
            (history, 0, 0),
        )
        history[1]["tool_calls"] = [{"id": "0", "function": {"name": []}}]
        self.assertEqual(
            compact_tool_history(history, {"keep_recent_tool_results": 0}),
            (history, 0, 0),
        )
        history[1]["tool_calls"] = None
        history += [None, [], {"role": "tool", "content": "KEEP"}]
        self.assertEqual(
            compact_tool_history(history, {"keep_recent_tool_results": 0}),
            (history, 0, 0),
        )

    def test_escaped_unicode_and_error_state_remain_valid_json(self):
        history = self.history(
            1,
            content=json.dumps(
                {
                    "status": "error",
                    "exit_code": 7,
                    "session_id": "id",
                    "has_more": True,
                    "stderr": '\n"\\错误' * 10000,
                },
                ensure_ascii=False,
            ),
        )
        result, count, _ = compact_tool_history(
            history,
            {
                "keep_recent_tool_results": 0,
                "old_tool_result_chars": 500,
            },
        )
        self.assertEqual(count, 1)
        self.assertLessEqual(len(result[-1]["content"]), 500)
        parsed = json.loads(result[-1]["content"])
        self.assertEqual(parsed["exit_code"], 7)
        self.assertTrue(parsed["has_more"])

    def test_configuration_bounds(self):
        for value, expected in (
            (True, 8),
            (None, 8),
            (float("inf"), 8),
            (-1, 0),
            (1000, 100),
            ("3", 3),
        ):
            self.assertEqual(bounded_int({"n": value}, "n", 8, 0, 100), expected)


if __name__ == "__main__":
    unittest.main()
