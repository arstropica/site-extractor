"""Resource filter — determines which URLs to download based on category rules."""

import mimetypes
from urllib.parse import urlparse
from typing import Optional


MIME_TO_CATEGORY = {
    "text/html": "web_pages",
    "application/xhtml+xml": "web_pages",
    "image/jpeg": "images",
    "image/png": "images",
    "image/gif": "images",
    "image/webp": "images",
    "image/svg+xml": "images",
    "image/x-icon": "images",
    "image/bmp": "images",
    "image/tiff": "images",
    "video/mp4": "media",
    "video/x-msvideo": "media",
    "video/quicktime": "media",
    "video/x-ms-wmv": "media",
    "video/webm": "media",
    "video/x-matroska": "media",
    "audio/mpeg": "media",
    "audio/wav": "media",
    "audio/ogg": "media",
    "audio/flac": "media",
    "application/pdf": "documents",
    "application/msword": "documents",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "documents",
    "application/vnd.ms-excel": "documents",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "documents",
    "application/vnd.ms-powerpoint": "documents",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "documents",
    "text/plain": "documents",
    "text/csv": "documents",
    "application/rtf": "documents",
    "application/zip": "archives",
    "application/x-tar": "archives",
    "application/gzip": "archives",
    "application/x-rar-compressed": "archives",
    "application/x-7z-compressed": "archives",
    "application/json": "code",
    "application/xml": "code",
    "text/xml": "code",
    "text/css": "code",
    "application/javascript": "code",
    "text/javascript": "code",
}


class ResourceFilter:
    def __init__(self, filters: dict):
        self.filters = filters
        self._build_lookup()

    def _build_lookup(self):
        """Build extension-to-category and exclusion lookups."""
        self.ext_include = {}
        self.ext_exclude = set()
        self.enabled_categories = set()

        for cat_key, cat_config in self.filters.items():
            if not cat_config.get("enabled", False):
                continue
            self.enabled_categories.add(cat_key)

            mode = cat_config.get("mode", "include")
            extensions = cat_config.get("extensions", [])
            exclude_exts = cat_config.get("exclude_extensions", [])

            # Exclude takes priority
            for ext in exclude_exts:
                self.ext_exclude.add(ext.lower().lstrip("."))

            if mode == "include":
                for ext in extensions:
                    clean = ext.lower().lstrip(".")
                    if clean not in self.ext_exclude:
                        self.ext_include[clean] = cat_key

    def get_category(self, url: str, mime_type: Optional[str] = None) -> Optional[str]:
        """Determine the category for a URL. Returns None if the resource should be skipped.

        Priority:
          1. Path extension excludes → reject (user-configured definitive deny).
          2. Server-reported MIME → category (authoritative when present).
          3. Path extension includes → category (user-configured allow list).
          4. Stdlib mimetypes.guess_type → category (catches uncommon extensions).
          5. Reject.

        Step 2 wins over step 3 because the server's Content-Type reflects what
        is actually being served — extensions can be missing, lying, or hidden
        in the query string (image CDNs, Wayback combiner URLs, etc.).
        """
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else None

        # 1. Excludes win definitively
        if ext and ext in self.ext_exclude:
            return None

        # 2. MIME first (authoritative when we have it)
        if mime_type:
            clean_mime = mime_type.split(";")[0].strip().lower()
            cat = MIME_TO_CATEGORY.get(clean_mime)
            if cat and cat in self.enabled_categories:
                return cat

        # 3. User-configured extension include
        if ext and ext in self.ext_include:
            return self.ext_include[ext]

        # 4. Stdlib guess (broader extension → MIME map than MIME_TO_CATEGORY)
        guessed_mime, _ = mimetypes.guess_type(url)
        if guessed_mime:
            cat = MIME_TO_CATEGORY.get(guessed_mime)
            if cat and cat in self.enabled_categories:
                return cat

        # 5. No signal we can use
        return None

    def should_download(self, url: str, mime_type: Optional[str] = None) -> bool:
        """Check if a resource should be downloaded."""
        return self.get_category(url, mime_type) is not None

    def should_consider(self, url: str) -> bool:
        """Permissive prefilter: True unless the URL is *definitively* excluded.

        Use this before dispatching to a HEAD-probing downloader so that URLs
        without a recognizable path-level extension (image CDNs, Wayback
        combiner URLs, etc.) still get a chance to be categorized via the
        server's reported MIME. The downstream HEAD path calls
        get_category(url, mime_type) which is the authoritative gate.
        """
        if not self.enabled_categories:
            return False
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        if "." in path:
            ext = path.rsplit(".", 1)[-1].lower()
            if ext in self.ext_exclude:
                return False
        return True

    def is_page(self, url: str, mime_type: Optional[str] = None) -> bool:
        """Check if a URL points to a web page (for crawling)."""
        cat = self.get_category(url, mime_type)
        return cat == "web_pages"
