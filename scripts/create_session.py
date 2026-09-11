from __future__ import annotations

import getpass
from pathlib import Path

import instaloader


def main() -> None:
    username = input("Instagram username (a secondary account is recommended): ").strip()
    if not username:
        raise SystemExit("Username cannot be empty")

    loader = instaloader.Instaloader()
    loader.login(username, getpass.getpass("Instagram password: "))
    output = Path("instagram.session").resolve()
    loader.save_session_to_file(str(output))
    print(f"Session saved to {output}")
    print("This file contains a login session. Never commit or share it.")


if __name__ == "__main__":
    main()

