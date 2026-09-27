"""
tests/test_web.py - Tests for the web pages and the admin login.

TestClient runs the whole FastAPI app in memory (no real server), sending
requests like a browser would. Each test uses its own temporary database.
"""

import pytest
from fastapi.testclient import TestClient

import auth
import daraja
import database
import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Point the app at a throw-away database and a known admin password.
    monkeypatch.setattr(main, "get_connection",
                        lambda: database.get_connection(tmp_path / "web.db"))
    monkeypatch.setattr(auth, "ADMIN_PASSWORD", "test-pass")
    monkeypatch.setattr(daraja, "is_configured", lambda: False)   # never call Safaricom
    with TestClient(main.app) as test_client:        # "with" runs the lifespan (start-up)
        yield test_client


@pytest.mark.parametrize("path", ["/", "/display", "/entry", "/exit", "/admin/login"])
def test_public_pages_load(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert "Tekashi" in response.text


def test_admin_page_needs_login(client):
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_admin_api_needs_login(client):
    assert client.get("/api/reports/summary").status_code == 401
    assert client.put("/api/rates", json={"tiers": [{"fee_kes": 0}]}).status_code == 401


def test_wrong_password_is_refused(client):
    response = client.post("/admin/login", data={"password": "nope"})
    assert response.status_code == 401


def test_login_then_admin_works_then_logout(client):
    client.post("/admin/login", data={"password": "test-pass"})   # cookie is kept by the client
    assert client.get("/admin").status_code == 200
    assert client.get("/api/reports/summary").status_code == 200
    client.post("/admin/logout")
    assert client.get("/api/reports/summary").status_code == 401


def test_public_display_hides_plates(client):
    client.post("/api/entry", json={"plate": "KDA123X"})
    assert "KDA123X" not in client.get("/display").text
    assert "KDA123X" not in str(client.get("/api/display").json())
