"""Demo account authentication; caller identity always comes from the server request."""

from __future__ import annotations

import hmac
from typing import Any

from .config import Settings


def authenticate(settings: Settings, username: str, password: str) -> bool:
    account = settings.auth_accounts.get(username)
    return bool(account and hmac.compare_digest(
        password.encode(), account.password.get_secret_value().encode(),
    ))


def user_id_for(settings: Settings, request: Any) -> str:
    account = settings.auth_accounts.get(getattr(request, "username", None))
    if account is None:
        raise PermissionError("请先使用演示账户登录")
    return account.user_id
