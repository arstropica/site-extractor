"""
Shared data models for all Site Extractor services.

Defines API contracts between the API gateway, scraper service,
extraction service, and UI.
"""

from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
import uuid


# ─── Enums ────────────────────────────────────────────────────────────────────

class JobStatus(str, Enum):
    CREATED = "created"
    SCRAPING = "scraping"
    SCRAPED = "scraped"
    MAPPING = "mapping"
    EXTRACTING = "extracting"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class ExtractionMode(str, Enum):
    DOCUMENT = "document"
    FILE = "file"


class CrawlMode(str, Enum):
    HTTP = "http"
    BROWSER = "browser"


class AuthMethod(str, Enum):
    NONE = "none"
    BASIC = "basic"
    BEARER = "bearer"
    COOKIE = "cookie"
    BROWSER_SESSION = "browser_session"


class SchemaFieldType(str, Enum):
    STRING = "string"
    NUMBER = "number"
    IMAGE = "image"


class ResourceCategory(str, Enum):
    WEB_PAGES = "web_pages"
    IMAGES = "images"
    MEDIA = "media"
    DOCUMENTS = "documents"
    ARCHIVES = "archives"
    CODE = "code"


# ─── Resource Filter Defaults ────────────────────────────────────────────────

DEFAULT_RESOURCE_FILTERS: Dict[str, Dict[str, Any]] = {
    "web_pages": {
        "label": "Web Pages",
        "extensions": ["html", "htm", "php", "asp", "aspx", "jsp"],
        "enabled": True,
        "mode": "include",
    },
    "images": {
        "label": "Images",
        "extensions": ["jpg", "jpeg", "png", "gif", "webp", "svg", "ico", "bmp", "tiff"],
        "enabled": False,
        "mode": "include",
    },
    "media": {
        "label": "Media",
        "extensions": ["mp4", "m4v", "avi", "mov", "wmv", "webm", "mkv", "mp3", "m4a", "wav", "ogg", "oga", "ogv", "flac", "aac"],
        "enabled": False,
        "mode": "include",
    },
    "documents": {
        "label": "Documents",
        "extensions": ["pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv", "rtf"],
        "enabled": False,
        "mode": "include",
    },
    "archives": {
        "label": "Archives",
        "extensions": ["zip", "tar", "gz", "rar", "7z"],
        "enabled": False,
        "mode": "include",
    },
    "code": {
        "label": "Code",
        "extensions": ["json", "xml", "yaml", "css", "js"],
        "enabled": False,
        "mode": "include",
    },
}


# ─── Scraper Configuration Models ────────────────────────────────────────────

class AuthConfig(BaseModel):
    method: AuthMethod = AuthMethod.NONE
    username: Optional[str] = None
    password: Optional[str] = None
    token: Optional[str] = None
    cookies: Optional[Dict[str, str]] = None
    session_data: Optional[Dict[str, Any]] = None


class ResourceFilter(BaseModel):
    label: str
    extensions: List[str]
    enabled: bool = False
    mode: str = "include"
    exclude_extensions: List[str] = Field(default_factory=list)


class DomainFilter(BaseModel):
    allowed_domains: List[str] = Field(default_factory=list)
    path_filters: List[str] = Field(default_factory=list)


class ScrapeConfig(BaseModel):
    seed_urls: List[str]
    crawl_mode: CrawlMode = CrawlMode.HTTP
    depth_limit: int = 3
    domain_filter: DomainFilter = Field(default_factory=DomainFilter)
    resource_filters: Dict[str, ResourceFilter] = Field(default_factory=dict)
    respect_robots: bool = True
    request_delay_ms: int = 500
    max_concurrent_per_domain: int = 2
    max_concurrent_total: int = 10
    max_download_size: Optional[int] = None
    auth: AuthConfig = Field(default_factory=AuthConfig)
    user_agent: Optional[str] = None
    retry_limit: Optional[int] = None  # per-job override; falls back to env SCRAPER_RETRY_LIMIT


# ─── Extraction Schema Models ────────────────────────────────────────────────

class SchemaField(BaseModel):
    name: str
    field_type: SchemaFieldType = SchemaFieldType.STRING
    is_array: bool = False
    children: Optional[List["SchemaField"]] = None


class ExtractionSchema(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str = ""
    fields: List[SchemaField] = Field(default_factory=list)
    is_template: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ─── Boundary-Based Extraction Mapping Models ────────────────────────────────
#
# Boundaries define the DOM scope for extraction. They are cumulative:
#   root_boundary=".products" + nested boundary=".card"
#   → effective scope is ".products .card"
#
# Collections require an iterator selector that identifies repeating elements
# within the boundary. Records can optionally narrow scope with their own
# boundary. Leaf fields map a CSS selector (+ optional attribute) to a value.

class FieldMapping(BaseModel):
    """Maps a schema field to a value source within the current boundary scope.

    Sources are tried in priority order: url_regex > selector. If url_regex
    is set, the field value comes from a regex capture against the page URL
    rather than from the DOM. Useful for IDs embedded in URL paths.
    """
    field_path: str              # dot-notation, e.g. "title" or "movie.title"
    selector: Optional[str] = None  # CSS selector; None = use boundary element itself
    attribute: Optional[str] = None  # e.g. "href", "src", "data-id"; None = textContent
    url_regex: Optional[str] = None  # if set, value = capture group 1 of regex against page URL


class BoundaryMapping(BaseModel):
    """Defines a boundary scope for a record or collection in the schema."""
    field_path: str              # dot-notation path to the record/collection field
    boundary: Optional[str] = None  # CSS selector narrowing scope; None = inherit parent
    iterator: Optional[str] = None  # for collections: selector for each repeated element


class DocumentExtractionConfig(BaseModel):
    """Mapping configuration for document-based extraction."""
    root_boundary: Optional[str] = None  # top-level boundary; None = <body> (one record/page)
    url_pattern: Optional[str] = None    # only apply to pages matching this pattern
    boundaries: List[BoundaryMapping] = Field(default_factory=list)
    field_mappings: List[FieldMapping] = Field(default_factory=list)
    # Optional: collapse multiple records into one by grouping on a field value.
    # Useful when the same logical entity appears across multiple URLs (e.g.,
    # player Overview/Bio/GameLog tabs).
    merge_by: Optional[str] = None  # dot-notation path to the merge key field
    merge_strategy: str = "first_non_null"  # for now only this strategy is supported


class FilePattern(BaseModel):
    """Maps a regex pattern to a category key for file-based extraction."""
    schema_key: str
    regex_pattern: str


class ExtractionConfig(BaseModel):
    """Top-level extraction configuration for a job."""
    mode: ExtractionMode = ExtractionMode.DOCUMENT
    schema_id: Optional[str] = None
    document: Optional[DocumentExtractionConfig] = None
    file_patterns: List[FilePattern] = Field(default_factory=list)


# ─── Job Models ──────────────────────────────────────────────────────────────

class Job(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: Optional[str] = None
    status: JobStatus = JobStatus.CREATED
    scrape_config: ScrapeConfig
    extraction_config: Optional[ExtractionConfig] = None

    progress: float = 0.0
    progress_message: str = ""
    pages_discovered: int = 0
    pages_downloaded: int = 0
    pages_errored: int = 0
    resources_discovered: int = 0
    resources_downloaded: int = 0
    resources_errored: int = 0
    bytes_downloaded: int = 0
    error_message: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    scraped_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


# ─── API Request/Response Models ─────────────────────────────────────────────

class CreateJobRequest(BaseModel):
    name: Optional[str] = None
    scrape_config: ScrapeConfig


class UpdateJobExtractionRequest(BaseModel):
    extraction_config: ExtractionConfig


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    message: str = "Job created"


class JobStatusResponse(BaseModel):
    id: str
    status: JobStatus
    progress: float
    progress_message: str
    pages_discovered: int
    pages_downloaded: int
    pages_errored: int = 0
    resources_discovered: int = 0
    resources_downloaded: int = 0
    resources_errored: int = 0
    bytes_downloaded: int
    error_message: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    scraped_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class JobListItem(BaseModel):
    id: str
    name: Optional[str] = None
    status: JobStatus
    extraction_mode: Optional[ExtractionMode] = None
    seed_urls: List[str]
    pages_downloaded: int = 0
    resources_downloaded: int = 0
    created_at: datetime
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None


class CreateSchemaRequest(BaseModel):
    name: str
    description: str = ""
    fields: List[SchemaField] = Field(default_factory=list)
    is_template: bool = False


class UpdateSchemaRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    fields: Optional[List[SchemaField]] = None
    is_template: Optional[bool] = None


class ScrapePageInfo(BaseModel):
    id: str
    job_id: str
    url: str
    local_path: str
    status: str
    content_type: Optional[str] = None
    size: int = 0
    depth: int = 0
    parent_url: Optional[str] = None


class ExtractionResultRow(BaseModel):
    page_url: Optional[str] = None
    data: Dict[str, Any]


class ExtractRequest(BaseModel):
    job_id: str
    extraction_config: ExtractionConfig
    schema_fields: List[SchemaField]


class ExtractPreviewRequest(BaseModel):
    job_id: str
    extraction_config: ExtractionConfig
    schema_fields: List[SchemaField]
    limit: int = 20


class ValidateSelectorRequest(BaseModel):
    job_id: str
    selector: str
    limit: int = 10


class ValidateSelectorResponse(BaseModel):
    selector: str
    match_count: int
    pages_checked: int
    sample_matches: List[Dict[str, Any]] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str = "healthy"
    services: Dict[str, str] = Field(default_factory=dict)
    version: str = "0.1.0"


# ─── WebSocket Event Models ─────────────────────────────────────────────────

class WSEventType(str, Enum):
    PAGE_DISCOVERED = "PAGE_DISCOVERED"
    PAGE_DOWNLOADED = "PAGE_DOWNLOADED"
    RESOURCE_DISCOVERED = "RESOURCE_DISCOVERED"
    RESOURCE_DOWNLOADED = "RESOURCE_DOWNLOADED"
    SCRAPE_ERROR = "SCRAPE_ERROR"
    SCRAPE_PROGRESS = "SCRAPE_PROGRESS"
    SCRAPE_STATUS = "SCRAPE_STATUS"
    PAGE_TREE_UPDATE = "PAGE_TREE_UPDATE"
    EXTRACTION_PROGRESS = "EXTRACTION_PROGRESS"
    EXTRACTION_STATUS = "EXTRACTION_STATUS"


class WSEvent(BaseModel):
    type: WSEventType
    job_id: str
    data: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
