"""
Crawler engine — orchestrates HTTP and browser-based crawling.

Manages the crawl queue, depth tracking, domain filtering, rate limiting,
robots.txt compliance, and progress reporting via Redis pub/sub.
"""

import asyncio
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Set, Dict, Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from ..config import settings
from .resource_filter import ResourceFilter
from .page_storage import PageStorage

logger = logging.getLogger(__name__)


class CrawlState:
    """Tracks the state of a crawl for pause/resume support."""

    def __init__(self, job_id: str, config: dict):
        self.job_id = job_id
        self.config = config
        self.visited_urls: Set[str] = set()
        self.queued_urls: list = []  # (url, depth, parent_url)
        self.downloaded_hashes: Set[str] = set()
        # Pages = HTML documents; Resources = files (media, docs, etc.)
        self.pages_discovered = 0
        self.pages_downloaded = 0
        self.resources_discovered = 0
        self.resources_downloaded = 0
        self.bytes_downloaded = 0
        self.errors: list = []
        self.paused = False
        self.cancelled = False

    def save(self, job_dir: Path):
        """Persist crawl state to disk for resume support."""
        state_file = job_dir / "crawl_state.json"
        state_file.write_text(json.dumps({
            "visited_urls": list(self.visited_urls),
            "queued_urls": self.queued_urls,
            "downloaded_hashes": list(self.downloaded_hashes),
            "pages_discovered": self.pages_discovered,
            "pages_downloaded": self.pages_downloaded,
            "resources_discovered": self.resources_discovered,
            "resources_downloaded": self.resources_downloaded,
            "bytes_downloaded": self.bytes_downloaded,
        }, indent=2))

    @classmethod
    def load(cls, job_id: str, config: dict, job_dir: Path) -> "CrawlState":
        """Load persisted crawl state for resume."""
        state = cls(job_id, config)
        state_file = job_dir / "crawl_state.json"
        if state_file.exists():
            data = json.loads(state_file.read_text())
            state.visited_urls = set(data.get("visited_urls", []))
            state.queued_urls = data.get("queued_urls", [])
            state.downloaded_hashes = set(data.get("downloaded_hashes", []))
            state.pages_discovered = data.get("pages_discovered", 0)
            state.pages_downloaded = data.get("pages_downloaded", 0)
            state.resources_discovered = data.get("resources_discovered", 0)
            state.resources_downloaded = data.get("resources_downloaded", 0)
            state.bytes_downloaded = data.get("bytes_downloaded", 0)
        return state


class Crawler:
    """Main crawl engine supporting HTTP and browser modes."""

    def __init__(self, redis_client, db_conn=None):
        self.redis = redis_client
        self.db = db_conn
        self._robot_parsers: Dict[str, Optional[RobotFileParser]] = {}
        self._domain_semaphores: Dict[str, asyncio.Semaphore] = {}
        self._global_semaphore: Optional[asyncio.Semaphore] = None

    @staticmethod
    def _extract_freshness_meta(headers: Dict[str, Any], size: int = 0) -> dict:
        """Pull ETag, Last-Modified, Content-Length, Content-Type from response headers."""
        h = {k.lower(): v for k, v in headers.items()}
        cl_str = str(h.get("content-length", "")).strip()
        try:
            content_length = int(cl_str) if cl_str.isdigit() else size
        except (ValueError, TypeError):
            content_length = size
        return {
            "etag": h.get("etag"),
            "last_modified": h.get("last-modified"),
            "content_length": content_length,
            "content_type": h.get("content-type", ""),
        }

    @staticmethod
    def _is_fresh(sidecar: dict, head_headers: Dict[str, Any]) -> bool:
        """Return True if the on-disk file is byte-identical to what HEAD reports.

        Priority: ETag > Last-Modified > Content-Length. If none of these are
        comparable, return False (re-download to be safe).
        """
        if not sidecar:
            return False
        h = {k.lower(): v for k, v in head_headers.items()}

        s_etag, h_etag = sidecar.get("etag"), h.get("etag")
        if s_etag and h_etag:
            return s_etag == h_etag

        s_lm, h_lm = sidecar.get("last_modified"), h.get("last-modified")
        if s_lm and h_lm:
            return s_lm == h_lm

        s_cl = sidecar.get("content_length")
        h_cl_str = str(h.get("content-length", "")).strip()
        if s_cl and h_cl_str.isdigit():
            return int(s_cl) == int(h_cl_str)

        return False

    async def cleanup_for_fresh_run(self, job_id: str):
        """Wipe Redis transient state and on-disk crawl_state.json for a fresh re-run.

        Leaves saved pages/, assets/, results/ on disk — those are verified
        per-URL via HEAD freshness during the new crawl.
        """
        await self.redis.delete(
            f"scraper:pages:{job_id}",
            f"scraper:resources:{job_id}",
            f"scraper:result:{job_id}",
            f"scraper:signal:{job_id}",
        )
        state_file = Path(settings.DATA_DIR) / "jobs" / job_id / "crawl_state.json"
        if state_file.exists():
            try:
                state_file.unlink()
            except OSError as e:
                logger.warning(f"Failed to remove {state_file}: {e}")

    async def crawl(self, job_id: str, config: dict, resume: bool = False):
        """Execute a crawl job. When resume=False, the caller is expected to have
        invoked cleanup_for_fresh_run(); we still load_state() defensively in case
        crawl_state.json lingers, but with no visited_urls the seed loop fires."""
        job_dir = Path(settings.DATA_DIR) / "jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        # Check for existing state (resume)
        state = CrawlState.load(job_id, config, job_dir)

        # Initialize components
        resource_filter = ResourceFilter(config.get("resource_filters", {}))
        storage = PageStorage(str(job_dir))

        # Rate limiting
        max_per_domain = config.get("max_concurrent_per_domain", 2)
        max_total = config.get("max_concurrent_total", 10)
        self._global_semaphore = asyncio.Semaphore(max_total)
        delay_ms = config.get("request_delay_ms", 500)

        # Domain filtering
        domain_filter = config.get("domain_filter", {})
        allowed_domains = set(domain_filter.get("allowed_domains", []))
        path_filters = domain_filter.get("path_filters", [])

        # Max download size
        max_size = config.get("max_download_size") or settings.MAX_DOWNLOAD_SIZE
        depth_limit = config.get("depth_limit", 3)
        respect_robots = config.get("respect_robots", True)
        user_agent = config.get("user_agent") or settings.DEFAULT_USER_AGENT
        crawl_mode = config.get("crawl_mode", "http")
        # Retry limit: per-job override falls back to env default
        retry_limit = config.get("retry_limit")
        if retry_limit is None:
            retry_limit = settings.SCRAPER_RETRY_LIMIT

        # Seed the queue if fresh start
        if not state.queued_urls and not state.visited_urls:
            for url in config.get("seed_urls", []):
                state.queued_urls.append((url, 0, None))
                state.pages_discovered += 1

        # Set up HTTP client
        proxy_url = settings.HTTPS_PROXY or settings.HTTP_PROXY or None
        client_kwargs = {
            "timeout": httpx.Timeout(30.0),
            "follow_redirects": True,
            "headers": {"User-Agent": user_agent},
            "limits": httpx.Limits(max_connections=max_total, max_keepalive_connections=max_per_domain),
        }
        if proxy_url:
            client_kwargs["proxy"] = proxy_url

        # Auth setup
        auth_config = config.get("auth", {})
        auth_method = auth_config.get("method", "none")
        if auth_method == "basic":
            client_kwargs["auth"] = (auth_config.get("username", ""), auth_config.get("password", ""))
        elif auth_method == "bearer":
            client_kwargs["headers"]["Authorization"] = f"Bearer {auth_config.get('token', '')}"
        elif auth_method == "cookie":
            cookies = auth_config.get("cookies", {})
            client_kwargs["cookies"] = cookies

        browser = None
        browser_context = None

        try:
            if crawl_mode == "browser":
                from playwright.async_api import async_playwright
                pw = await async_playwright().start()
                browser = await pw.chromium.launch(headless=True)
                browser_context = await browser.new_context(
                    user_agent=user_agent,
                    ignore_https_errors=True,
                )
                # Apply cookies for browser mode
                if auth_method == "cookie" and auth_config.get("cookies"):
                    cookie_list = []
                    for name, value in auth_config["cookies"].items():
                        cookie_list.append({
                            "name": name, "value": value,
                            "domain": urlparse(config["seed_urls"][0]).netloc,
                            "path": "/",
                        })
                    await browser_context.add_cookies(cookie_list)
                elif auth_method == "browser_session" and auth_config.get("session_data"):
                    session = auth_config["session_data"]
                    if session.get("cookies"):
                        await browser_context.add_cookies(session["cookies"])

            async with httpx.AsyncClient(**client_kwargs) as client:
                # Stash per-crawl context so _process_one can read it (saves passing
                # 15+ args through every call).
                self._client = client
                self._browser_context = browser_context
                self._crawl_mode = crawl_mode
                self._job_id = job_id
                self._state = state
                self._storage = storage
                self._resource_filter = resource_filter
                self._depth_limit = depth_limit
                self._allowed_domains = allowed_domains
                self._path_filters = path_filters
                self._respect_robots = respect_robots
                self._user_agent = user_agent
                self._delay_ms = delay_ms
                self._max_size = max_size
                self._max_per_domain = max_per_domain
                self._max_total = max_total
                self._retry_limit = retry_limit
                self._state_lock = asyncio.Lock()

                # Sliding-window dispatcher: keep up to max_total tasks in flight,
                # refill as they complete. The queue may grow during execution
                # (link extraction discovers more URLs).
                in_flight: set[asyncio.Task] = set()

                while True:
                    # Drain pause/cancel signals from Redis
                    signal = await self.redis.getdel(f"scraper:signal:{job_id}")
                    if signal == "pause":
                        state.paused = True
                    elif signal == "cancel":
                        state.cancelled = True

                    if state.cancelled or state.paused:
                        break

                    # Check global download size limit
                    if state.bytes_downloaded >= max_size:
                        logger.info(f"Job {job_id}: Download limit reached ({state.bytes_downloaded} bytes)")
                        break

                    # Fill the in-flight pool from the queue
                    while state.queued_urls and len(in_flight) < max_total:
                        url, depth, parent_url = state.queued_urls.pop(0)
                        in_flight.add(asyncio.create_task(
                            self._process_one(url, depth, parent_url)
                        ))

                    # Nothing in flight and queue empty → all done
                    if not in_flight:
                        break

                    # Wait for at least one to complete (or timeout to re-check signals)
                    done, pending = await asyncio.wait(
                        in_flight,
                        return_when=asyncio.FIRST_COMPLETED,
                        timeout=2.0,
                    )
                    in_flight = pending
                    # Surface any unexpected exceptions for visibility
                    for task in done:
                        try:
                            task.result()
                        except Exception as e:
                            logger.warning(f"Worker task raised: {e}")

                # Drain any in-flight tasks gracefully (cancellation OR pause)
                if in_flight:
                    if state.cancelled:
                        for t in in_flight:
                            t.cancel()
                    await asyncio.gather(*in_flight, return_exceptions=True)

                # If paused, persist state and notify
                if state.paused:
                    state.save(job_dir)
                    await self._publish_event(job_id, "SCRAPE_STATUS", {"status": "paused"})
                    return

        finally:
            if browser:
                await browser.close()

        # Save final state
        state.save(job_dir)

        # Mark complete
        if not state.cancelled:
            completion_data = {
                "pages_discovered": state.pages_discovered,
                "pages_downloaded": state.pages_downloaded,
                "resources_discovered": state.resources_discovered,
                "resources_downloaded": state.resources_downloaded,
                "bytes_downloaded": state.bytes_downloaded,
            }
            await self.redis.hset(f"scraper:result:{job_id}", mapping={
                k: str(v) for k, v in completion_data.items()
            })
            await self._publish_event(job_id, "SCRAPE_STATUS", {
                "status": "scraped",
                **completion_data,
            })

    async def _process_one(self, url: str, depth: int, parent_url: Optional[str]):
        """Process a single queued URL: filter checks, fetch (page or asset),
        extract links, queue more, publish events. Designed to be safe under
        concurrent execution by N peer tasks via the sliding-window dispatcher.
        """
        state = self._state
        client = self._client
        resource_filter = self._resource_filter

        # Quick exit if cancellation kicked in after dispatch
        if state.cancelled or state.paused:
            return

        # Atomic visited check + insert (prevents duplicate work when the same
        # URL is discovered from multiple parent pages before either worker
        # has marked it visited).
        async with self._state_lock:
            if url in state.visited_urls:
                return
            state.visited_urls.add(url)

        # Filter checks (read-only, no need for lock)
        if depth > self._depth_limit:
            return
        if not self._is_allowed_domain(url, self._allowed_domains):
            return
        if self._path_filters and not self._matches_path_filter(url, self._path_filters):
            return
        if self._respect_robots and not await self._check_robots(url, self._user_agent, client):
            return

        domain = urlparse(url).netloc
        async with self._state_lock:
            if domain not in self._domain_semaphores:
                self._domain_semaphores[domain] = asyncio.Semaphore(self._max_per_domain)
        domain_sem = self._domain_semaphores[domain]

        # Resource branch: download as asset
        url_category = resource_filter.get_category(url)
        if url_category and url_category != "web_pages":
            async with self._global_semaphore:
                async with domain_sem:
                    await self._download_asset(
                        client, url, self._job_id, self._storage, state, url_category, resource_filter,
                    )
            await asyncio.sleep(self._delay_ms / 1000.0)
            return

        # Page branch: fetch HTML, save, extract links, dispatch asset downloads
        async with self._global_semaphore:
            async with domain_sem:
                try:
                    # Disk + HEAD freshness: if we have this page on disk and the
                    # remote ETag/Last-Modified/Content-Length matches, reuse the
                    # cached HTML instead of refetching.
                    cached_path = self._storage.page_local_path(url)
                    sidecar = self._storage.read_meta(cached_path) if cached_path.exists() else None
                    html = None
                    content_type = None
                    page_size = 0
                    response_headers: Dict[str, Any] = {}
                    served_from_cache = False
                    if sidecar:
                        try:
                            head = await client.head(
                                url, timeout=10.0, follow_redirects=True,
                            )
                            if head.status_code == 200 and self._is_fresh(sidecar, head.headers):
                                html = cached_path.read_text(encoding="utf-8")
                                content_type = sidecar.get("content_type") or head.headers.get(
                                    "content-type", "text/html",
                                )
                                page_size = cached_path.stat().st_size
                                response_headers = dict(head.headers)
                                served_from_cache = True
                        except Exception as e:
                            logger.debug(f"Freshness HEAD failed for {url}: {e}")

                    if html is None:
                        if self._crawl_mode == "browser" and self._browser_context:
                            html, content_type, page_size, response_headers = await self._fetch_browser(
                                self._browser_context, url, retry_limit=self._retry_limit,
                            )
                        else:
                            html, content_type, page_size, response_headers = await self._fetch_http(
                                client, url, retry_limit=self._retry_limit,
                            )
                        if html is None:
                            err = response_headers.get("_fetch_error") if response_headers else None
                            if err:
                                state.errors.append({"url": url, "error": err})
                                await self._publish_event(self._job_id, "SCRAPE_ERROR", {
                                    "url": url, "error": err,
                                })
                            return

                    soup = BeautifulSoup(html, "lxml")
                    title = soup.title.string.strip() if soup.title and soup.title.string else None

                    if served_from_cache:
                        # Refresh sidecar with the latest HEAD-confirmed metadata
                        # so subsequent runs can compare against current values.
                        meta = self._extract_freshness_meta(response_headers, page_size)
                        meta.update({
                            "url": url, "title": title,
                            "fetched_at": datetime.utcnow().isoformat(),
                            "from_cache": True,
                        })
                        self._storage.write_meta(cached_path, meta)
                        local_path = f"pages/{cached_path.name}"
                    else:
                        meta = self._extract_freshness_meta(response_headers, page_size)
                        meta.update({
                            "url": url, "title": title,
                            "fetched_at": datetime.utcnow().isoformat(),
                            "from_cache": False,
                        })
                        local_path = self._storage.save_page(url, html, meta=meta)
                        async with self._state_lock:
                            state.bytes_downloaded += page_size

                    async with self._state_lock:
                        state.pages_downloaded += 1

                    await self._publish_event(self._job_id, "PAGE_DOWNLOADED", {
                        "url": url, "status": 200, "size": page_size,
                        "content_type": content_type, "depth": depth, "title": title,
                        "from_cache": served_from_cache,
                    })

                    page_record = {
                        "id": str(uuid.uuid4()),
                        "job_id": self._job_id,
                        "url": url,
                        "local_path": local_path,
                        "status": "downloaded",
                        "content_type": content_type,
                        "size": page_size,
                        "depth": depth,
                        "parent_url": parent_url,
                        "title": title,
                    }
                    await self.redis.rpush(
                        f"scraper:pages:{self._job_id}", json.dumps(page_record),
                    )

                    # Extract & queue links discovered from this page
                    if depth < self._depth_limit:
                        links = self._extract_links(html, url)
                        for link in links:
                            async with self._state_lock:
                                if link in state.visited_urls:
                                    continue
                                if not self._is_allowed_domain(link, self._allowed_domains):
                                    continue
                                state.queued_urls.append((link, depth + 1, url))
                                link_cat = resource_filter.get_category(link)
                                is_resource = bool(link_cat and link_cat != "web_pages")
                                if is_resource:
                                    state.resources_discovered += 1
                                else:
                                    state.pages_discovered += 1
                            event_type = "RESOURCE_DISCOVERED" if is_resource else "PAGE_DISCOVERED"
                            await self._publish_event(self._job_id, event_type, {
                                "url": link, "depth": depth + 1,
                                "source_url": url, "category": link_cat,
                            })

                    # Asset downloads embedded in this page (img/link/source) —
                    # also parallelized within this URL's processing.
                    asset_urls = self._extract_assets(html, url)
                    asset_tasks = []
                    asset_sem = asyncio.Semaphore(self._max_total)
                    for asset_url in asset_urls:
                        if not resource_filter.should_download(asset_url):
                            continue
                        category = resource_filter.get_category(asset_url)

                        async def _bounded(u=asset_url, c=category):
                            async with asset_sem:
                                await self._download_asset(
                                    client, u, self._job_id, self._storage, state, c, resource_filter,
                                )
                        asset_tasks.append(asyncio.create_task(_bounded()))
                    if asset_tasks:
                        await asyncio.gather(*asset_tasks, return_exceptions=True)

                    # Progress event (read-only snapshot of counters)
                    pages_total = max(state.pages_discovered, state.pages_downloaded)
                    resources_total = max(state.resources_discovered, state.resources_downloaded)
                    await self._publish_event(self._job_id, "SCRAPE_PROGRESS", {
                        "pages_done": state.pages_downloaded,
                        "pages_total": pages_total,
                        "resources_done": state.resources_downloaded,
                        "resources_total": resources_total,
                        "estimated_total": pages_total + resources_total,
                        "bytes_downloaded": state.bytes_downloaded,
                        "max_bytes": self._max_size,
                    })

                except Exception as e:
                    logger.error(f"Error crawling {url}: {e}")
                    state.errors.append({"url": url, "error": str(e)})
                    await self._publish_event(self._job_id, "SCRAPE_ERROR", {
                        "url": url, "error": str(e),
                    })

        await asyncio.sleep(self._delay_ms / 1000.0)

    async def _fetch_with_retry(self, fetcher, url: str, retry_limit: int):
        """Run `fetcher()` with retry on transient failures (timeout, 5xx, network errors).
        Returns (html, content_type, size, headers) on success.
        On permanent failure, returns (None, None, 0, {"_fetch_error": "..."}) so
        callers can surface the cause as a SCRAPE_ERROR event instead of skipping
        silently. Successful non-HTML responses (skipped because of content-type)
        return (None, content_type, 0, headers) without _fetch_error.
        """
        attempts = max(1, retry_limit + 1)
        last_err = None
        for i in range(attempts):
            try:
                return await fetcher()
            except httpx.HTTPStatusError as e:
                # 5xx is retryable; 4xx is not (won't fix itself)
                if 500 <= e.response.status_code < 600 and i < attempts - 1:
                    last_err = e
                else:
                    msg = f"HTTP {e.response.status_code}"
                    logger.warning(f"Fetch failed for {url}: {msg}")
                    return None, None, 0, {"_fetch_error": msg}
            except (httpx.TimeoutException, httpx.NetworkError, asyncio.TimeoutError) as e:
                last_err = e
                if i >= attempts - 1:
                    msg = f"{type(e).__name__} after {attempts} attempts: {e}"
                    logger.warning(f"Fetch failed for {url}: {msg}")
                    return None, None, 0, {"_fetch_error": msg}
            except Exception as e:
                # Non-transient (parsing, browser-level) — don't retry
                msg = f"{type(e).__name__}: {e}"
                logger.warning(f"Fetch failed for {url}: {msg}")
                return None, None, 0, {"_fetch_error": msg}

            # Exponential backoff before retry
            await asyncio.sleep((settings.SCRAPER_RETRY_BACKOFF_MS / 1000.0) * (2 ** i))
            logger.info(f"Retrying {url} (attempt {i + 2}/{attempts}) after {last_err}")

        return None, None, 0, {"_fetch_error": str(last_err) if last_err else "unknown"}

    async def _fetch_http(self, client: httpx.AsyncClient, url: str, retry_limit: int = 0):
        """Fetch a page via HTTP. Returns (html, content_type, size, headers)."""
        async def do_fetch():
            resp = await client.get(url)
            resp.raise_for_status()  # so retry_with logic catches 5xx
            content_type = resp.headers.get("content-type", "")
            headers = dict(resp.headers)
            if "text/html" not in content_type and "xhtml" not in content_type:
                return None, content_type, 0, headers
            return resp.text, content_type, len(resp.content), headers
        return await self._fetch_with_retry(do_fetch, url, retry_limit)

    async def _fetch_browser(self, context, url: str, retry_limit: int = 0):
        """Fetch a page via Playwright browser. Returns (html, content_type, size, headers).

        Uses domcontentloaded as primary signal (won't hang on ad-heavy sites
        that never reach networkidle), then waits briefly for additional content.
        """
        async def do_fetch():
            page = await context.new_page()
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                if not resp:
                    return None, None, 0, {"_fetch_error": "Browser navigation returned no response"}
                try:
                    await page.wait_for_load_state("load", timeout=15000)
                except Exception:
                    pass
                headers = dict(resp.headers)
                content_type = resp.headers.get("content-type", "text/html")
                html = await page.content()
                return html, content_type, len(html.encode()), headers
            finally:
                await page.close()
        return await self._fetch_with_retry(do_fetch, url, retry_limit)

    async def _download_asset(self, client, url, job_id, storage, state, category, resource_filter):
        """HEAD first → check size + verify category from server MIME → stream GET with abort guard.

        - HEAD: cheap probe of Content-Type and Content-Length. Re-categorize
          based on server-reported MIME (more accurate than extension guessing).
          Skip entirely if file exceeds MAX_ASSET_SIZE.
        - Disk + freshness: if the asset already exists locally and HEAD
          confirms the remote is unchanged (ETag/Last-Modified/Content-Length),
          reuse the on-disk copy and skip the GET.
        - GET: streaming download with chunk-level size enforcement so we don't
          accumulate megabytes of memory if the server lies about Content-Length.
        - Some servers don't support HEAD (405). Fall back to streaming GET in
          that case; the size guard still applies.
        """
        max_asset = settings.MAX_ASSET_SIZE
        skip_size_check = False
        head_headers: Dict[str, Any] = {}

        try:
            # ── HEAD probe ────────────────────────────────────────────────
            try:
                head = await client.head(url, timeout=10.0, follow_redirects=True)
                if head.status_code == 405:
                    # Server doesn't allow HEAD; we'll guard during stream
                    skip_size_check = True
                elif head.status_code != 200:
                    return
                else:
                    head_headers = dict(head.headers)
                    head_ct = head.headers.get("content-type", "")
                    head_len_str = head.headers.get("content-length", "")
                    head_len = int(head_len_str) if head_len_str.isdigit() else 0

                    # Re-verify category using server-reported MIME
                    actual_cat = resource_filter.get_category(url, head_ct)
                    if not actual_cat:
                        return  # filter excludes this file
                    category = actual_cat

                    if head_len > max_asset:
                        logger.info(
                            f"Skipping {url}: declared size {head_len} > MAX_ASSET_SIZE {max_asset}"
                        )
                        await self._publish_event(job_id, "SCRAPE_ERROR", {
                            "url": url,
                            "error": f"Skipped — file too large ({head_len} bytes)",
                        })
                        return

                    # Disk freshness: if local copy exists and HEAD says nothing
                    # has changed, reuse it without re-downloading.
                    cached_asset = storage.asset_local_path(url)
                    sidecar = storage.read_meta(cached_asset) if cached_asset.exists() else None
                    if sidecar and self._is_fresh(sidecar, head_headers):
                        size = cached_asset.stat().st_size
                        content_hash = sidecar.get("content_hash")
                        if not content_hash:
                            content_hash = storage.content_hash(cached_asset.read_bytes())
                        if content_hash in state.downloaded_hashes:
                            return
                        state.downloaded_hashes.add(content_hash)
                        # Refresh sidecar's HEAD-confirmed metadata
                        refreshed = self._extract_freshness_meta(head_headers, size)
                        refreshed.update({
                            "url": url, "category": category,
                            "content_hash": content_hash,
                            "fetched_at": datetime.utcnow().isoformat(),
                            "from_cache": True,
                        })
                        storage.write_meta(cached_asset, refreshed)
                        async with self._state_lock:
                            state.resources_downloaded += 1
                        resource_record = {
                            "id": str(uuid.uuid4()),
                            "job_id": job_id,
                            "url": url,
                            "local_path": f"assets/{cached_asset.name}",
                            "filename": Path(urlparse(url).path).name or "unknown",
                            "category": category,
                            "size": size,
                            "mime_type": head_ct,
                            "content_hash": content_hash,
                        }
                        await self.redis.rpush(
                            f"scraper:resources:{job_id}",
                            json.dumps(resource_record),
                        )
                        await self._publish_event(job_id, "RESOURCE_DOWNLOADED", {
                            "filename": resource_record["filename"],
                            "category": category,
                            "size": size,
                            "from_cache": True,
                        })
                        return
            except httpx.TimeoutException:
                # HEAD timed out; try streaming GET anyway
                skip_size_check = True
            except Exception as e:
                logger.debug(f"HEAD failed for {url}: {e}; falling back to GET")
                skip_size_check = True

            # ── Streaming GET with size guard ─────────────────────────────
            async with client.stream("GET", url, follow_redirects=True) as resp:
                if resp.status_code != 200:
                    return

                resp_ct = resp.headers.get("content-type", "")
                resp_len_str = resp.headers.get("content-length", "")
                resp_len = int(resp_len_str) if resp_len_str.isdigit() else 0

                if not skip_size_check and resp_len > max_asset:
                    logger.info(f"Skipping {url}: GET-declared size {resp_len} > MAX_ASSET_SIZE")
                    return

                # Re-verify category if HEAD was skipped
                if skip_size_check:
                    actual_cat = resource_filter.get_category(url, resp_ct)
                    if not actual_cat:
                        return
                    category = actual_cat

                chunks = []
                total = 0
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    total += len(chunk)
                    if total > max_asset:
                        logger.info(f"Aborting {url}: streamed bytes exceeded MAX_ASSET_SIZE")
                        return
                    chunks.append(chunk)

                content = b"".join(chunks)
                final_ct = resp_ct

            content_hash = storage.content_hash(content)

            # Dedup check (atomic via asyncio's single-thread guarantee)
            if content_hash in state.downloaded_hashes:
                return
            state.downloaded_hashes.add(content_hash)

            # Persist the freshness sidecar alongside the asset, preferring
            # HEAD headers (more reliable for streaming responses) and falling
            # back to GET response headers when HEAD was skipped.
            meta_source = head_headers or dict(resp.headers)
            meta = self._extract_freshness_meta(meta_source, len(content))
            meta.update({
                "url": url, "category": category,
                "content_hash": content_hash,
                "fetched_at": datetime.utcnow().isoformat(),
                "from_cache": False,
            })
            local_path, _ = storage.save_asset(url, content, meta=meta)
            state.bytes_downloaded += len(content)
            state.resources_downloaded += 1

            resource_record = {
                "id": str(uuid.uuid4()),
                "job_id": job_id,
                "url": url,
                "local_path": local_path,
                "filename": Path(urlparse(url).path).name or "unknown",
                "category": category,
                "size": len(content),
                "mime_type": final_ct,
                "content_hash": content_hash,
            }
            await self.redis.rpush(
                f"scraper:resources:{job_id}",
                json.dumps(resource_record),
            )
            await self._publish_event(job_id, "RESOURCE_DOWNLOADED", {
                "filename": resource_record["filename"],
                "category": category,
                "size": len(content),
                "from_cache": False,
            })

        except Exception as e:
            logger.warning(f"Asset download failed for {url}: {e}")

    def _extract_links(self, html: str, base_url: str) -> list:
        """Extract all links from HTML content."""
        soup = BeautifulSoup(html, "lxml")
        links = []
        for tag in soup.find_all("a", href=True):
            href = tag["href"]
            abs_url = urljoin(base_url, href)
            # Strip fragment
            abs_url = abs_url.split("#")[0]
            if abs_url and abs_url.startswith(("http://", "https://")):
                links.append(abs_url)
        return list(set(links))

    def _extract_assets(self, html: str, base_url: str) -> list:
        """Extract asset URLs (images, CSS, etc.) from HTML.

        Prefers `data-original-*` attributes when present, since cached pages
        read back from disk have their src/href rewritten to local paths.
        """
        soup = BeautifulSoup(html, "lxml")
        assets = set()

        for img in soup.find_all("img"):
            src = img.get("data-original-src") or img.get("src")
            if src:
                assets.add(urljoin(base_url, src))
        for link in soup.find_all("link"):
            rel = link.get("rel")
            if rel and "stylesheet" in rel:
                href = link.get("data-original-href") or link.get("href")
                if href:
                    assets.add(urljoin(base_url, href))
        for source in soup.find_all("source", src=True):
            assets.add(urljoin(base_url, source["src"]))

        return [a for a in assets if a.startswith(("http://", "https://"))]

    def _is_allowed_domain(self, url: str, allowed_domains: set) -> bool:
        """Check if a URL's domain is in the allowlist (supports wildcards)."""
        if not allowed_domains:
            return True
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        for allowed in allowed_domains:
            allowed = allowed.lower()
            if allowed.startswith("*."):
                suffix = allowed[2:]
                if domain == suffix or domain.endswith("." + suffix):
                    return True
            elif domain == allowed:
                return True
        return False

    def _matches_path_filter(self, url: str, path_filters: list) -> bool:
        """Check if a URL matches any path filter."""
        if not path_filters:
            return True
        parsed = urlparse(url)
        path = parsed.path
        for pattern in path_filters:
            regex = pattern.replace("*", ".*")
            if re.match(regex, path):
                return True
        return False

    async def _check_robots(self, url: str, user_agent: str, client: httpx.AsyncClient) -> bool:
        """Check robots.txt for the given URL."""
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

        if robots_url not in self._robot_parsers:
            try:
                resp = await client.get(robots_url, timeout=10.0)
                if resp.status_code == 200:
                    rp = RobotFileParser()
                    rp.parse(resp.text.splitlines())
                    self._robot_parsers[robots_url] = rp
                else:
                    self._robot_parsers[robots_url] = None
            except Exception:
                self._robot_parsers[robots_url] = None

        parser = self._robot_parsers.get(robots_url)
        if parser is None:
            return True  # No robots.txt or failed to fetch — allow
        return parser.can_fetch(user_agent, url)

    async def _publish_event(self, job_id: str, event_type: str, data: dict):
        """Publish a crawl event to Redis pub/sub."""
        event = {
            "type": event_type,
            "job_id": job_id,
            "data": data,
            "timestamp": datetime.utcnow().isoformat(),
        }
        await self.redis.publish("scraper_events", json.dumps(event))
