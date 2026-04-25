"""Page storage — saves scraped HTML with rewritten asset URLs."""

import hashlib
import os
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse, quote
from typing import Optional

from bs4 import BeautifulSoup


class PageStorage:
    def __init__(self, job_dir: str):
        self.job_dir = Path(job_dir)
        self.pages_dir = self.job_dir / "pages"
        self.assets_dir = self.job_dir / "assets"
        self.pages_dir.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)

    def url_to_filename(self, url: str) -> str:
        """Convert a URL to a safe local filename."""
        parsed = urlparse(url)
        path = parsed.path.strip("/") or "index"
        # Replace path separators with underscores
        safe = re.sub(r'[^\w\-.]', '_', f"{parsed.netloc}_{path}")
        if parsed.query:
            query_hash = hashlib.md5(parsed.query.encode()).hexdigest()[:8]
            safe += f"_{query_hash}"
        # Ensure html extension for pages
        if not safe.endswith((".html", ".htm")):
            safe += ".html"
        return safe

    def asset_filename(self, url: str) -> str:
        """Convert an asset URL to a safe local filename."""
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        if not path:
            path = hashlib.md5(url.encode()).hexdigest()[:12]
        # Keep the original extension
        ext = ""
        if "." in path.split("/")[-1]:
            ext = "." + path.rsplit(".", 1)[-1]
        name_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        base = re.sub(r'[^\w\-.]', '_', path.rsplit("/", 1)[-1] if "/" in path else path)
        # Truncate to avoid overly long filenames
        if len(base) > 80:
            base = base[:80]
        return f"{name_hash}_{base}"

    def save_page(self, url: str, html: str, base_url: str = None) -> str:
        """Save HTML content with rewritten asset URLs. Returns local path relative to job_dir."""
        filename = self.url_to_filename(url)
        local_path = self.pages_dir / filename

        # Rewrite asset references to point to local copies
        soup = BeautifulSoup(html, "lxml")
        effective_base = base_url or url

        # Rewrite img src
        for tag in soup.find_all("img"):
            src = tag.get("src")
            if src:
                abs_url = urljoin(effective_base, src)
                local_asset = self.asset_filename(abs_url)
                tag["src"] = f"../assets/{quote(local_asset)}"
                tag["data-original-src"] = abs_url

        # Rewrite link href (CSS)
        for tag in soup.find_all("link", rel="stylesheet"):
            href = tag.get("href")
            if href:
                abs_url = urljoin(effective_base, href)
                local_asset = self.asset_filename(abs_url)
                tag["href"] = f"../assets/{quote(local_asset)}"
                tag["data-original-href"] = abs_url

        # Rewrite background images in style attributes
        for tag in soup.find_all(style=True):
            style = tag["style"]
            urls = re.findall(r'url\(["\']?(.*?)["\']?\)', style)
            for u in urls:
                abs_url = urljoin(effective_base, u)
                local_asset = self.asset_filename(abs_url)
                style = style.replace(u, f"../assets/{quote(local_asset)}")
            tag["style"] = style

        # Remove scripts (not needed for extraction)
        for script in soup.find_all("script"):
            script.decompose()

        # Write processed HTML
        local_path.write_text(str(soup), encoding="utf-8")
        return f"pages/{filename}"

    def save_asset(self, url: str, content: bytes) -> tuple[str, str]:
        """Save an asset file. Returns (local_path relative to job_dir, content_hash)."""
        filename = self.asset_filename(url)
        local_path = self.assets_dir / filename

        content_hash = hashlib.sha256(content).hexdigest()

        local_path.write_bytes(content)
        return f"assets/{filename}", content_hash

    def content_hash(self, content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()
