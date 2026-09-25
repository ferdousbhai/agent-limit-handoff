from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import handoff


class HandoffTests(unittest.TestCase):
    def test_each_provider_selects_its_account_weekly_window(self) -> None:
        for provider, included, excluded in (
            ("codex", "Weekly (7-day)", "5h window"),
            ("claude", "Weekly (7-day)", "Fable Weekly"),
            ("grok", "Weekly", "Grok Build"),
        ):
            self.assertTrue(handoff.usable_label(provider, included))
            self.assertFalse(handoff.usable_label(provider, excluded))

    def test_draft_then_final_handoff_once_per_task_and_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(handoff, "STATE_ROOT", Path(tmp)):
                event = {"session_id": "task-1", "cwd": "/work/project"}
                draft = handoff.decide("codex", event, (95.3, "2030-10-02T00:00:00Z"))
                self.assertEqual(draft["decision"], "block")
                document = handoff.handoff_path("codex", "task-1")
                document.parent.mkdir(parents=True, exist_ok=True)
                document.write_text("# Task\n## Objective\nWork in progress.\n## Completed\nRead the code.\n## Changed files\nNone.\n## Verification\nNot run.\n## Blockers\nNone.\n## Next steps\nContinue implementation with the current branch and verify the change.\n")
                self.assertEqual(handoff.decide("codex", event, (95.3, "2030-10-02T00:00:00Z")), {})
                final = handoff.decide("codex", event, (99.1, "2030-10-02T00:00:00Z"))
                self.assertEqual(final["decision"], "block")
                time.sleep(0.01)
                document.write_text(document.read_text() + "\nFinal state checked.\n")
                stopped = handoff.decide("codex", event, (99.1, "2030-10-02T00:00:00Z"))
                self.assertEqual(stopped["continue"], False)
                self.assertEqual(handoff.decide("codex", {"session_id": "task-2"}, (99.1, "2030-10-02T00:00:00Z"))["decision"], "block")
                self.assertEqual(handoff.decide("codex", event, (99.1, "2030-10-09T00:00:00Z"))["decision"], "block")

    def test_collector_failure_does_not_trigger_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            command = Path(tmp) / "omarchy-agent-usage-codex"
            command.write_text("#!/bin/sh\nexit 1\n")
            command.chmod(0o755)
            with patch.object(handoff, "COLLECTOR_DIR", Path(tmp)):
                self.assertIsNone(handoff.read_usage("codex"))

    def test_tool_notice_is_per_task_and_rate_limited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(handoff, "STATE_ROOT", Path(tmp)):
                event = {"session_id": "task-1"}
                first = handoff.decide("codex", event, (99.2, "2030-10-02T00:00:00Z"), tool=True)
                self.assertIn("Finalize", first["hookSpecificOutput"]["additionalContext"])
                self.assertEqual(handoff.decide("codex", event, (99.2, "2030-10-02T00:00:00Z"), tool=True), {})
                self.assertTrue(handoff.decide("codex", {"session_id": "task-2"}, (99.2, "2030-10-02T00:00:00Z"), tool=True))

    def test_failed_final_handoff_produces_emergency_document_and_stops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(handoff, "STATE_ROOT", Path(tmp)):
                event = {"session_id": "task-1", "cwd": "/work/project", "last_assistant_message": "Changes are unfinished."}
                for _ in range(2):
                    self.assertEqual(handoff.decide("claude", event, (99.5, "2030-10-02T00:00:00Z"))["decision"], "block")
                result = handoff.decide("claude", event, (99.5, "2030-10-02T00:00:00Z"))
                self.assertEqual(result["continue"], False)
                self.assertIn("Changes are unfinished", handoff.handoff_path("claude", "task-1").read_text())

    def test_stale_claude_reading_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            command = base / "omarchy-agent-usage-claude"
            record = {"limits": [{"label": "Weekly (7-day)", "percent": 0.99, "resetsAt": "2030-10-02T00:00:00Z"}]}
            command.write_text("#!/bin/sh\ncat <<'JSON'\n" + json.dumps(record) + "\nJSON\n")
            command.chmod(0o755)
            cache = base / "claude-limits.json"
            cache.write_text(json.dumps({"fetchedAtMs": 1000}))
            with patch.object(handoff, "COLLECTOR_DIR", base), patch.object(handoff, "CACHE_ROOT", base):
                self.assertIsNone(handoff.read_usage("claude"))
                cache.write_text(json.dumps({"fetchedAtMs": time.time() * 1000}))
                self.assertEqual(handoff.read_usage("claude"), (99.0, "2030-10-02T00:00:00Z"))


if __name__ == "__main__":
    unittest.main()
