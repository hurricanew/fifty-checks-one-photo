import importlib.util
import os
import subprocess
import sys

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
