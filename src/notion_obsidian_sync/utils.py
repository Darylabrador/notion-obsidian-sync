"""Small shared helpers: rate limiting, retry/backoff, hashing."""

from __future__ import annotations

import hashlib
import logging
import random
import threading
import time
from collections.abc import Callable, Iterable
from typing import TypeVar

T = TypeVar("T")

logger = logging.getLogger("notion_obsidian_sync")


class RateLimiter:
    """Simple thread-safe rate limiter: at most `rate` calls per second."""

    def __init__(self, rate: float = 3.0) -> None:
        self._min_interval = 1.0 / rate
        self._lock = threading.Lock()
        self._last_call = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            sleep_for = self._min_interval - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._last_call = time.monotonic()


class RetryExhausted(Exception):
    """Raised when a retried operation still fails after all attempts."""


def retry_with_backoff(
    func: Callable[[], T],
    *,
    max_attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable: Callable[[BaseException], bool] = lambda exc: True,
    retry_after: Callable[[BaseException], float | None] = lambda exc: None,
    on_retry: Callable[[BaseException, int, float], None] | None = None,
) -> T:
    """Call `func()` with exponential backoff + jitter on retryable failures.

    `retry_after` lets the caller extract a server-provided `Retry-After`
    delay (e.g. from an HTTP 429) which takes precedence over the computed
    backoff delay.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return func()
        except BaseException as exc:  # noqa: BLE001 - deliberately broad, re-raised below
            if attempt >= max_attempts or not retryable(exc):
                raise RetryExhausted(f"Gave up after {attempt} attempts: {exc}") from exc
            delay = retry_after(exc)
            if delay is None:
                delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                delay += random.uniform(0, delay * 0.25)
            if on_retry:
                on_retry(exc, attempt, delay)
            time.sleep(delay)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def chunked(items: list[T], size: int) -> Iterable[list[T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
