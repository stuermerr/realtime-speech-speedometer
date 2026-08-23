from fastapi.testclient import TestClient

from app.main import app


def test_health_check_does_not_require_or_expose_azure_settings() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
