"""Authentication is independent of client identity hints and the agent prompt."""

import json
from types import SimpleNamespace

import pytest

from acs.auth import authenticate, user_id_for
from acs.config import DemoAccount


def test_demo_login_checks_password_and_maps_only_the_verified_username(settings):
    settings = settings.model_copy(update={"auth_accounts": {
        "alice": DemoAccount(user_id="USER001", password="测试密码"),
    }})
    assert authenticate(settings, "alice", "测试密码")
    assert not authenticate(settings, "alice", "wrong")
    assert not authenticate(settings, "unknown", "测试密码")
    assert user_id_for(settings, SimpleNamespace(username="alice", user_id="USER002")) == "USER001"
    assert "测试密码" not in repr(settings.auth_accounts)
    for request in (None, SimpleNamespace(username=None), SimpleNamespace(username="unknown")):
        with pytest.raises(PermissionError):
            user_id_for(settings, request)


def test_empty_demo_password_is_invalid():
    with pytest.raises(ValueError):
        DemoAccount(user_id="USER001", password="")


def test_account_setup_handles_empty_template_without_printing_or_replacing_passwords(
    tmp_path, monkeypatch, capsys,
):
    from scripts import configure_demo_accounts

    path = tmp_path / ".env"
    path.write_text("ACS_AUTH_ACCOUNTS={}\nACS_LLM_API_KEY=placeholder\n")
    monkeypatch.setattr(configure_demo_accounts, "ROOT", tmp_path)
    configure_demo_accounts.main()
    from dotenv import dotenv_values

    first = path.read_bytes()
    accounts = json.loads(dotenv_values(path)["ACS_AUTH_ACCOUNTS"])
    assert len(accounts) == 3 and len({a["password"] for a in accounts.values()}) == 3
    configure_demo_accounts.main()
    assert path.read_bytes() == first
    printed = capsys.readouterr().out
    assert all(a["password"] not in printed for a in accounts.values())
    assert dotenv_values(path)["ACS_LLM_API_KEY"] == "placeholder"
