import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from story_alert.monitor import Story, build_message, read_seen, required_env, write_seen


class MonitorTests(unittest.TestCase):
    def test_story_url(self):
        story = Story("12345", "zero2sudo", datetime.now(timezone.utc), "image")
        self.assertEqual(
            story.instagram_url,
            "https://www.instagram.com/stories/zero2sudo/12345/",
        )

    def test_state_round_trip_and_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "state.json"
            write_seen(path, ["1", "2", "1", "3"])
            self.assertEqual(read_seen(path), {"1", "2", "3"})
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["seen_story_ids"],
                ["1", "2", "3"],
            )

    def test_state_limit_keeps_latest_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            write_seen(path, ["1", "2", "3"], limit=2)
            self.assertEqual(read_seen(path), {"2", "3"})

    def test_required_env_rejects_blank_values(self):
        with self.assertRaisesRegex(RuntimeError, "TOKEN"):
            required_env("TOKEN", {"TOKEN": "  "})

    def test_email_contains_direct_story_link(self):
        story = Story(
            "987",
            "zero2sudo",
            datetime(2026, 9, 11, 14, 30, tzinfo=timezone.utc),
            "video",
        )
        message = build_message(story, "from@example.com", "to@example.com")
        body = message.get_content()
        self.assertIn("2026-09-11 14:30 UTC", body)
        self.assertIn(story.instagram_url, body)
        self.assertEqual(message["To"], "to@example.com")


if __name__ == "__main__":
    unittest.main()

