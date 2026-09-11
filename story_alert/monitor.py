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
from urllib.parse import urlsplit

import requests

MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024


@dataclass(frozen=True)
class Story:
    story_id: str
    username: str
    posted_at: datetime
    media_type: str
    media_url: str = ""
    links: tuple[str, ...] = ()

    @property
    def instagram_url(self) -> str:
        return f"https://www.instagram.com/stories/{self.username}/{self.story_id}/"


@dataclass(frozen=True)
class Attachment:
    data: bytes
    content_type: str
    filename: str

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


def http_url(value: object) -> str:
    if not isinstance(value, str):
        return ""
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in ("https", "http") and parsed.hostname and not parsed.username else ""
    except ValueError:
        return ""


def story_links(item: Mapping[str, object]) -> tuple[str, ...]:
    """Extract exposed link stickers/legacy swipe-up URLs; never follow them."""
    found: list[str] = []

    def visit(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("url", "webUri", "web_uri") and http_url(value):
                    found.append(value)
                elif isinstance(value, (dict, list)):
                    visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    for key in ("story_link", "story_link_stickers", "story_cta"):
        visit(item.get(key))
    # Newer payloads put the actual link under a story_link object in stickers.
    for sticker in item.get("story_bloks_stickers", []) or []:
        if isinstance(sticker, dict):
            block = sticker.get("bloks_sticker")
            if isinstance(block, dict):
                visit(block.get("story_link"))
    return tuple(dict.fromkeys(found))


def media_url(item: Mapping[str, object]) -> str:
    if int(item.get("media_type", 1)) == 2:
        candidates = item.get("video_versions", [])
    else:
        candidates = (item.get("image_versions2") or {}).get("candidates", [])
    candidates = [v for v in candidates or [] if isinstance(v, dict) and http_url(v.get("url"))]
    candidates.sort(key=lambda v: int(v.get("width", 0)) * int(v.get("height", 0)), reverse=True)
    return candidates[0]["url"] if candidates else ""


def download_attachment(story: Story) -> tuple[Attachment | None, str]:
    if not story.media_url:
        return None, "Instagram did not provide a downloadable media URL."
    url = story.media_url
    try:
        # Media requests never receive the Instagram login session or cookies.
        for _ in range(4):
            host = urlsplit(url).hostname or ""
            if urlsplit(url).scheme != "https" or not any(
                host.endswith("." + domain) or host == domain
                for domain in ("cdninstagram.com", "fbcdn.net")
            ):
                return None, "Media host was unsupported; use the Story link below."
            with requests.get(url, stream=True, timeout=(10, 20), allow_redirects=False) as response:
                if response.is_redirect:
                    url = response.headers.get("Location", "")
                    continue
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").split(";")[0].lower()
                allowed = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "video/mp4": "mp4"}
                if content_type not in allowed or not content_type.startswith(story.media_type + "/"):
                    return None, "Media format was unsupported; use the Story link below."
                if int(response.headers.get("Content-Length", "0")) > MAX_ATTACHMENT_BYTES:
                    return None, "Media exceeds the 15 MiB attachment limit."
                data = bytearray()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if len(data) + len(chunk) > MAX_ATTACHMENT_BYTES:
                        return None, "Media exceeds the 15 MiB attachment limit."
                    data.extend(chunk)
                if not data:
                    return None, "Instagram returned empty media."
                return Attachment(bytes(data), content_type, f"story.{allowed[content_type]}"), ""
        return None, "Media redirected too many times."
    except (requests.RequestException, ValueError):
        return None, "Media download failed; use the links below."


def stories_from_iphone_payload(payload: Mapping[str, object], username: str, user_id: int) -> list[Story]:
    reels = payload.get("reels", {})
    if not isinstance(reels, dict):
        return []
    reel = reels.get(str(user_id), {})
    if not isinstance(reel, dict):
        return []
    items = reel.get("items", [])
    if not isinstance(items, list):
        return []

    found: list[Story] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        story_id = item.get("pk") or item.get("id")
        taken_at = item.get("taken_at")
        if story_id is None or taken_at is None:
            continue
        found.append(
            Story(
                story_id=str(story_id),
                username=username,
                posted_at=datetime.fromtimestamp(int(taken_at), tz=timezone.utc),
                media_type="video" if int(item.get("media_type", 1)) == 2 else "image",
                media_url=media_url(item),
                links=story_links(item),
            )
        )
    return sorted(found, key=lambda item: item.posted_at)


def fetch_active_stories(
    username: str,
    session_file: Path,
    login_username: str,
    target_user_id: int | None = None,
) -> list[Story]:
    import instaloader

    loader = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        save_metadata=False,
        compress_json=False,
        quiet=True,
        # GitHub runners must never sleep for Instagram's 10+ minute 429 backoff.
        # A later scheduled run is a better retry and keeps Actions usage free.
        max_connection_attempts=1,
        request_timeout=30,
    )
    loader.load_session_from_file(login_username, str(session_file))

    # Instaloader's public username lookup and legacy Stories GraphQL query are
    # frequently blocked on GitHub-hosted IPs. The authenticated mobile endpoint
    # is current and accepts the stable numeric profile ID directly.
    if target_user_id is not None:
        payload = loader.context.get_iphone_json(
            f"api/v1/feed/reels_media/?reel_ids={target_user_id}",
            {},
        )
        return stories_from_iphone_payload(payload, username, target_user_id)

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
                    media_url=item.video_url if item.is_video else item.url,
                )
            )
    return sorted(found, key=lambda item: item.posted_at)


def build_message(story: Story, sender: str, recipient: str,
                  attachment: Attachment | None = None, attachment_note: str = "") -> EmailMessage:
    posted = story.posted_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    message = EmailMessage()
    message["Subject"] = f"New @{story.username} Instagram Story"
    message["From"] = sender
    message["To"] = recipient
    extras = ""
    if story.links:
        extras += "Links in this Story (provided by the poster):\n" + "\n".join(story.links) + "\n\n"
    if attachment:
        extras += f"Story {story.media_type} attached: {attachment.filename}\n\n"
    elif attachment_note:
        extras += f"Attachment unavailable: {attachment_note}\n"
        if http_url(story.media_url):
            extras += f"Temporary media download/view link: {story.media_url}\n"
        extras += "\n"
    message.set_content(
        f"@{story.username} posted a new Instagram Story.\n\n"
        f"Posted: {posted}\n"
        f"Type: {story.media_type}\n"
        f"Open Story: {story.instagram_url}\n\n"
        + extras
        + "Instagram Stories normally expire after 24 hours. Media links may expire sooner."
    )
    if attachment:
        maintype, subtype = attachment.content_type.split("/", 1)
        message.add_attachment(attachment.data, maintype=maintype, subtype=subtype, filename=attachment.filename)
    return message


def send_email(message: EmailMessage, password: str) -> None:
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=30) as smtp:
        smtp.login(str(message["From"]), password)
        smtp.send_message(message)


def run() -> int:
    target = os.getenv("TARGET_USERNAME", "zero2sudo").strip().lstrip("@").lower()
    target_user_id_text = os.getenv("TARGET_USER_ID", "50350974961").strip()
    if not target_user_id_text.isdigit():
        raise RuntimeError("TARGET_USER_ID must contain only digits")
    target_user_id = int(target_user_id_text)
    login_username = required_env("INSTAGRAM_USERNAME")
    session_file = Path(os.getenv("INSTAGRAM_SESSION_FILE", "instagram.session"))
    gmail_address = required_env("GMAIL_ADDRESS")
    gmail_password = required_env("GMAIL_APP_PASSWORD")
    recipient = os.getenv("EMAIL_TO", gmail_address).strip() or gmail_address
    state_file = Path(os.getenv("STATE_FILE", "data/seen_stories.json"))

    if not session_file.is_file():
        raise RuntimeError(f"Instagram session file not found: {session_file}")

    seen = read_seen(state_file)
    active = fetch_active_stories(target, session_file, login_username, target_user_id)
    new_stories = [story for story in active if story.story_id not in seen]

    if not new_stories:
        print(f"No new Stories for @{target}. Active: {len(active)}; already seen: {len(seen)}")
        return 0

    ordered_seen = list(seen)
    for story in new_stories:
        attachment, note = download_attachment(story)
        send_email(build_message(story, gmail_address, recipient, attachment, note), gmail_password)
        ordered_seen.append(story.story_id)
        write_seen(state_file, ordered_seen)
        print(f"Emailed Story {story.story_id}: {story.instagram_url}")
    return len(new_stories)


def main() -> None:
    try:
        run()
    except Exception as exc:
        raise SystemExit(f"Story monitor failed: {exc}") from exc
