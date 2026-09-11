from __future__ import annotations

import json
import os
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable, Mapping


@dataclass(frozen=True)
class Story:
    story_id: str
    username: str
    posted_at: datetime
    media_type: str

    @property
    def instagram_url(self) -> str:
        return f"https://www.instagram.com/stories/{self.username}/{self.story_id}/"


def required_env(name: str, env: Mapping[str, str] = os.environ) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable {name} is missing")
    return value


def read_seen(path: Path) -> set[str]:
    if not path.exists():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get("seen_story_ids", [])
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise ValueError(f"Invalid state file: {path}")
    return set(values)


def write_seen(path: Path, story_ids: Iterable[str], limit: int = 1000) -> None:
    unique = list(dict.fromkeys(str(item) for item in story_ids))[-limit:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"seen_story_ids": unique}, indent=2) + "\n",
        encoding="utf-8",
    )


def fetch_active_stories(username: str, session_file: Path, login_username: str) -> list[Story]:
    import instaloader

    loader = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        save_metadata=False,
        compress_json=False,
        quiet=True,
    )
    loader.load_session_from_file(login_username, str(session_file))
    profile = instaloader.Profile.from_username(loader.context, username)

    found: list[Story] = []
    for story_collection in loader.get_stories(userids=[profile.userid]):
        for item in story_collection.get_items():
            posted = item.date_utc
            if posted.tzinfo is None:
                posted = posted.replace(tzinfo=timezone.utc)
            found.append(
                Story(
                    story_id=str(item.mediaid),
                    username=username,
                    posted_at=posted,
                    media_type="video" if item.is_video else "image",
                )
            )
    return sorted(found, key=lambda item: item.posted_at)


def build_message(story: Story, sender: str, recipient: str) -> EmailMessage:
    posted = story.posted_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    message = EmailMessage()
    message["Subject"] = f"New @{story.username} Instagram Story"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        f"@{story.username} posted a new Instagram Story.\n\n"
        f"Posted: {posted}\n"
        f"Type: {story.media_type}\n"
        f"Open Story: {story.instagram_url}\n\n"
        "Instagram Stories normally expire after 24 hours."
    )
    return message


def send_email(message: EmailMessage, password: str) -> None:
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=30) as smtp:
        smtp.login(str(message["From"]), password)
        smtp.send_message(message)


def run() -> int:
    target = os.getenv("TARGET_USERNAME", "zero2sudo").strip().lstrip("@").lower()
    login_username = required_env("INSTAGRAM_USERNAME")
    session_file = Path(os.getenv("INSTAGRAM_SESSION_FILE", "instagram.session"))
    gmail_address = required_env("GMAIL_ADDRESS")
    gmail_password = required_env("GMAIL_APP_PASSWORD")
    recipient = os.getenv("EMAIL_TO", gmail_address).strip() or gmail_address
    state_file = Path(os.getenv("STATE_FILE", "data/seen_stories.json"))

    if not session_file.is_file():
        raise RuntimeError(f"Instagram session file not found: {session_file}")

    seen = read_seen(state_file)
    active = fetch_active_stories(target, session_file, login_username)
    new_stories = [story for story in active if story.story_id not in seen]

    if not new_stories:
        print(f"No new Stories for @{target}. Active: {len(active)}; already seen: {len(seen)}")
        return 0

    ordered_seen = list(seen)
    for story in new_stories:
        send_email(build_message(story, gmail_address, recipient), gmail_password)
        ordered_seen.append(story.story_id)
        write_seen(state_file, ordered_seen)
        print(f"Emailed Story {story.story_id}: {story.instagram_url}")
    return len(new_stories)


def main() -> None:
    try:
        run()
    except Exception as exc:
        raise SystemExit(f"Story monitor failed: {exc}") from exc
