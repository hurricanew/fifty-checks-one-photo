# Evidence

`scripts/measure.py` writes two files here after real Decisions API calls:

- `measurements.json` — per-call latency, input tokens, and cost, plus p50/p95/min/max latency
- `measurements.md` — the same figures as a short table

Those files are gitignored. This directory is empty of measurements on purpose. Latency, token counts, and dollar cost are recorded only when `GEMINI_API_KEY` is set and the script completes live calls. The script exits before writing anything when the key is missing, and it does not fill these files with sample numbers.

Replay mode and `data/sample_response.json` are labelled `SAMPLE - not a real API result` and are not measurements.
