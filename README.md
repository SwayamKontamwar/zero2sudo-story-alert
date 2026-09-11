# zero2sudo Instagram Story email alerts

A free, self-hosted monitor that checks the public Instagram profile
[`@zero2sudo`](https://www.instagram.com/zero2sudo/) every 30 minutes and emails you
when it finds a Story it has not reported before.

It runs on GitHub Actions, uses an Instagram session from a secondary account, sends
mail through Gmail SMTP, and remembers emailed Story IDs in `data/seen_stories.json`.
No password or session is stored in the repository.

## What you need

- A secondary Instagram account. Using your main account for automation is not recommended.
- A Gmail account with 2-Step Verification and an App Password.
- This repository hosted publicly on GitHub so standard Actions usage remains free.

Instagram can challenge or invalidate automated sessions, and changes to Instagram can
temporarily break Instaloader. Scheduled GitHub Actions can also start later than their
nominal time during busy periods. This project is therefore best-effort, not guaranteed.
If Instagram rate-limits a shared GitHub runner, that check exits quickly and the next
scheduled run tries again instead of consuming minutes waiting inside the runner.

## One-time setup

### 1. Create the Instagram session locally

Install Python 3.12+, then from this repository run:

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python scripts\create_session.py
```

Complete any Instagram challenge shown. The resulting `instagram.session` is ignored by
Git. Treat it like a password.

### 2. Add GitHub Actions secrets

Open the GitHub repository and go to **Settings → Secrets and variables → Actions**.
Create these repository secrets:

| Secret | Value |
|---|---|
| `INSTAGRAM_USERNAME` | Username used to create the session |
| `INSTAGRAM_SESSION_B64` | Base64 form of `instagram.session` |
| `GMAIL_ADDRESS` | Gmail address that sends the alert |
| `GMAIL_APP_PASSWORD` | 16-character Google App Password |
| `EMAIL_TO` | Destination email address (optional; defaults to `GMAIL_ADDRESS`) |

Generate the base64 value in PowerShell without printing the session contents:

```powershell
$bytes = [IO.File]::ReadAllBytes((Resolve-Path .\instagram.session))
[Convert]::ToBase64String($bytes) | Set-Clipboard
```

Create a Google App Password at <https://myaccount.google.com/apppasswords>. This normally
requires 2-Step Verification. Do not use your normal Google password.

### 3. Enable and test the workflow

Open **Actions → Instagram Story alert → Run workflow**. A successful run either sends
one email for every currently active unseen Story or reports that there are no new Stories.
After that, GitHub schedules checks at 7 and 37 minutes past each hour.

## Test locally without sending email

```powershell
.venv\Scripts\python -m unittest discover -s tests -v
```

The unit tests exercise state persistence, deduplication, direct Story links, and required
configuration. Live Instagram/Gmail access is intentionally tested only by the manually
triggered GitHub workflow so credentials never enter test fixtures.

## Change the account or frequency

- Change `TARGET_USERNAME` in `.github/workflows/story-alert.yml` to monitor another profile.
- Change the cron line to adjust frequency. GitHub schedules use UTC and may be delayed.
- Keep the repository public for unmetered standard GitHub-hosted Actions usage.

## Security notes

- Never commit `instagram.session`, Gmail App Passwords, or copied secret values.
- Rotate the Instagram session and Gmail App Password if either is exposed.
- A public repository exposes the monitored username and previously emailed Story IDs, but
  not the account credentials, recipient address, or session.
- This uses unofficial automation and may stop working when Instagram changes its systems.
