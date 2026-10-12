"""Create separate local demo passwords in the ignored .env; never print credentials."""

import json
import secrets
from pathlib import Path

from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]


def main():
    path = ROOT / ".env"
    existing = dotenv_values(path).get("ACS_AUTH_ACCOUNTS")
    if existing and json.loads(existing):
        print("ACS_AUTH_ACCOUNTS already configured; existing accounts preserved.")
        return
    accounts = {f"demo{i}": {"user_id": f"USER{i:03d}", "password": secrets.token_urlsafe(18)}
                for i in range(1, 4)}
    set_key(path, "ACS_AUTH_ACCOUNTS", json.dumps(accounts), quote_mode="always")
    print("Created demo1 / demo2 / demo3. Read passwords locally in .env (ACS_AUTH_ACCOUNTS).")


if __name__ == "__main__":
    main()
