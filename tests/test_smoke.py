import importlib
import sys

from fastapi.testclient import TestClient


def test_health_endpoint_smoke(monkeypatch):
    monkeypatch.setenv("AUTH_BYPASS_FOR_LOCAL", "true")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "")

    from lti_app.config import get_settings

    get_settings.cache_clear()
    sys.modules.pop("lti_app.main", None)
    main = importlib.import_module("lti_app.main")

    with TestClient(main.app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    get_settings.cache_clear()
