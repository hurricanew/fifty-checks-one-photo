import base64
import tempfile

import pytest
from fastapi.testclient import TestClient

from app.config import ROOT
from app.limits import HOUR_SECONDS, DAY_SECONDS, LiveCallLimiter, client_ip, env_flag, env_int
from app.main import create_app

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _clear_limit_env(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("TRUST_PROXY", raising=False)
    monkeypatch.delenv("LIVE_CALLS_PER_IP_PER_HOUR", raising=False)
    monkeypatch.delenv("LIVE_CALLS_PER_DAY", raising=False)


def _post(client, mode="live", content=JPEG, mime="image/jpeg", headers=None):
    return client.post(
        "/api/check",
        files={"photo": ("rack.jpg", content, mime)},
        data={"mode": mode, "fallback": "0"},
        headers=headers,
    )


def test_defaults_and_env_parsing(monkeypatch):
    _clear_limit_env(monkeypatch)
    assert env_int("LIVE_CALLS_PER_IP_PER_HOUR", 5) == 5
    assert env_int("LIVE_CALLS_PER_DAY", 200) == 200
    assert env_flag("TRUST_PROXY") is False
    monkeypatch.setenv("LIVE_CALLS_PER_IP_PER_HOUR", "7")
    monkeypatch.setenv("LIVE_CALLS_PER_DAY", "0")
    monkeypatch.setenv("TRUST_PROXY", "yes")
    assert env_int("LIVE_CALLS_PER_IP_PER_HOUR", 5) == 7
    assert env_int("LIVE_CALLS_PER_DAY", 200) == 0
    assert env_flag("TRUST_PROXY") is True
    monkeypatch.setenv("LIVE_CALLS_PER_DAY", "nope")
    with pytest.raises(ValueError, match="integer"):
        env_int("LIVE_CALLS_PER_DAY", 200)
    monkeypatch.setenv("LIVE_CALLS_PER_DAY", "-1")
    with pytest.raises(ValueError, match=">="):
        env_int("LIVE_CALLS_PER_DAY", 200)
    monkeypatch.setenv("TRUST_PROXY", "maybe")
    with pytest.raises(ValueError, match="TRUST_PROXY"):
        env_flag("TRUST_PROXY")


def test_windows_reset_and_daily_cap_is_shared():
    now = {"t": 1_000_000.0}
    limiter = LiveCallLimiter(per_ip_per_hour=1, per_day=2, clock=lambda: now["t"])
    limiter.allow("a")
    limiter.allow("b")
    with pytest.raises(Exception, match="daily cap"):
        limiter.allow("c")
    now["t"] += HOUR_SECONDS + 1
    with pytest.raises(Exception, match="daily cap"):
        limiter.allow("a")
    now["t"] += DAY_SECONDS
    limiter.allow("a")
    limiter.allow("b")


def test_ip_window_resets_after_an_hour():
    now = {"t": 0.0}
    limiter = LiveCallLimiter(per_ip_per_hour=1, per_day=10, clock=lambda: now["t"])
    limiter.allow("a")
    with pytest.raises(Exception, match="per IP per hour"):
        limiter.allow("a")
    now["t"] = HOUR_SECONDS + 1
    limiter.allow("a")


def test_forwarded_for_is_ignored_unless_the_proxy_is_trusted():
    assert client_ip("1.2.3.4", "9.9.9.9", trust_proxy=False) == "1.2.3.4"
    assert client_ip("1.2.3.4", "8.8.8.8, 9.9.9.9", trust_proxy=True) == "9.9.9.9"
    assert client_ip("1.2.3.4", None, trust_proxy=True) == "1.2.3.4"
    assert client_ip(None, None, trust_proxy=False) == "unknown"


def test_live_limit_returns_429_and_replay_still_works(monkeypatch):
    _clear_limit_env(monkeypatch)
    monkeypatch.setenv("LIVE_CALLS_PER_IP_PER_HOUR", "2")
    monkeypatch.setenv("LIVE_CALLS_PER_DAY", "10")
    app = create_app()
    calls = []
    real = __import__("app.main", fromlist=["evaluate"]).evaluate

    def spy(**kwargs):
        calls.append(kwargs["mode"])
        return real(**kwargs)

    monkeypatch.setattr("app.main.evaluate", spy)

    def boom(*_args, **_kwargs):
        raise AssertionError("a temp file was opened")

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", boom)
    monkeypatch.setattr(tempfile, "TemporaryFile", boom)
    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", boom)
    monkeypatch.setattr(tempfile, "mkstemp", boom)

    client = TestClient(app, client=("203.0.113.5", 50000))
    before = _files()
    first = _post(client)
    second = _post(client)
    blocked = _post(client, headers={"X-Forwarded-For": "198.51.100.9"})
    replay = _post(client, mode="replay")
    bad = _post(client, content=PNG, mime="image/jpeg")
    assert first.status_code == 503
    assert second.status_code == 503
    assert blocked.status_code == 429
    assert "per IP per hour" in blocked.json()["detail"]
    assert "Replay mode still works" in blocked.json()["detail"]
    assert "not stored" in blocked.json()["detail"]
    assert replay.status_code == 200
    assert replay.json()["source"] == "replay"
    assert bad.status_code == 400
    assert calls == ["live", "live", "replay"]
    assert _files() == before
    meta = client.get("/api/meta").json()
    assert meta["live_calls_per_ip_per_hour"] == 2
    assert meta["live_calls_per_day"] == 10
    assert meta["trust_proxy"] is False


def test_daily_cap_and_trusted_proxy_use_the_rightmost_forwarded_address(monkeypatch):
    _clear_limit_env(monkeypatch)
    monkeypatch.setenv("LIVE_CALLS_PER_IP_PER_HOUR", "5")
    monkeypatch.setenv("LIVE_CALLS_PER_DAY", "2")
    monkeypatch.setenv("TRUST_PROXY", "1")
    app = create_app()
    one = TestClient(app, client=("10.0.0.1", 9))
    two = TestClient(app, client=("10.0.0.2", 9))
    assert _post(one, headers={"X-Forwarded-For": "198.51.100.10"}).status_code == 503
    assert _post(two, headers={"X-Forwarded-For": "198.51.100.11"}).status_code == 503
    blocked = _post(one, headers={"X-Forwarded-For": "198.51.100.12"})
    assert blocked.status_code == 429
    assert "daily cap" in blocked.json()["detail"]
    replay = _post(two, mode="replay", headers={"X-Forwarded-For": "198.51.100.12"})
    assert replay.status_code == 200

    monkeypatch.setenv("LIVE_CALLS_PER_IP_PER_HOUR", "1")
    monkeypatch.setenv("LIVE_CALLS_PER_DAY", "20")
    app = create_app()
    client = TestClient(app, client=("10.1.1.1", 9))
    assert _post(client, headers={"X-Forwarded-For": "203.0.113.8, 203.0.113.9"}).status_code == 503
    again = _post(client, headers={"X-Forwarded-For": "198.51.100.1, 203.0.113.9"})
    assert again.status_code == 429
    assert "per IP per hour" in again.json()["detail"]
    other = _post(client, headers={"X-Forwarded-For": "203.0.113.9, 198.51.100.50"})
    assert other.status_code == 503


def test_bad_upload_does_not_consume_a_live_slot(monkeypatch):
    _clear_limit_env(monkeypatch)
    monkeypatch.setenv("LIVE_CALLS_PER_IP_PER_HOUR", "1")
    monkeypatch.setenv("LIVE_CALLS_PER_DAY", "10")
    client = TestClient(create_app(), client=("203.0.113.20", 9))
    assert _post(client, content=PNG, mime="image/jpeg").status_code == 400
    assert _post(client).status_code == 503
    assert _post(client).status_code == 429


def _files() -> set[str]:
    skip = {".git", ".venv", ".pytest_cache", "__pycache__"}
    found = set()
    for path in ROOT.rglob("*"):
        if any(part in skip for part in path.parts):
            continue
        if path.is_file():
            found.add(str(path.relative_to(ROOT)))
    return found
