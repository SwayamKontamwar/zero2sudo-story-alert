"""Explicit manual delivery check; does not modify Story deduplication state."""
import base64
import os
from datetime import datetime, timezone

from .monitor import Attachment, Story, build_message, required_env, send_email


def main():
    # Valid tiny PNG, intentionally synthetic; no real Instagram post is implied.
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aH1sAAAAASUVORK5CYII=")
    sender = required_env("GMAIL_ADDRESS")
    story = Story("demo", "zero2sudo", datetime.now(timezone.utc), "image", links=("https://example.com/",))
    message = build_message(story, sender, os.getenv("EMAIL_TO", sender) or sender,
                            Attachment(png, "image/png", "sample-test-image.png"))
    message.replace_header("Subject", "TEST: Story alert attachment and links")
    body = message.get_body(preferencelist=("plain",))
    body.set_content("This is a synthetic delivery test, not a real Instagram Story.\n"
                     "The tiny PNG attachment and example.com link are test samples.\n\n"
                     + body.get_content())
    send_email(message, required_env("GMAIL_APP_PASSWORD"))
    print("Sample attachment-and-links email accepted by Gmail SMTP.")


if __name__ == "__main__":
    main()
