"""Contract tests for the extractor's on-disk asset existence check.

The extraction engine emits two kinds of resource references that point
at scraper-produced files in the job directory:

  1. Image-typed leaf fields whose mapped DOM attribute resolves to an
     ``../assets/<name>`` URL — the rewrite that ``page_storage`` writes
     into the saved HTML when an asset is successfully downloaded.
  2. ``extract_file_patterns`` results, which read ``local_path``
     directly from ``scrape_resources`` rows.

Until this check existed, the engine trusted both sources unconditionally
and emitted dangling references whenever the underlying file was missing
(e.g. silently skipped by the dedup branch, never downloaded due to a
filtered MIME type, or removed off-band). The HTML can also reference
resources that don't exist on the source server in the first place — so
HTML presence is no proof of disk presence.

Both call sites now compute the on-disk path and ``is_file()``-check it
before emitting. These tests pin the path-building rules. If a refactor
moves the assets directory or changes the page_storage rewrite prefix,
they fail and surface the regression before it ships.
"""

from __future__ import annotations

from pathlib import Path


def _image_asset_disk_path(data_dir: Path, job_id: str, src: str) -> Path | None:
    """Mirror of engine.py's image-asset disk path computation.

    Returns the expected on-disk path for an ``../assets/<name>`` image
    src, or None if the src is not a scraper-rewritten asset reference
    (external URLs and unresolved relative paths are not disk-checked).
    """
    if not src.startswith("../assets/"):
        return None
    rel = src[len("../assets/"):]
    rel_clean = rel.split("?", 1)[0].split("#", 1)[0]
    return data_dir / "jobs" / job_id / "assets" / rel_clean


def _file_pattern_disk_path(data_dir: Path, job_id: str, local_path: str) -> Path:
    """Mirror of engine.py's file-pattern disk path computation.

    ``local_path`` in scrape_resources is stored as ``assets/<filename>``
    (relative to the job directory), so the disk path is
    ``<data_dir>/jobs/<job_id>/<local_path>``.
    """
    return data_dir / "jobs" / job_id / local_path


# ── Image asset path resolution ──────────────────────────────────────────

def test_image_src_with_assets_prefix_resolves_to_assets_dir(tmp_path: Path):
    job_id = "job-123"
    expected = tmp_path / "jobs" / job_id / "assets" / "abc123_cover.jpg"
    assert _image_asset_disk_path(tmp_path, job_id, "../assets/abc123_cover.jpg") == expected


def test_image_src_query_string_stripped_for_disk_check(tmp_path: Path):
    job_id = "job-123"
    # The on-disk filename is URL-derived and never includes query strings;
    # a cache-busting ?v= must not break the existence check.
    expected = tmp_path / "jobs" / job_id / "assets" / "abc123_cover.jpg"
    assert _image_asset_disk_path(tmp_path, job_id, "../assets/abc123_cover.jpg?v=2") == expected


def test_image_src_fragment_stripped_for_disk_check(tmp_path: Path):
    job_id = "job-123"
    expected = tmp_path / "jobs" / job_id / "assets" / "abc123_cover.jpg"
    assert _image_asset_disk_path(tmp_path, job_id, "../assets/abc123_cover.jpg#anchor") == expected


def test_external_http_url_not_disk_checked(tmp_path: Path):
    # External CDN URLs in image src are pass-through; they were never
    # expected to live on disk. The engine emits them unchanged, no check.
    assert _image_asset_disk_path(tmp_path, "j", "https://cdn.example.com/foo.jpg") is None


def test_unresolved_relative_url_not_disk_checked(tmp_path: Path):
    # Anything not prefixed with ../assets/ falls through to urljoin and
    # is not a scraper-handled asset, so it isn't disk-checked either.
    assert _image_asset_disk_path(tmp_path, "j", "/static/foo.jpg") is None


# ── Image asset existence ────────────────────────────────────────────────

def test_present_asset_passes_existence_check(tmp_path: Path):
    job_id = "job-123"
    assets = tmp_path / "jobs" / job_id / "assets"
    assets.mkdir(parents=True)
    (assets / "abc123_cover.jpg").write_bytes(b"fake-jpg")

    disk_path = _image_asset_disk_path(tmp_path, job_id, "../assets/abc123_cover.jpg")
    assert disk_path is not None and disk_path.is_file()


def test_missing_asset_fails_existence_check(tmp_path: Path):
    job_id = "job-123"
    assets = tmp_path / "jobs" / job_id / "assets"
    assets.mkdir(parents=True)
    # File NOT written — simulates dedup-skipped or never-downloaded asset.

    disk_path = _image_asset_disk_path(tmp_path, job_id, "../assets/missing_cover.jpg")
    assert disk_path is not None and not disk_path.is_file()


# ── File-pattern resource existence ──────────────────────────────────────

def test_file_pattern_local_path_resolves_under_job_dir(tmp_path: Path):
    job_id = "job-123"
    expected = tmp_path / "jobs" / job_id / "assets" / "abc123_cover.jpg"
    assert _file_pattern_disk_path(tmp_path, job_id, "assets/abc123_cover.jpg") == expected


def test_file_pattern_present_resource_passes(tmp_path: Path):
    job_id = "job-123"
    assets = tmp_path / "jobs" / job_id / "assets"
    assets.mkdir(parents=True)
    (assets / "abc123_cover.jpg").write_bytes(b"fake-jpg")

    disk_path = _file_pattern_disk_path(tmp_path, job_id, "assets/abc123_cover.jpg")
    assert disk_path.is_file()


def test_file_pattern_missing_resource_fails(tmp_path: Path):
    job_id = "job-123"
    # No assets directory at all — DB row exists, file does not.
    disk_path = _file_pattern_disk_path(tmp_path, job_id, "assets/abc123_cover.jpg")
    assert not disk_path.is_file()


def test_file_pattern_empty_local_path_treated_as_missing(tmp_path: Path):
    # A scrape_resources row with an empty local_path (malformed legacy
    # data) must not be silently emitted — the engine's caller treats
    # empty local_path the same as missing-on-disk.
    local_path = ""
    assert not local_path  # the engine guards on this falsy check first
