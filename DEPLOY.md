# Deploy

One container. Photos are read into memory and are not written to disk. The process listens on `$PORT` (8080 if you do not set it).

## Build

```bash
docker build -t rack-checks .
docker run --rm -p 8080:8080 \
  -e PORT=8080 \
  -e TRUST_PROXY=1 \
  -e GEMINI_API_KEY \
  rack-checks
```

`GEMINI_API_KEY` must be a host secret. Do not put it in the Dockerfile, a build arg, a committed `.env`, or the image. The image has no key in it.

On a host that injects the variable itself:

- **Cloud Run.** Cloud Run sets `PORT`. Put the key in Secret Manager and deploy with `--set-secrets GEMINI_API_KEY=gemini-api-key:latest` (or the console equivalent). Set `TRUST_PROXY=1`.
- **Render.** Add `GEMINI_API_KEY` as a secret environment variable. Render sets `PORT`. Set `TRUST_PROXY=1`.
- **Fly.** `fly secrets set GEMINI_API_KEY=...` and set `TRUST_PROXY=1` in `fly.toml` `[env]`. Fly sets `PORT`.

## Who the rate limit sees

Live calls are limited to `LIVE_CALLS_PER_IP_PER_HOUR` (default 5) per address per rolling hour, and `LIVE_CALLS_PER_DAY` (default 200) across everyone per rolling 24 hours. Replay is not counted. Over the limit, live mode returns HTTP 429 and does not call Gemini.

`X-Forwarded-For` is ignored unless `TRUST_PROXY=1`. Set that only when the app is reachable solely through the host's proxy. The address used is the rightmost entry, which is the one that proxy appended. The leftmost entry is whatever the caller sent, so it is not used. Leave `TRUST_PROXY` unset if you run the container so that clients connect to it directly. Otherwise a caller can put any address in the header.

The counters live in this process. They reset when the process restarts and they are not shared across instances. Use one instance if the daily cap has to be exact.

## What the container does not do

It does not store uploads. It does not ship latency, token, or cost measurements. Those come from `scripts/measure.py` on a machine that has the key, which is a separate step from hosting the try-it page.
