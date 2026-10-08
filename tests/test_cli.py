"""Configuration inspection must work without retired settings."""

from typer.testing import CliRunner

from acs.cli import app
from acs.config import Settings


def test_info_uses_current_settings_and_hides_credentials(monkeypatch):
    settings = Settings(_env_file=None, llm_api_key="audit-cli-placeholder")
    monkeypatch.setattr("acs.cli.get_settings", lambda: settings)

    result = CliRunner().invoke(app, ["info"])

    assert result.exit_code == 0
    assert settings.llm_model in result.output
    assert "***set***" in result.output
    assert settings.llm_api_key not in result.output
