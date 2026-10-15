import sys

from scripts import serve

from acs.scenarios import data_fingerprint
from acs.sessions import SessionStore


def test_container_bootstrap_preserves_existing_history_and_business_data(settings, monkeypatch):
    store = SessionStore(settings.db_path)
    sid = store.create("USER001")
    before = data_fingerprint(settings.db_path)
    executed = []
    monkeypatch.setattr(serve, "get_settings", lambda: settings)
    monkeypatch.setattr(serve.os, "execv", lambda binary, args: executed.append(args))
    monkeypatch.setattr(sys, "argv", ["serve.py", "--port", "7880"])
    serve.main()
    assert SessionStore(settings.db_path).list("USER001")[0]["session_id"] == sid
    assert data_fingerprint(settings.db_path) == before
    assert executed[0][1:] == ["app.py", "--port", "7880"]


def test_container_bootstrap_can_initialise_only_an_absent_database(
    settings, tmp_path, monkeypatch,
):
    settings = settings.model_copy(update={"db_path": tmp_path / "new" / "business.db"})
    monkeypatch.setattr(serve, "get_settings", lambda: settings)
    monkeypatch.setattr(serve.os, "execv", lambda *args: None)
    monkeypatch.setattr(sys, "argv", ["serve.py"])
    serve.main()
    assert SessionStore(settings.db_path).list("USER001") == []
    assert settings.db_path.is_file()
