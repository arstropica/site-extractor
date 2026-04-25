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
        """Determine the category for a URL. Returns None if the resource should be skipped."""
        # Try extension first
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        if "." in path:
            ext = path.rsplit(".", 1)[-1].lower()
            if ext in self.ext_exclude:
                return None
            if ext in self.ext_include:
                return self.ext_include[ext]

        # Try mime type
        if mime_type:
            clean_mime = mime_type.split(";")[0].strip().lower()
            cat = MIME_TO_CATEGORY.get(clean_mime)
            if cat and cat in self.enabled_categories:
                return cat

        # Try guessing mime from URL
        guessed_mime, _ = mimetypes.guess_type(url)
        if guessed_mime:
            cat = MIME_TO_CATEGORY.get(guessed_mime)
            if cat and cat in self.enabled_categories:
                return cat

        return None

    def should_download(self, url: str, mime_type: Optional[str] = None) -> bool:
        """Check if a resource should be downloaded."""
        return self.get_category(url, mime_type) is not None

    def is_page(self, url: str, mime_type: Optional[str] = None) -> bool:
        """Check if a URL points to a web page (for crawling)."""
        cat = self.get_category(url, mime_type)
        return cat == "web_pages"
