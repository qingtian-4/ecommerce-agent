import pytest

from acs.readiness import check_ready
from acs.scenarios import build_scenario, scenario_settings
from acs.sessions import SessionStore


def test_document_mode_readiness_does_not_require_embedding_key_or_index(settings):
    settings = settings.model_copy(update={"knowledge_mode": "documents"})
    with pytest.raises(RuntimeError, match="迁移"):
        check_ready(settings)
    SessionStore(settings.db_path)
    assert check_ready(settings) == {"ready": True, "schema_version": 6, "documents": 8,
                                    "knowledge_mode": "documents", "provider_checked": False}


def test_retrieval_readiness_rejects_missing_and_stale_index(settings, fake_embedder):
    from acs.rag import KnowledgeBase

    SessionStore(settings.db_path)
    with pytest.raises((FileNotFoundError, RuntimeError)):
        check_ready(settings)
    KnowledgeBase(settings, llm=fake_embedder).build()
    assert check_ready(settings)["ready"] is True
    changed = settings.model_copy(update={"embedding_model": "different-model"})
    with pytest.raises(RuntimeError, match="model|模型|过期"):
        check_ready(changed)


def test_new_scenario_is_ready_without_touching_original_database(settings, tmp_path):
    original = settings.db_path.read_bytes()
    root = tmp_path / "v2"
    build_scenario(root)
    profile = scenario_settings(settings, root)
    assert check_ready(profile)["documents"] == 16
    assert settings.db_path.read_bytes() == original
