"""Download Notion-hosted images/files into the vault so links survive after
Notion's temporary signed URLs expire.

Assets are only ever written under `<sync_root>/_assets/`, enforced via
`paths.resolve_within`.
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

from .paths import atomic_write_bytes, resolve_within
from .utils import RetryExhausted, retry_with_backoff

logger = logging.getLogger("notion_obsidian_sync")

_DEFAULT_EXT = ".bin"


class AssetManager:
    def __init__(self, assets_dir: Path, timeout: float = 60.0) -> None:
        self._assets_dir = assets_dir
        self._session = requests.Session()
        self._timeout = timeout

    def download(self, page_id: str, block_id: str, url: str) -> str | None:
        """Download `url` into `_assets/<page_id>/<block_id><ext>` and return the
        path relative to the sync root (POSIX separators, for use in `![[...]]`
        Obsidian embeds), or None if the download ultimately failed.
        """
        ext = _guess_extension(url)
        subdir = resolve_within(self._assets_dir.parent, Path("_assets") / _short(page_id))
        target = resolve_within(self._assets_dir.parent, subdir / f"{_short(block_id)}{ext}")

        response_holder: dict[str, requests.Response] = {}

        def do_download() -> bytes:
            resp = self._session.get(url, timeout=self._timeout)
            resp.raise_for_status()
            response_holder["resp"] = resp
            return resp.content

        def is_retryable(exc: BaseException) -> bool:
            if isinstance(exc, requests.exceptions.HTTPError):
                status = exc.response.status_code if exc.response is not None else 0
                return status == 429 or status >= 500
            return isinstance(exc, requests.exceptions.RequestException)

        try:
            content = retry_with_backoff(
                do_download,
                max_attempts=4,
                base_delay=1.0,
                max_delay=15.0,
                retryable=is_retryable,
                on_retry=lambda exc, attempt, delay: logger.warning(
                    "Retrying asset download (%s) after error: %s [attempt %d]",
                    block_id,
                    exc,
                    attempt,
                ),
            )
        except RetryExhausted as exc:
            logger.warning("Failed to download asset for block %s: %s", block_id, exc)
            return None

        # Refine the extension from the real Content-Type if the URL had none.
        content_type = response_holder["resp"].headers.get("Content-Type")
        if ext == _DEFAULT_EXT and content_type:
            guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
            if guessed:
                target = target.with_suffix(guessed)

        atomic_write_bytes(target, content)
        return target.relative_to(self._assets_dir.parent).as_posix()


def _short(notion_id: str) -> str:
    return notion_id.replace("-", "")[:32]


def _guess_extension(url: str) -> str:
    path = unquote(urlparse(url).path)
    suffix = Path(path).suffix
    if suffix and len(suffix) <= 6:
        return suffix
    return _DEFAULT_EXT
