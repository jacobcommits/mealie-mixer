from fastapi.testclient import TestClient

import app


def test_root_serves_new_ui():
    r = TestClient(app.fastapi_app).get("/")
    assert r.status_code == 200
    assert "Mealie" in r.text and "mixer()" in r.text


def test_static_assets_served():
    c = TestClient(app.fastapi_app)
    assert c.get("/app.js").status_code == 200
    assert c.get("/style.css").status_code == 200
    assert c.get("/vendor/alpine.min.js").status_code == 200


def test_admin_gone():
    # Gradio was removed in Stage 4 — /admin no longer exists
    c = TestClient(app.fastapi_app)
    assert c.get("/admin/").status_code == 404


def test_api_and_docs_intact():
    c = TestClient(app.fastapi_app)
    assert c.get("/docs").status_code == 200
    assert c.get("/api/health").status_code == 200
