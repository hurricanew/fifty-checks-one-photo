"""In-memory caps for live calls. Photos are not stored here."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable

HOUR_SECONDS = 60 * 60
DAY_SECONDS = 24 * HOUR_SECONDS

DEFAULT_PER_IP_PER_HOUR = 5
DEFAULT_PER_DAY = 200

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


class RateLimited(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.status_code = 429


class LiveCallLimiter:
    """Rolling windows. One hit is recorded only when the call is allowed."""

    def __init__(
        self,
        per_ip_per_hour: int,
        per_day: int,
        clock: Callable[[], float] | None = None,
    ):
        if isinstance(per_ip_per_hour, bool) or not isinstance(per_ip_per_hour, int):
            raise ValueError("per_ip_per_hour must be an int")
        if isinstance(per_day, bool) or not isinstance(per_day, int):
            raise ValueError("per_day must be an int")
        if per_ip_per_hour < 0 or per_day < 0:
            raise ValueError("live call limits must be >= 0")
        self.per_ip_per_hour = per_ip_per_hour
        self.per_day = per_day
        self._clock = clock or time.monotonic
        self._ip_hits: dict[str, list[float]] = {}
        self._day_hits: list[float] = []
        self._lock = threading.Lock()

    def allow(self, ip: str) -> None:
        now = self._clock()
        with self._lock:
            self._prune(now)
            ip_hits = self._ip_hits.get(ip, [])
            if len(ip_hits) >= self.per_ip_per_hour:
                raise RateLimited(
                    f"Too many live checks from this address. The limit is "
                    f"{self.per_ip_per_hour} live calls per IP per hour. "
                    "Replay mode still works. The photo was not stored."
                )
            if len(self._day_hits) >= self.per_day:
                raise RateLimited(
                    f"The shared daily cap of {self.per_day} live calls is used up "
                    "(rolling 24 hours). Replay mode still works. The photo was not stored."
                )
            ip_hits.append(now)
            self._ip_hits[ip] = ip_hits
            self._day_hits.append(now)

    def _prune(self, now: float) -> None:
        hour_cutoff = now - HOUR_SECONDS
        day_cutoff = now - DAY_SECONDS
        self._day_hits = [stamp for stamp in self._day_hits if stamp > day_cutoff]
        empty = []
        for ip, stamps in self._ip_hits.items():
            kept = [stamp for stamp in stamps if stamp > hour_cutoff]
            if kept:
                self._ip_hits[ip] = kept
            else:
                empty.append(ip)
        for ip in empty:
            del self._ip_hits[ip]


def limiter_from_env(clock: Callable[[], float] | None = None) -> LiveCallLimiter:
    return LiveCallLimiter(
        per_ip_per_hour=env_int("LIVE_CALLS_PER_IP_PER_HOUR", DEFAULT_PER_IP_PER_HOUR),
        per_day=env_int("LIVE_CALLS_PER_DAY", DEFAULT_PER_DAY),
        clock=clock,
    )


def trust_proxy_from_env() -> bool:
    return env_flag("TRUST_PROXY")


def env_flag(name: str) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return False
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"{name} must be true or false, not {raw!r}")


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, not {raw!r}") from exc
    if value < 0:
        raise ValueError(f"{name} must be >= 0, not {value}")
    return value


def client_ip(host: str | None, forwarded_for: str | None, trust_proxy: bool) -> str:
    """Use X-Forwarded-For only when the operator says a trusted proxy appended it.

    The rightmost address is the one that proxy added. The leftmost address is
    whatever the caller sent, so it is not used.
    """
    if trust_proxy and forwarded_for:
        parts = [part.strip().strip('"') for part in forwarded_for.split(",")]
        parts = [part for part in parts if part]
        if parts:
            return parts[-1]
    if host:
        return host
    return "unknown"
