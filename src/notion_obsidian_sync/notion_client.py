"""Minimal, read-only Notion API client.

SECURITY / DATA-SAFETY RULE: this module must never issue a request that
mutates Notion content. Only the following read operations are implemented:

    GET  /v1/users/me
    GET  /v1/pages/{id}
    GET  /v1/pages/{id}/properties/{property_id}   (paginated property values)
    GET  /v1/blocks/{id}
    GET  /v1/blocks/{id}/children                  (paginated)
    GET  /v1/databases/{id}
    GET  /v1/data_sources/{id}
    POST /v1/data_sources/{id}/query                (read-only query, not a write)
    POST /v1/search                                 (read-only search, not a write)

No PATCH/PUT/DELETE calls exist anywhere in this client, and no POST call
targets a content-mutating endpoint. Do not add one without updating this
comment and the project README's "Security" section.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from .utils import RateLimiter, retry_with_backoff

logger = logging.getLogger("notion_obsidian_sync")

BASE_URL = "https://api.notion.com/v1"
PAGE_SIZE = 100


class NotionAPIError(Exception):
    def __init__(self, status_code: int, message: str, code: str | None = None) -> None:
        super().__init__(f"Notion API error {status_code} ({code}): {message}")
        self.status_code = status_code
        self.code = code


class NotionClient:
    def __init__(
        self,
        token: str,
        api_version: str,
        rate_limiter: RateLimiter | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Notion-Version": api_version,
                "Content-Type": "application/json",
            }
        )
        self._rate_limiter = rate_limiter or RateLimiter(rate=3.0)
        self._timeout = timeout

    # -- low-level request plumbing ------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{BASE_URL}{path}"

        def do_request() -> dict[str, Any]:
            self._rate_limiter.wait()
            resp = self._session.request(method, url, timeout=self._timeout, **kwargs)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise _RetryableHTTPError(resp)
            if resp.status_code >= 400:
                body = _safe_json(resp)
                raise NotionAPIError(
                    resp.status_code,
                    body.get("message", resp.text[:500]),
                    code=body.get("code"),
                )
            return _safe_json(resp)

        def is_retryable(exc: BaseException) -> bool:
            return isinstance(exc, (_RetryableHTTPError, requests.exceptions.RequestException))

        def retry_after(exc: BaseException) -> float | None:
            if isinstance(exc, _RetryableHTTPError):
                header = exc.response.headers.get("Retry-After")
                if header:
                    try:
                        return float(header)
                    except ValueError:
                        return None
            return None

        def on_retry(exc: BaseException, attempt: int, delay: float) -> None:
            logger.warning(
                "Retrying Notion API call (%s %s) after error: %s "
                "[attempt %d, sleeping %.1fs]",
                method,
                path,
                exc,
                attempt,
                delay,
            )

        try:
            return retry_with_backoff(
                do_request,
                max_attempts=6,
                base_delay=1.0,
                max_delay=30.0,
                retryable=is_retryable,
                retry_after=retry_after,
                on_retry=on_retry,
            )
        except _RetryableHTTPError as exc:
            body = _safe_json(exc.response)
            raise NotionAPIError(
                exc.response.status_code,
                body.get("message", exc.response.text[:500]),
                code=body.get("code"),
            ) from exc

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("GET", path, params=params)

    def _post(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("POST", path, json=json or {})

    def _paginate(
        self, fetch_page: Any, start_cursor: str | None = None
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        cursor = start_cursor
        while True:
            page = fetch_page(cursor)
            results.extend(page.get("results", []))
            if not page.get("has_more"):
                break
            cursor = page.get("next_cursor")
            if not cursor:
                break
        return results

    # -- public read-only API --------------------------------------------------

    def whoami(self) -> dict[str, Any]:
        return self._get("/users/me")

    def get_page(self, page_id: str) -> dict[str, Any]:
        return self._get(f"/pages/{page_id}")

    def get_page_property(self, page_id: str, property_id: str) -> list[dict[str, Any]]:
        """Resolve a paginated page property (e.g. relation/people with >25 items)."""

        def fetch_page(cursor: str | None) -> dict[str, Any]:
            params: dict[str, Any] = {"page_size": PAGE_SIZE}
            if cursor:
                params["start_cursor"] = cursor
            return self._get(f"/pages/{page_id}/properties/{property_id}", params=params)

        return self._paginate(fetch_page)

    def get_block(self, block_id: str) -> dict[str, Any]:
        return self._get(f"/blocks/{block_id}")

    def get_block_children(self, block_id: str) -> list[dict[str, Any]]:
        def fetch_page(cursor: str | None) -> dict[str, Any]:
            params: dict[str, Any] = {"page_size": PAGE_SIZE}
            if cursor:
                params["start_cursor"] = cursor
            return self._get(f"/blocks/{block_id}/children", params=params)

        return self._paginate(fetch_page)

    def get_database(self, database_id: str) -> dict[str, Any]:
        return self._get(f"/databases/{database_id}")

    def get_data_source(self, data_source_id: str) -> dict[str, Any]:
        return self._get(f"/data_sources/{data_source_id}")

    def query_data_source(
        self, data_source_id: str, filter_: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        def fetch_page(cursor: str | None) -> dict[str, Any]:
            body: dict[str, Any] = {"page_size": PAGE_SIZE}
            if filter_:
                body["filter"] = filter_
            if cursor:
                body["start_cursor"] = cursor
            return self._post(f"/data_sources/{data_source_id}/query", json=body)

        return self._paginate(fetch_page)

    def search(
        self, query: str = "", filter_: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        def fetch_page(cursor: str | None) -> dict[str, Any]:
            body: dict[str, Any] = {"page_size": PAGE_SIZE}
            if query:
                body["query"] = query
            if filter_:
                body["filter"] = filter_
            if cursor:
                body["start_cursor"] = cursor
            return self._post("/search", json=body)

        return self._paginate(fetch_page)

    def resolve_data_source_ids(self, database_id: str) -> list[str]:
        """A database may contain multiple data sources (2025-09-03 model)."""
        db = self.get_database(database_id)
        sources = db.get("data_sources", [])
        return [s["id"] for s in sources]


class _RetryableHTTPError(Exception):
    def __init__(self, response: requests.Response) -> None:
        super().__init__(f"HTTP {response.status_code}")
        self.response = response


def _safe_json(resp: requests.Response) -> dict[str, Any]:
    try:
        return resp.json()
    except ValueError:
        return {}
