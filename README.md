# 50 checks on one photo

Upload a photo of a person or a couple. The app asks [Gemini Decision 1](https://ai.google.dev/gemini-api/docs/decisions-api) fifty yes/no questions about the **photograph** in a **single** Decisions API call, then paints one tile per check. It does not rate how anyone looks.

- **Green** — that part of the photo is looking good.
- **Red** — that part is worth another try.
- **Amber** — confidence is below the cut-off, or P(yes) sits in the uncertain band. Have another look.

The page header shows three figures for that one call, and only when they were actually measured:

- **Latency** — `time.perf_counter()` around the API call itself, not around file reads or JSON parsing.
- **Input tokens** — `usage.total_input_tokens` from the response.
- **Cost** — `input_tokens * 0.10 / 1_000_000` US dollars. Output tokens are not billed on the Decisions API.

This repo does not ship measured latency, token counts, or cost. Those files are written only by `scripts/measure.py` after live calls. Replay mode loads `data/sample_response.json`, which is labelled **SAMPLE - not a real API result** until you replace it with `scripts/refresh_sample.py`. The upload is held in memory and is not stored or logged.

## Setup

Python 3.12. The SDK pin is `google-genai` 3.3.0 (3.2 or later is required).

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run locally

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8741
```

Open http://127.0.0.1:8741

Choose a photo of a person or a couple, leave **Replay sample** selected, and submit. You get 50 tiles and the sample banner. Latency, tokens, and cost stay “not measured”.

Your photo is checked in memory and never stored or logged. We only look at the photo, never rate how anyone looks. The cap is 10,485,760 bytes. Accepted types are JPEG, PNG, WebP, HEIC, and HEIF.

## Environment variables

| Variable | Role |
| --- | --- |
| `GEMINI_API_KEY` | Required for live mode and for `scripts/measure.py`. The app reads this variable and passes it to `genai.Client(api_key=..., http_options={"api_version": "v1beta"})`. Set it as a host secret. Never put it in the image. |
| `LIVE_CALLS_PER_IP_PER_HOUR` | Live calls from one address per rolling hour. Default 5. |
| `LIVE_CALLS_PER_DAY` | Live calls from everyone per rolling 24 hours. Default 200. |
| `TRUST_PROXY` | Set to `1` only behind a trusted reverse proxy. Then the client address is the rightmost `X-Forwarded-For` entry. Unset, that header is ignored. |
| `PORT` | Port the container listens on. See `DEPLOY.md`. |

`GOOGLE_API_KEY` alone is not treated as a key. Live mode and `measure.py` refuse to run when `GEMINI_API_KEY` is unset, including when some other Google variable is present. They do not invent a result.

No `.env` file is loaded. Do not commit a key. Public hosting is in `DEPLOY.md`.

## Modes

**Replay** loads `data/sample_response.json`. The probabilities are hand-written so the tiles show green, red, and amber. They are not a reading of the uploaded photo. The file deliberately has no latency, token, or cost fields.

**Live** builds one `client.decisions.create` request:

- model `gemini-decision-1`
- input: a short text line plus one image block (`type`, base64 `data`, `mime_type`, `resolution`)
- `questions`: 50 `noul` questions, each with `instructions` and `criteria.true_description` / `false_description`

The image resolution defaults to `medium` (`low`, `medium`, `high`, `ultra_high` are the documented values). Change it in `data/config.json`.

If `GEMINI_API_KEY` is missing, live mode returns HTTP 503 and a message that says the API was not called.

Live calls are also capped: 5 per address per rolling hour and 200 across everyone per rolling 24 hours, unless the env vars above say otherwise. The 6th call from one address, or a call after the daily cap, returns HTTP 429 and does not call the API. Replay is not counted. The counters are in process memory. They are not a photo log.

**Fallback** is optional and off unless the request asks for it (the checkbox on the page, sent as `fallback=1`). It runs only after a live call fails because the Decisions API looks unavailable (HTTP 404, 410, or 501, or a not-found / unimplemented error). Auth failures, quota errors, and other call failures are shown as errors and do **not** fall through. The fallback calls `gemini-3.5-flash` (override `fallback_model` in `data/config.json`) with structured JSON. The page labels that result:

> FALLBACK: regular Gemini structured output, not the Decisions API. Confidence is not calibrated, and output tokens are billed.

No US dollar figure is shown for the fallback. The Decisions input-only formula does not apply there, and this app does not guess a second price.

## Tile cut-offs

`data/config.json`:

- `confidence_min` `0.70` — confidence **below** this is amber. Equal to the cut-off is not amber.
- `uncertain_low` `0.40` and `uncertain_high` `0.60` — P(yes) inside the band, inclusive, is amber.
- Outside the band, P(yes) above `uncertain_high` is a yes and P(yes) below `uncertain_low` is a no. The tile is green or red from that answer and the check's `yes_is_good` flag.

Every cut-off must be at most **0.99**. The Decisions API keeps P(yes) in the closed range 0.01–0.99, so a threshold above 0.99 can never match. The loader rejects a higher cut-off. If you set `uncertain_high` to `0.99`, a decisive yes becomes impossible, because 0.99 is inside the inclusive band. The shipped band ends at 0.60, so 0.61–0.99 can still be a decisive yes.

Checks live in `data/checks.json`. Each one is a single visible question about the photograph (light, focus, eyes, expression, framing, background, colour, profile-picture fit) with a positive wording and a `yes_is_good` flag. A yes is not always a good sign: “Is red-eye visible?” is a problem when the answer is yes. None of the checks ask about attractiveness, body, age, or anything similar.

## Measure live calls

```bash
GEMINI_API_KEY=your-key python scripts/measure.py --photos photos --calls 3 --resolution medium
GEMINI_API_KEY=your-key python scripts/measure.py --photos photos --calls 3 --resolution high
```

`--resolution` is `medium` or `high`. Omit it to use `image_resolution` in `data/config.json`. This makes `--calls` live requests, cycling through the images in the folder if you ask for more calls than photos. It prints a short summary, and it writes:

- `evidence/measurements.json` — per-call latency, input tokens, output tokens, and cost, plus min / p50 / p95 / max latency
- `evidence/measurements.md` — the same table

p50 and p95 use linear interpolation on the sorted successful latencies (the same idea as NumPy’s linear percentile). Failed calls are stored with `ok: false` and **no** latency, token, or cost fields. If every call fails, the percentile block is null rather than filled with zeros.

Without `GEMINI_API_KEY` the script exits with status 2, prints a refusal, and does not create or overwrite the output files.

To replace the replay file with one real call:

```bash
GEMINI_API_KEY=your-key python scripts/refresh_sample.py photos/portrait-wavy-hair.jpg
```

That writes measured latency and token counts into `data/sample_response.json` and does not store the photo bytes. Without the key it refuses and leaves the sample file alone. The shipped file stays a labelled sample until you run that command.

## Tests

```bash
pytest
```

The tests cover tile cut-offs, the 50-check file, request building, parsing of a hand-written fake response, upload type and size checks, replay versus a missing key, and the guarantee that an upload does not open a temp file or add a file under the repo. They also check that `measure.py` refuses to run without `GEMINI_API_KEY`.

Tests stub the SDK. They do not call Google. A socket connect during a test raises.

## Photos

`photos/` contains four Wikimedia Commons photographs (CC0 and CC BY 2.0). Authors, licenses, and source links are in `photos/ATTRIBUTION.md`. They are not evidence of model accuracy.

## Limitations

- Nothing in this repository is a measured accuracy number. Tile colors are the cut-offs above applied to P(yes) and confidence. Those cut-offs are a starting point, not a threshold tuned on labeled portraits. Green means that one check looks fine. It is not a judgment of the person.
- Latency, tokens, and cost in the UI are shown only when this process timed a call or read `usage` from a response. Replay leaves them blank on purpose. Do not copy the sample probabilities into a report.
- The Decisions API returns calibrated probabilities for `gemini-decision-1`. The fallback model’s confidence is a self-rating from a normal generate call. It is not the same number, and that path bills output tokens.
- P(yes) never exceeds 0.99. A rule written as “only above 0.99” will not fire.
- The checks judge the photograph only. They do not score attractiveness, body, age, gender, race, or anything similar.
- The model only sees what is in the frame. A check about eyes or headroom is amber or wrong when that part of the photo is cropped, dark, or too small. Questions are independent; one answer does not see the others.
- One photo is not a verdict. A red tile means that part of the picture is worth another try.
- Google’s free tier for Gemini Decision 1 is marked “Used to improve our products: Yes” on the [pricing page](https://ai.google.dev/gemini-api/docs/pricing). The paid tier is marked “No”. Use the paid tier, or only photos you are willing to submit.
- The paid input price used here is $0.10 per 1,000,000 input tokens for text, image, video, audio, and PDF, with output not billed. That matches the pricing page as checked on 15 November 2026. Shared input is billed once per request; each question still adds its own instruction and criteria tokens. If Google changes the price, change `app/cost.py` and `data/config.json` together. The loader refuses a price other than 0.10 so the displayed formula cannot drift silently.
- This project was exercised in replay. It was not run against the live API here, because no `GEMINI_API_KEY` was available, and no latency or cost figure was filled in to stand in for that run.
