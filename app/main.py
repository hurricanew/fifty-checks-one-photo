"""HTTP entrypoint. Photos are read into memory and then discarded."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.checks import load_checks
from app.config import load_settings
from app.labels import SAMPLE_LABEL
from app.limits import RateLimited, client_ip, limiter_from_env, trust_proxy_from_env
from app.sample_data import load_sample, replay_hint
from app.service import CallFailed, MissingApiKey, api_key_from_env, evaluate
from app.uploads import UploadError, parse_upload, validate_image

INDEX_PATH = Path(__file__).resolve().parent / "static" / "index.html"
BODY_FRAMING_BYTES = 65_536


def create_app() -> FastAPI:
    settings = load_settings()
    checks = load_checks(settings.checks_path)
    sample = load_sample(settings.sample_path)
    app = FastAPI(title="50 checks on one photo")
    app.state.settings = settings
    app.state.checks = checks
    app.state.sample = sample
    app.state.limiter = limiter_from_env()
    app.state.trust_proxy = trust_proxy_from_env()

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(INDEX_PATH.read_text(encoding="utf-8"))

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/api/meta")
    def meta() -> JSONResponse:
        current = app.state.settings
        return JSONResponse(
            {
                "title": "50 checks on one photo",
                "checks": len(app.state.checks),
                "model": current.model,
                "api_version": current.api_version,
                "image_resolution": current.image_resolution,
                "fallback_model": current.fallback_model,
                "fallback_enabled": current.fallback_enabled,
                "live_key_set": bool(api_key_from_env()),
                "max_upload_bytes": current.max_upload_bytes,
                "allowed_mime_types": list(current.allowed_mime_types),
                "thresholds": {
                    "confidence_min": current.thresholds.confidence_min,
                    "uncertain_low": current.thresholds.uncertain_low,
                    "uncertain_high": current.thresholds.uncertain_high,
                },
                "sample_label": SAMPLE_LABEL,
                "replay": {
                    "label": app.state.sample.label,
                    "recorded": app.state.sample.kind == "recorded",
                    "photo_name": app.state.sample.photo_name,
                    "latency_ms": app.state.sample.latency_ms,
                    "input_tokens": app.state.sample.input_tokens,
                    "output_tokens": app.state.sample.output_tokens,
                    "cost_usd": app.state.sample.cost_usd,
                    "hint": replay_hint(app.state.sample),
                },
                "cost_formula": "input_tokens * 0.10 / 1000000",
                "live_calls_per_ip_per_hour": app.state.limiter.per_ip_per_hour,
                "live_calls_per_day": app.state.limiter.per_day,
                "trust_proxy": app.state.trust_proxy,
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/check")
    async def check(request: Request) -> JSONResponse:
        current = request.app.state.settings
        body_cap = current.max_upload_bytes + BODY_FRAMING_BYTES
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                declared = int(content_length)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Content-Length is not a number.") from exc
            if declared > body_cap:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"The request body exceeds the {body_cap} byte cap. "
                        "The photo was not stored."
                    ),
                )
        body = await _read_limited(request, body_cap)
        try:
            data, mime, mode, fallback = parse_upload(
                request.headers.get("content-type", ""),
                body,
                current.max_upload_bytes,
            )
            mime = validate_image(data, mime, current.allowed_mime_types, current.max_upload_bytes)
            if mode == "live":
                request.app.state.limiter.allow(
                    client_ip(
                        request.client.host if request.client else None,
                        request.headers.get("x-forwarded-for"),
                        request.app.state.trust_proxy,
                    )
                )
            payload = await asyncio.to_thread(
                evaluate,
                mode=mode,
                image=data,
                mime_type=mime,
                checks=request.app.state.checks,
                settings=current,
                sample_answers=request.app.state.sample,
                fallback_requested=fallback,
            )
        except UploadError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        except RateLimited as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        except MissingApiKey as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except CallFailed as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    return app


async def _read_limited(request: Request, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=413,
                detail=f"The request body exceeds the {limit} byte cap. The photo was not stored.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


app = create_app()
