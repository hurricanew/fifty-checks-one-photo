import base64
import json
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import ROOT
from app.labels import FALLBACK_LABEL, RECORDED_LABEL, SAMPLE_LABEL
from app.sample_data import ReplaySource, load_sample, replay_hint
from app.main import create_app
from app.service import CallFailed, MissingApiKey, decisions_unavailable, evaluate
from app.tiles import classify

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def test_upload_bytes_are_not_logged_or_written(monkeypatch, caplog):
    import logging

    marker = b"UNIQUEPHOTOMARKER12345"
    blob = JPEG + marker
    printed = []

    def spy_print(*args, **_kwargs):
        printed.append(" ".join(str(arg) for arg in args))

    monkeypatch.setattr("builtins.print", spy_print)
    caplog.set_level(logging.DEBUG)
    client = TestClient(create_app())
    before = _tree(ROOT)
    response = client.post(
        "/api/check",
        files={"photo": ("person.jpg", blob, "image/jpeg")},
        data={"mode": "replay", "fallback": "0"},
    )
    assert response.status_code == 200
    marker_text = marker.decode()
    assert marker_text not in response.text
    assert marker_text not in caplog.text
    assert all(marker_text not in line for line in printed)
    assert _tree(ROOT) == before
    for path in (ROOT / "app").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "logging." not in source
        assert "print(" not in source
        assert "write_bytes" not in source


def test_page_and_meta_describe_the_sample_and_the_fifty_checks(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = TestClient(create_app())
    page = client.get("/")
    assert page.status_code == 200
    assert "50 checks on one photo" in page.text
    assert 'id="replay-copy"' in page.text
    assert "Run <code>scripts/refresh_sample.py</code> later" not in page.text
    assert (
        "Your photo is checked in memory and never stored or logged. "
        "We only look at the photo, never rate how anyone looks."
    ) in page.text
    meta = client.get("/api/meta").json()
    assert meta["checks"] == 50
    assert meta["live_key_set"] is False
    assert meta["sample_label"] == SAMPLE_LABEL
    assert meta["cost_formula"] == "input_tokens * 0.10 / 1000000"
    shipped = json.loads((ROOT / "data" / "sample_response.json").read_text(encoding="utf-8"))
    replay = meta["replay"]
    assert replay["hint"]
    assert replay["label"] == shipped["label"]
    if shipped["label"] == SAMPLE_LABEL:
        assert replay["recorded"] is False
        assert replay["latency_ms"] is None
        assert replay["cost_usd"] is None
        assert SAMPLE_LABEL in replay["hint"]
        assert "refresh_sample.py" in replay["hint"]
    else:
        assert replay["recorded"] is True
        assert replay["photo_name"] == shipped["photo_name"]
        assert replay["latency_ms"] == shipped["latency_ms"]
        assert replay["input_tokens"] == shipped["usage"]["total_input_tokens"]
        assert replay["cost_usd"] == shipped["cost_usd"]
        assert shipped["photo_name"] in replay["hint"]
        assert "one real call" in replay["hint"]
        assert "does not call the API or score your photo" in replay["hint"]
        assert SAMPLE_LABEL not in replay["hint"]
        assert f"{shipped['latency_ms']:.1f} ms" in replay["hint"]
        assert str(shipped["usage"]["total_input_tokens"]) in replay["hint"]
        assert f"US${shipped['cost_usd']}" in replay["hint"]


def test_replay_renders_fifty_tiles_and_does_not_invent_measurements(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    shipped = json.loads((ROOT / "data" / "sample_response.json").read_text(encoding="utf-8"))
    client = TestClient(create_app())
    response = client.post(
        "/api/check",
        files={"photo": ("person.png", PNG, "image/png")},
        data={"mode": "replay", "fallback": "0"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["fallback_label"] is None
    assert body["source"] == "replay"
    assert body["photo"]["sent_to_model"] is False
    assert body["photo"]["byte_size"] == len(PNG)
    # This request did not call the API, so the header stays unmeasured.
    assert body["timing"]["measured"] is False
    assert body["timing"]["latency_ms"] is None
    assert body["usage"]["measured"] is False
    assert body["usage"]["input_tokens"] is None
    assert body["usage"]["output_tokens"] is None
    assert body["usage"]["cost_usd"] is None
    assert len(body["tiles"]) == 50
    assert sum(body["summary"].values()) == 50
    if shipped["label"] == SAMPLE_LABEL:
        assert body["sample_label"] == SAMPLE_LABEL
        assert body["recorded_label"] is None
        assert "will not invent" in body["usage"]["note"]
    else:
        assert shipped["label"] == RECORDED_LABEL
        assert shipped["recorded"] is True
        assert body["sample_label"] is None
        assert body["recorded_label"] == shipped["label"]
        note = body["usage"]["note"]
        assert f"{shipped['latency_ms']:.1f}" in note
        assert str(shipped["usage"]["total_input_tokens"]) in note
        assert f"US${shipped['cost_usd']}" in note
        assert "not for the photo you just chose" in note
    app = client.app
    by_key = {check.key: check for check in app.state.checks}
    for tile in body["tiles"]:
        check = by_key[tile["key"]]
        assert (tile["status"], tile["reason"]) == classify(
            tile["p_yes"],
            tile["confidence"],
            check.yes_is_good,
            app.state.settings.thresholds,
        )
        file_answer = shipped["answers"][tile["key"]]
        assert tile["p_yes"] == file_answer["noul"]
        assert tile["confidence"] == file_answer["confidence"]
    assert {tile["status"] for tile in body["tiles"]} == {"green", "red", "amber"}


def test_placeholder_replay_shows_the_sample_label_and_no_figures(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    app = create_app()
    answers = {
        check.key: type("A", (), {"p_yes": 0.2 if check.yes_is_good else 0.8, "confidence": 0.9})()
        for check in app.state.checks
    }
    # One uncertain and one low-confidence answer so all three colours appear.
    first, second, third = app.state.checks[:3]
    answers[first.key] = type("A", (), {"p_yes": 0.93, "confidence": 0.91})()
    answers[second.key] = type("A", (), {"p_yes": 0.5, "confidence": 0.95})()
    answers[third.key] = type("A", (), {"p_yes": 0.96, "confidence": 0.2})()
    payload = evaluate(
        mode="replay",
        image=PNG,
        mime_type="image/png",
        checks=app.state.checks,
        settings=app.state.settings,
        sample_answers=ReplaySource(answers=answers, kind="sample"),
        fallback_requested=False,
    )
    assert payload["sample_label"] == SAMPLE_LABEL
    assert payload["recorded_label"] is None
    assert payload["timing"]["latency_ms"] is None
    assert payload["usage"]["input_tokens"] is None
    assert payload["usage"]["cost_usd"] is None
    assert len(payload["tiles"]) == 50
    hint = replay_hint(ReplaySource(answers=answers, kind="sample"))
    assert SAMPLE_LABEL in hint
    assert "no measured latency, tokens, or cost" in hint
    assert "one real call on" not in hint
    assert load_sample(ROOT / "data" / "sample_response.json").kind in {"sample", "recorded"}


def test_live_mode_without_a_key_fails_cleanly(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "not-the-variable-this-app-reads")
    client = TestClient(create_app())
    response = client.post(
        "/api/check",
        files={"photo": ("rack.jpg", JPEG, "image/jpeg")},
        data={"mode": "live", "fallback": "1"},
    )
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "GEMINI_API_KEY is not set" in detail
    assert "will not invent" in detail


def test_bad_upload_is_rejected():
    client = TestClient(create_app())
    response = client.post(
        "/api/check",
        files={"photo": ("rack.jpg", PNG, "image/jpeg")},
        data={"mode": "replay"},
    )
    assert response.status_code == 400
    assert "not valid image/jpeg" in response.json()["detail"]


def test_replay_does_not_write_the_photo(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    def boom(*_args, **_kwargs):
        raise AssertionError("a temp file was opened while handling an upload")

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", boom)
    monkeypatch.setattr(tempfile, "TemporaryFile", boom)
    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", boom)
    monkeypatch.setattr(tempfile, "mkstemp", boom)
    before = _tree(ROOT)
    client = TestClient(create_app())
    blob = b"\xff\xd8\xff\xe0" + b"\x00" * (2 * 1024 * 1024)
    response = client.post(
        "/api/check",
        files={"photo": ("big.jpg", blob, "image/jpeg")},
        data={"mode": "replay"},
    )
    assert response.status_code == 200
    assert response.json()["photo"]["byte_size"] == len(blob)
    assert _tree(ROOT) == before
    assert not (ROOT / "evidence" / "measurements.json").exists()


def test_live_call_times_only_the_client_and_prices_input_tokens(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
    app = create_app()
    image = JPEG
    captured = {}

    class Decisions:
        def create(self, **kwargs):
            captured.update(kwargs)
            time.sleep(0.05)
            answers = {
                check.key: _Answer(0.91, 0.9) for check in app.state.checks
            }
            answers["red_eye"] = _Answer(0.11, 0.88)
            return _Result(answers, 2500, 0)

    class Client:
        decisions = Decisions()

        def __init__(self):
            captured["built"] = True

    def factory(_key):
        captured["key"] = _key
        return Client()

    started = time.perf_counter()
    payload = evaluate(
        mode="live",
        image=image,
        mime_type="image/jpeg",
        checks=app.state.checks,
        settings=app.state.settings,
        sample_answers=app.state.sample,
        fallback_requested=False,
        client_factory=factory,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert captured["key"] == "test-key-not-used"
    assert captured["model"] == "gemini-decision-1"
    assert base64.b64decode(captured["input"][1]["data"]) == image
    assert payload["source"] == "decisions"
    assert payload["sample_label"] is None
    assert payload["timing"]["measured"] is True
    assert payload["timing"]["latency_ms"] >= 40
    assert payload["timing"]["latency_ms"] <= elapsed_ms
    assert payload["usage"]["input_tokens"] == 2500
    assert payload["usage"]["output_tokens"] == 0
    assert payload["usage"]["cost_usd"] == "0.00025000"
    assert payload["photo"]["sent_to_model"] is True


def test_missing_key_does_not_build_a_client(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    app = create_app()

    def factory(_key):
        raise AssertionError("client factory should not run without a key")

    with pytest_raises_missing():
        evaluate(
            mode="live",
            image=JPEG,
            mime_type="image/jpeg",
            checks=app.state.checks,
            settings=app.state.settings,
            sample_answers=app.state.sample,
            fallback_requested=True,
            client_factory=factory,
        )


def test_fallback_runs_only_when_decisions_is_unavailable(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
    app = create_app()
    calls = {"generate": 0}

    class Unavailable(Exception):
        def __init__(self):
            super().__init__("404 NOT_FOUND. gemini-decision-1 is not found")
            self.code = 404
            self.status = "NOT_FOUND"
            self.message = "models/gemini-decision-1 is not found"

    class Denied(Exception):
        def __init__(self):
            super().__init__("401 UNAUTHENTICATED. API key not valid")
            self.code = 401
            self.status = "UNAUTHENTICATED"
            self.message = "API key not valid"

    class Decisions:
        def __init__(self, error):
            self.error = error

        def create(self, **_kwargs):
            raise self.error

    class Models:
        def generate_content(self, **kwargs):
            calls["generate"] += 1
            calls["model"] = kwargs["model"]
            text = _fallback_json(app)
            return _FallbackResponse(text, prompt=90, candidates=60)

    class Client:
        def __init__(self, error):
            self.decisions = Decisions(error)
            self.models = Models()

    payload = evaluate(
        mode="live",
        image=JPEG,
        mime_type="image/jpeg",
        checks=app.state.checks,
        settings=app.state.settings,
        sample_answers=app.state.sample,
        fallback_requested=True,
        client_factory=lambda _key: Client(Unavailable()),
    )
    assert calls["generate"] == 1
    assert calls["model"] == app.state.settings.fallback_model
    assert payload["source"] == "fallback"
    assert payload["fallback_label"] == FALLBACK_LABEL
    assert "not calibrated" in payload["fallback_label"]
    assert "output tokens are billed" in payload["fallback_label"]
    assert payload["sample_label"] is None
    assert payload["usage"]["input_tokens"] == 90
    assert payload["usage"]["output_tokens"] == 60
    assert payload["usage"]["cost_usd"] is None
    assert payload["timing"]["measured"] is True
    assert len(payload["tiles"]) == 50

    calls["generate"] = 0
    try:
        evaluate(
            mode="live",
            image=JPEG,
            mime_type="image/jpeg",
            checks=app.state.checks,
            settings=app.state.settings,
            sample_answers=app.state.sample,
            fallback_requested=True,
            client_factory=lambda _key: Client(Denied()),
        )
    except CallFailed as exc:
        assert "rejected the credentials" in str(exc)
        assert "Fallback was not used" in str(exc)
    else:
        raise AssertionError("auth failure should not return tiles")
    assert calls["generate"] == 0
    assert decisions_unavailable(Unavailable())
    assert not decisions_unavailable(Denied())


class _Answer:
    def __init__(self, noul, confidence):
        self.type = "noul"
        self.noul = noul
        self.confidence = confidence


class _Usage:
    def __init__(self, input_tokens, output_tokens):
        self.total_input_tokens = input_tokens
        self.total_output_tokens = output_tokens


class _Result:
    def __init__(self, answers, input_tokens, output_tokens):
        self.answers = answers
        self.usage = _Usage(input_tokens, output_tokens)


class _UsageMeta:
    def __init__(self, prompt, candidates):
        self.prompt_token_count = prompt
        self.candidates_token_count = candidates
        self.thoughts_token_count = 0


class _FallbackResponse:
    def __init__(self, text, prompt, candidates):
        self.text = text
        self.usage_metadata = _UsageMeta(prompt, candidates)


def _fallback_json(app) -> str:
    import json

    answers = [
        {"key": check.key, "noul": 0.5, "confidence": 0.4} for check in app.state.checks
    ]
    return json.dumps({"answers": answers})


def _tree(root: Path) -> set[str]:
    skip = {".git", ".venv", ".pytest_cache", "__pycache__"}
    found = set()
    for path in root.rglob("*"):
        if any(part in skip for part in path.parts):
            continue
        if path.is_file():
            found.add(str(path.relative_to(root)))
    return found


def pytest_raises_missing():
    import pytest

    return pytest.raises(MissingApiKey)
