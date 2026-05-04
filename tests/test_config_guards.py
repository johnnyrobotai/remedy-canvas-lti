import pytest

from lti_app.config import Settings


def settings(**overrides):
    return Settings(_env_file=None, **overrides)


def test_development_auth_bypass_can_start_without_database_url():
    settings(
        auth_bypass_for_local=True,
        environment="development",
        database_url="",
    ).validate_for_startup()


def test_production_rejects_local_auth_bypass():
    with pytest.raises(RuntimeError, match="AUTH_BYPASS_FOR_LOCAL"):
        settings(
            auth_bypass_for_local=True,
            environment="production",
            database_url="postgresql://remedy:password@db:5432/canvas_remedy_lti",
            session_secret_key="ci-session-secret",
            app_base_url="https://lti.example.test",
            cors_origins="https://canvas.example.test",
            ollama_api_key="ci-ollama-token",
        ).validate_for_startup()


def test_production_rejects_insecure_public_url_and_wildcard_cors():
    with pytest.raises(RuntimeError) as exc_info:
        settings(
            auth_bypass_for_local=False,
            environment="production",
            database_url="postgresql://remedy:password@db:5432/canvas_remedy_lti",
            session_secret_key="ci-session-secret",
            app_base_url="http://lti.example.test",
            cors_origins="https://canvas.example.test,*",
            ollama_api_key="ci-ollama-token",
        ).validate_for_startup()

    message = str(exc_info.value)
    assert "APP_BASE_URL must be set to the public https:// URL" in message
    assert "CORS_ORIGINS must not contain '*'" in message


def test_production_requires_ollama_api_key_for_ollama_cloud():
    with pytest.raises(RuntimeError, match="OLLAMA_API_KEY"):
        settings(
            auth_bypass_for_local=False,
            environment="production",
            database_url="postgresql://remedy:password@db:5432/canvas_remedy_lti",
            session_secret_key="ci-session-secret",
            app_base_url="https://lti.example.test",
            cors_origins="https://canvas.example.test",
            ollama_base_url="https://ollama.com/v1",
            ollama_api_key="",
        ).validate_for_startup()


def test_production_accepts_required_runtime_config():
    settings(
        auth_bypass_for_local=False,
        environment="production",
        database_url="postgresql://remedy:password@db:5432/canvas_remedy_lti",
        session_secret_key="ci-session-secret",
        app_base_url="https://lti.example.test",
        cors_origins="https://canvas.example.test",
        ollama_api_key="ci-ollama-token",
    ).validate_for_startup()
