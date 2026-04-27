"""HTTP client for the extractor-gateway's internal write endpoints.

The scraper is an HTTP producer — it never touches the database
directly. Pages, resources, and final-state updates all flow through
the gateway's /api/internal/* endpoints.

Retry-with-backoff on every call so transient gateway hiccups don't
lose records: this is where at-least-once delivery is enforced now
that there's no Redis queue sitting between scraper and DB.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


class GatewayClient:
    def __init__(self, base_url: Optional[str] = None, timeout: float = 15.0):
        self.base_url = (base_url or settings.GATEWAY_URL).rstrip("/")
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        # Lazy init so we only open the pool when first used (and so the
        # client lives in the same event loop that uses it).
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _post_with_retry(
        self,
        path: str,
        json_body: dict,
        max_attempts: int = 4,
    ) -> bool:
        """POST with exponential backoff. Returns True on success.

        4 attempts × 0.5s/1s/2s backoffs gives ~3.5s of total retry time
        before giving up — long enough to ride out a gateway restart on
        a small VM, short enough that a real outage surfaces quickly.
        """
        client = await self._get_client()
        url = f"{self.base_url}{path}"
        last_err: Optional[Exception] = None
        for attempt in range(max_attempts):
            try:
                resp = await client.post(url, json=json_body)
                resp.raise_for_status()
                return True
            except (httpx.HTTPError, httpx.TimeoutException, asyncio.TimeoutError) as e:
                last_err = e
                # When the gateway returns a non-2xx, surface the actual
                # response body + Allow header so we can tell a real
                # FastAPI 405 (body {"detail":"Method Not Allowed"}) from
                # a transport-level oddity (stale keep-alive bytes,
                # intermediate proxy, etc.).
                if isinstance(e, httpx.HTTPStatusError):
                    try:
                        body_snip = e.response.text[:300]
                        allow = e.response.headers.get("allow", "<absent>")
                        server = e.response.headers.get("server", "<absent>")
                        logger.warning(
                            f"POST {path} -> {e.response.status_code} "
                            f"server={server} allow={allow} body={body_snip!r}"
                        )
                    except Exception:
                        pass
                if attempt < max_attempts - 1:
                    backoff = 0.5 * (2 ** attempt)
                    logger.warning(
                        f"POST {path} failed ({type(e).__name__}); "
                        f"retry {attempt + 2}/{max_attempts} in {backoff}s"
                    )
                    await asyncio.sleep(backoff)
        logger.error(
            f"POST {path} failed after {max_attempts} attempts: "
            f"{type(last_err).__name__ if last_err else 'unknown'}: {last_err}"
        )
        return False

    async def add_pages(self, pages: List[Dict[str, Any]]) -> bool:
        if not pages:
            return True
        return await self._post_with_retry("/api/internal/pages", {"pages": pages})

    async def add_resources(self, resources: List[Dict[str, Any]]) -> bool:
        if not resources:
            return True
        return await self._post_with_retry(
            "/api/internal/resources", {"resources": resources}
        )

    async def mark_scraped(self, job_id: str, summary: Dict[str, Any]) -> bool:
        return await self._post_with_retry(
            f"/api/internal/jobs/{job_id}/scraped", summary
        )
