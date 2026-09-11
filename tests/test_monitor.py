import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from email import policy
from email.parser import BytesParser

from story_alert.monitor import (
    Story,
    build_message,
    read_seen,
    required_env,
    stories_from_iphone_payload,
    write_seen,
    download_attachment,
    story_links,
    run,
)


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

    def test_current_instagram_story_payload_is_parsed(self):
        payload = {
            "reels": {
                "50350974961": {
                    "items": [
                        {"pk": "222", "taken_at": 1789140002, "media_type": 2},
                        {"pk": "111", "taken_at": 1789140001, "media_type": 1},
                    ]
                }
            }
        }
        stories = stories_from_iphone_payload(payload, "zero2sudo", 50350974961)
        self.assertEqual([story.story_id for story in stories], ["111", "222"])
        self.assertEqual([story.media_type for story in stories], ["image", "video"])

    def test_missing_reel_means_no_active_story(self):
        self.assertEqual(
            stories_from_iphone_payload({"reels": {}}, "zero2sudo", 50350974961),
            [],
        )

    def test_payload_to_download_to_email_round_trip(self):
        for kind, mime, media_fields in (
            (1, "image/jpeg", {"image_versions2": {"candidates": [{"url": "https://s.cdninstagram.com/a.jpg", "width": 100}]}}),
            (2, "video/mp4", {"video_versions": [{"url": "https://s.cdninstagram.com/a.mp4", "width": 100}]}),
        ):
            with self.subTest(kind=kind):
                payload = {"reels": {"1": {"items": [{"pk": "123", "taken_at": 1789140001,
                    "media_type": kind, "story_link_stickers": [{"story_link": {"url": "https://example.com/job"}}], **media_fields}]}}}
                story = stories_from_iphone_payload(payload, "zero2sudo", 1)[0]
                response = MagicMock()
                response.__enter__.return_value = response
                response.is_redirect = False
                response.headers = {"Content-Type": mime}
                response.iter_content.return_value = [b"sample", b"-media"]
                with patch("story_alert.monitor.requests.get", return_value=response):
                    attachment, note = download_attachment(story)
                message = build_message(story, "from@example.com", "to@example.com", attachment, note)
                parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
                part = list(parsed.iter_attachments())[0]
                self.assertEqual(part.get_payload(decode=True), b"sample-media")
                self.assertEqual(part.get_content_type(), mime)
                self.assertIn("https://example.com/job", parsed.get_body().get_content())

    def test_download_failures_still_produce_link_email(self):
        import requests
        story = Story("1", "zero2sudo", datetime.now(timezone.utc), "video", "https://s.cdninstagram.com/a.mp4")
        with patch("story_alert.monitor.requests.get", side_effect=requests.Timeout):
            attachment, note = download_attachment(story)
        self.assertIsNone(attachment)
        message = build_message(story, "from@example.com", "to@example.com", attachment, note)
        self.assertIn(story.media_url, message.get_content())
        self.assertIn("download failed", message.get_content())

    def test_oversized_declared_and_streamed_media(self):
        story = Story("1", "zero2sudo", datetime.now(timezone.utc), "video", "https://s.cdninstagram.com/a.mp4")
        for headers in ({"Content-Length": "6"}, {}):
            response = MagicMock()
            response.__enter__.return_value = response
            response.is_redirect = False
            response.headers = {"Content-Type": "video/mp4", **headers}
            response.iter_content.return_value = [b"123", b"456"]
            with patch("story_alert.monitor.requests.get", return_value=response), patch("story_alert.monitor.MAX_ATTACHMENT_BYTES", 5):
                attachment, note = download_attachment(story)
            self.assertIsNone(attachment)
            self.assertIn("limit", note)

    def test_untrusted_redirect_is_not_fetched(self):
        story = Story("1", "zero2sudo", datetime.now(timezone.utc), "image", "https://s.cdninstagram.com/a.jpg")
        response = MagicMock()
        response.__enter__.return_value = response
        response.is_redirect = True
        response.headers = {"Location": "http://127.0.0.1/private"}
        with patch("story_alert.monitor.requests.get", return_value=response) as get:
            attachment, _ = download_attachment(story)
        self.assertIsNone(attachment)
        self.assertEqual(get.call_count, 1)

    def test_links_deduplicate_and_exclude_media_and_non_web_schemes(self):
        self.assertEqual(story_links({
            "story_link_stickers": [{"story_link": {"url": "https://example.com/job"}}],
            "story_cta": [{"links": [{"webUri": "https://example.com/job"}, {"webUri": "https://example.com/apply"}, {"webUri": "javascript:bad()"}]}],
            "image_versions2": {"candidates": [{"url": "https://s.cdninstagram.com/a.jpg"}]},
        }), ("https://example.com/job", "https://example.com/apply"))

    def test_email_failure_does_not_mark_story_seen_and_success_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "session"
            session.touch()
            state = Path(directory) / "seen.json"
            story = Story("1", "zero2sudo", datetime.now(timezone.utc), "image")
            env = {"INSTAGRAM_USERNAME": "test", "INSTAGRAM_SESSION_FILE": str(session),
                   "GMAIL_ADDRESS": "test@example.com", "GMAIL_APP_PASSWORD": "test",
                   "STATE_FILE": str(state)}
            with patch.dict("os.environ", env), patch("story_alert.monitor.fetch_active_stories", return_value=[story]), patch("story_alert.monitor.send_email") as send:
                send.side_effect = RuntimeError("SMTP failed")
                with self.assertRaises(RuntimeError):
                    run()
                self.assertEqual(read_seen(state), set())
                send.side_effect = None
                self.assertEqual(run(), 1)
                self.assertEqual(read_seen(state), {"1"})
                self.assertEqual(run(), 0)
                self.assertEqual(send.call_count, 2)


if __name__ == "__main__":
    unittest.main()
