import base64

import pytest
from fastapi import Depends, FastAPI
from fastapi.security import HTTPBasicCredentials
from fastapi.testclient import TestClient

from leadgen.api.auth import require_auth


def _basic_auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


# ---- require_auth() as a pure function, no app/DB needed ----


def test_require_auth_accepts_correct_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_UI_USERNAME", "alice")
    monkeypatch.setenv("WEB_UI_PASSWORD", "secret")
    result = require_auth(HTTPBasicCredentials(username="alice", password="secret"))
    assert result == "alice"


def test_require_auth_rejects_wrong_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_UI_USERNAME", "alice")
    monkeypatch.setenv("WEB_UI_PASSWORD", "secret")
    with pytest.raises(Exception) as exc_info:
        require_auth(HTTPBasicCredentials(username="alice", password="wrong"))
    assert exc_info.value.status_code == 401
    assert exc_info.value.headers == {"WWW-Authenticate": "Basic"}


def test_require_auth_rejects_wrong_username(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_UI_USERNAME", "alice")
    monkeypatch.setenv("WEB_UI_PASSWORD", "secret")
    with pytest.raises(Exception) as exc_info:
        require_auth(HTTPBasicCredentials(username="mallory", password="secret"))
    assert exc_info.value.status_code == 401


def test_require_auth_raises_clearly_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    # Not just delenv: a real local .env (this repo's own dev one, say)
    # could otherwise refill these via _configured_credentials()'s own
    # load_dotenv() call and mask exactly the condition this test means
    # to exercise -- stub that out too, not just the environment.
    monkeypatch.setattr("leadgen.api.auth.load_dotenv", lambda: None)
    monkeypatch.delenv("WEB_UI_USERNAME", raising=False)
    monkeypatch.delenv("WEB_UI_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="WEB_UI_USERNAME and WEB_UI_PASSWORD"):
        require_auth(HTTPBasicCredentials(username="anyone", password="anything"))


# ---- wired into a real app the way api/review.py does it ----


@pytest.fixture
def protected_app() -> FastAPI:
    app = FastAPI(dependencies=[Depends(require_auth)])

    @app.get("/secret")
    def secret() -> dict:
        return {"ok": True}

    return app


def test_route_rejects_request_with_no_credentials(protected_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_UI_USERNAME", "alice")
    monkeypatch.setenv("WEB_UI_PASSWORD", "secret")
    resp = TestClient(protected_app).get("/secret")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Basic"


def test_route_rejects_wrong_credentials(protected_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_UI_USERNAME", "alice")
    monkeypatch.setenv("WEB_UI_PASSWORD", "secret")
    resp = TestClient(protected_app, headers={"Authorization": _basic_auth_header("alice", "nope")}).get("/secret")
    assert resp.status_code == 401


def test_route_allows_correct_credentials(protected_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_UI_USERNAME", "alice")
    monkeypatch.setenv("WEB_UI_PASSWORD", "secret")
    resp = TestClient(protected_app, headers={"Authorization": _basic_auth_header("alice", "secret")}).get("/secret")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
