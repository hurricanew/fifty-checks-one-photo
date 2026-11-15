import importlib.util
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout

from app.config import ROOT


def _load():
    path = ROOT / "scripts" / "measure.py"
    spec = importlib.util.spec_from_file_location("measure_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_percentile_and_markdown_use_only_successful_calls():
    measure = _load()
    values = [10, 20, 30, 40, 50]
    assert measure.percentile(values, 50) == 30
    assert measure.percentile(values, 95) == 48
    summary = measure.summarize_latency(values)
    assert summary == {"n": 5, "min": 10, "p50": 30, "p95": 48, "max": 50}
    assert measure.summarize_latency([]) is None
    document = {
        "model": "gemini-decision-1",
        "calls": [
            {
                "photo": "a.jpg",
                "ok": True,
                "latency_ms": 10,
                "input_tokens": 100,
                "output_tokens": 0,
                "cost_usd": "0.00001000",
            },
            {"photo": "b.jpg", "ok": False, "error": "failed"},
        ],
        "latency_ms": measure.summarize_latency([10]),
    }
    text = measure.render_markdown(document)
    assert "p50 10.0" in text
    assert "| b.jpg | no |  |  |  |  |" in text
    assert "0.00001000" in text
    document["image_resolution"] = "high"
    summary = measure.render_human(document)
    assert "resolution high" in summary
    assert "1 succeeded, 1 failed" in summary
    assert "p50 10.0" in summary
    assert "0.00001000" in summary
    assert "Output tokens are not billed" in summary


def test_measure_refuses_without_gemini_api_key(tmp_path):
    env = os.environ.copy()
    env.pop("GEMINI_API_KEY", None)
    env["GOOGLE_API_KEY"] = "ignored-by-this-script"
    output = tmp_path / "measurements.json"
    markdown = tmp_path / "measurements.md"
    output.write_text("sentinel-json", encoding="utf-8")
    markdown.write_text("sentinel-md", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "measure.py"),
            "--photos",
            str(ROOT / "photos"),
            "--calls",
            "3",
            "--output",
            str(output),
            "--markdown",
            str(markdown),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "GEMINI_API_KEY" in result.stderr
    assert "will not fabricate" in result.stderr
    assert output.read_text(encoding="utf-8") == "sentinel-json"
    assert markdown.read_text(encoding="utf-8") == "sentinel-md"
    assert not (ROOT / "evidence" / "measurements.json").exists()


def test_refresh_sample_refuses_without_a_key(tmp_path):
    env = os.environ.copy()
    env.pop("GEMINI_API_KEY", None)
    output = tmp_path / "sample_response.json"
    output.write_text("sentinel", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "refresh_sample.py"),
            str(ROOT / "photos" / "portrait-wavy-hair.jpg"),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "GEMINI_API_KEY" in result.stderr
    assert "will not fabricate" in result.stderr
    assert output.read_text(encoding="utf-8") == "sentinel"


def test_help_does_not_require_a_key():
    env = os.environ.copy()
    env.pop("GEMINI_API_KEY", None)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "measure.py"), "--help"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--calls" in result.stdout
    assert "--resolution" in result.stdout


def test_resolution_flag_rejects_other_values_without_a_key(tmp_path):
    env = os.environ.copy()
    env.pop("GEMINI_API_KEY", None)
    output = tmp_path / "measurements.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "measure.py"),
            "--photos",
            str(ROOT / "photos"),
            "--calls",
            "1",
            "--resolution",
            "low",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "medium" in result.stderr
    assert "high" in result.stderr
    assert not output.exists()


def test_high_resolution_is_sent_and_summarized_without_a_network_call(tmp_path, monkeypatch):
    measure = _load()
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
    monkeypatch.setattr(measure, "build_client", lambda _key: object())
    seen = {}

    def timed(_client, request):
        seen["resolution"] = request["input"][1]["resolution"]

        class Usage:
            total_input_tokens = 100
            total_output_tokens = 0

        class Result:
            answers = {}
            usage = Usage()

        return Result(), 12.5

    monkeypatch.setattr(measure, "timed_decisions_call", timed)
    output = tmp_path / "measurements.json"
    markdown = tmp_path / "measurements.md"
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = measure.main(
            [
                "--photos",
                str(ROOT / "photos"),
                "--calls",
                "1",
                "--resolution",
                "high",
                "--output",
                str(output),
                "--markdown",
                str(markdown),
            ]
        )
    assert code == 0
    assert seen["resolution"] == "high"
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["image_resolution"] == "high"
    assert document["calls"][0]["latency_ms"] == 12.5
    assert document["calls"][0]["cost_usd"] == "0.00001000"
    printed = buf.getvalue()
    assert "resolution high" in printed
    assert "p50 12.5" in printed
    assert "Wrote" in printed
    assert "high" in markdown.read_text(encoding="utf-8")
    assert not (ROOT / "evidence" / "measurements.json").exists()
