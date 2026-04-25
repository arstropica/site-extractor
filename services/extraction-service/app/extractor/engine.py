"""
Extraction engine — applies boundary-scoped CSS selectors to scraped HTML.

The engine uses a cumulative boundary model:
  - A root boundary defines the top-level scope (one record per match,
    or one record per page if no root boundary is set).
  - Nested record/collection boundaries narrow scope cumulatively.
  - Collections use an iterator selector to identify repeating elements
    within their boundary.
  - Leaf fields extract text content, parsed numbers, or image src
    attributes from matched elements, with an optional attribute override.
"""

import hashlib
import logging
import re
import shutil
from pathlib import Path
from typing import Optional, List, Dict, Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)


class ExtractionEngine:
    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)

    def extract_from_pages(
        self,
        job_id: str,
        pages: List[Dict[str, Any]],
        schema_fields: List[Dict[str, Any]],
        config: Dict[str, Any],
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Run document-based extraction across scraped pages.

        Returns a list of extracted records.
        """
        doc_config = config.get("document", {})
        root_boundary = doc_config.get("root_boundary")
        url_pattern = doc_config.get("url_pattern")
        boundaries = {b["field_path"]: b for b in doc_config.get("boundaries", [])}
        field_mappings = doc_config.get("field_mappings", [])

        # Build a lookup: field_path -> FieldMapping
        mapping_lookup: Dict[str, Dict[str, Any]] = {}
        for fm in field_mappings:
            mapping_lookup[fm["field_path"]] = fm

        # Build schema tree for navigating nested fields
        schema_tree = self._build_schema_tree(schema_fields)

        results = []

        for page in pages:
            # URL pattern filter
            if url_pattern and not self._url_matches(page.get("url", ""), url_pattern):
                continue

            html_path = self.data_dir / "jobs" / job_id / page.get("local_path", "")
            if not html_path.is_file():
                continue

            try:
                html = html_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"Failed to read {html_path}: {e}")
                continue

            soup = BeautifulSoup(html, "lxml")
            body = soup.body or soup

            # Find root scope elements
            if root_boundary:
                scope_elements = body.select(root_boundary)
            else:
                scope_elements = [body]

            for scope_el in scope_elements:
                record = self._extract_record(
                    scope_el, schema_tree, mapping_lookup, boundaries,
                    parent_path="", page_url=page.get("url", ""),
                    job_id=job_id,
                )
                results.append({
                    "page_url": page.get("url"),
                    "data": record,
                })

                if limit and len(results) >= limit:
                    return self._maybe_merge(results, doc_config)

        return self._maybe_merge(results, doc_config)

    def _maybe_merge(self, results: List[Dict[str, Any]], doc_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Optional post-extraction merge: collapse records with the same key value
        into one, taking first-non-null per field across the group.
        """
        merge_by = doc_config.get("merge_by")
        if not merge_by:
            return results

        groups: Dict[Any, List[Dict[str, Any]]] = {}
        ordered_keys: List[Any] = []  # preserve first-seen order
        ungrouped: List[Dict[str, Any]] = []  # rows missing the merge key

        for row in results:
            key = self._read_path(row.get("data", {}), merge_by)
            if key is None or key == "":
                ungrouped.append(row)
                continue
            if key not in groups:
                groups[key] = []
                ordered_keys.append(key)
            groups[key].append(row)

        merged: List[Dict[str, Any]] = []
        for key in ordered_keys:
            rows = groups[key]
            merged_data: Dict[str, Any] = {}
            urls: List[str] = []
            for r in rows:
                if r.get("page_url"):
                    urls.append(r["page_url"])
                self._merge_dicts(merged_data, r.get("data", {}))
            merged.append({
                # Concatenate URLs of merged records for traceability
                "page_url": " ; ".join(urls) if urls else None,
                "data": merged_data,
            })
        # Keep ungrouped rows as-is (no merge key found)
        return merged + ungrouped

    @staticmethod
    def _read_path(obj: Any, path: str) -> Any:
        """Read a dot-notation path from a nested dict. Returns None if missing."""
        cur = obj
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return None
        return cur

    @staticmethod
    def _merge_dicts(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
        """In-place merge src into dst using first-non-null/non-empty semantics.
        Recursively merges nested dicts. For lists, takes the first non-empty
        list (later non-empty lists are ignored — assumes one source has the
        canonical array).
        """
        for k, v in src.items():
            if k not in dst or dst[k] is None or dst[k] == "" or dst[k] == [] or dst[k] == {}:
                dst[k] = v
                continue
            existing = dst[k]
            if isinstance(existing, dict) and isinstance(v, dict):
                ExtractionEngine._merge_dicts(existing, v)
            elif isinstance(existing, list):
                # Already non-empty: keep as-is (first non-empty wins)
                pass
            # else: existing is non-null scalar, keep it

    def extract_file_patterns(
        self,
        job_id: str,
        resources: List[Dict[str, Any]],
        file_patterns: List[Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Run file-based extraction using regex patterns."""
        result: Dict[str, List[Dict[str, Any]]] = {}

        for pattern in file_patterns:
            key = pattern["schema_key"]
            regex = pattern["regex_pattern"]
            result[key] = []

            try:
                compiled = re.compile(regex)
            except re.error as e:
                logger.warning(f"Invalid regex for '{key}': {e}")
                continue

            for resource in resources:
                filename = resource.get("filename", "")
                if compiled.search(filename):
                    result[key].append({
                        "filename": filename,
                        "path": resource.get("local_path", ""),
                        "size": resource.get("size", 0),
                        "mime": resource.get("mime_type", ""),
                        "url": resource.get("url", ""),
                    })

        return result

    def validate_selector(
        self,
        job_id: str,
        selector: str,
        pages: List[Dict[str, Any]],
        limit: int = 10,
    ) -> Dict[str, Any]:
        """Validate a CSS selector against stored pages."""
        total_matches = 0
        pages_checked = 0
        samples = []

        for page in pages[:50]:  # check at most 50 pages
            html_path = self.data_dir / "jobs" / job_id / page.get("local_path", "")
            if not html_path.is_file():
                continue

            try:
                html = html_path.read_text(encoding="utf-8")
            except Exception:
                continue

            soup = BeautifulSoup(html, "lxml")
            body = soup.body or soup
            pages_checked += 1

            try:
                matches = body.select(selector)
            except Exception:
                return {
                    "selector": selector,
                    "match_count": 0,
                    "pages_checked": pages_checked,
                    "error": "Invalid CSS selector",
                    "sample_matches": [],
                }

            total_matches += len(matches)

            for m in matches[:limit - len(samples)]:
                text = m.get_text(strip=True)[:200] if m.get_text(strip=True) else ""
                samples.append({
                    "page_url": page.get("url", ""),
                    "text": text,
                    "tag": m.name,
                    "classes": m.get("class", []),
                })
                if len(samples) >= limit:
                    break

            if len(samples) >= limit:
                break

        return {
            "selector": selector,
            "match_count": total_matches,
            "pages_checked": pages_checked,
            "sample_matches": samples,
        }

    # ── Internal extraction helpers ───────────────────────────────────────

    def _extract_record(
        self,
        scope_el: Tag,
        schema_tree: List[Dict],
        mapping_lookup: Dict[str, Dict],
        boundaries: Dict[str, Dict],
        parent_path: str,
        page_url: str,
        job_id: str,
    ) -> Dict[str, Any]:
        """Extract a single record from a DOM scope element."""
        record: Dict[str, Any] = {}

        for field in schema_tree:
            field_name = field["name"]
            field_path = f"{parent_path}.{field_name}" if parent_path else field_name
            field_type = field.get("field_type", "string")
            is_array = field.get("is_array", False)
            children = field.get("children")

            if children:
                # This is a record or collection — find its boundary scope
                boundary_config = boundaries.get(field_path, {})
                boundary_selector = boundary_config.get("boundary")
                iterator_selector = boundary_config.get("iterator")

                if is_array:
                    # Collection: iterate over elements
                    record[field_name] = self._extract_collection(
                        scope_el, boundary_selector, iterator_selector,
                        children, mapping_lookup, boundaries,
                        field_path, page_url, job_id,
                    )
                else:
                    # Nested record: narrow scope
                    if boundary_selector:
                        nested_el = scope_el.select_one(boundary_selector)
                        if not nested_el:
                            record[field_name] = {}
                            continue
                    else:
                        nested_el = scope_el

                    record[field_name] = self._extract_record(
                        nested_el, children, mapping_lookup, boundaries,
                        field_path, page_url, job_id,
                    )
            else:
                # Leaf field
                mapping = mapping_lookup.get(field_path)
                if not mapping:
                    record[field_name] = None
                    continue

                selector = mapping.get("selector")
                attribute = mapping.get("attribute")
                url_regex = mapping.get("url_regex")

                # url_regex source takes priority over selector
                if url_regex:
                    record[field_name] = self._extract_from_url(page_url, url_regex, field_type)
                elif is_array:
                    record[field_name] = self._extract_leaf_array(
                        scope_el, selector, attribute, field_type,
                        page_url, job_id,
                    )
                else:
                    record[field_name] = self._extract_leaf(
                        scope_el, selector, attribute, field_type,
                        page_url, job_id,
                    )

        return record

    def _extract_collection(
        self,
        scope_el: Tag,
        boundary_selector: Optional[str],
        iterator_selector: Optional[str],
        children: List[Dict],
        mapping_lookup: Dict[str, Dict],
        boundaries: Dict[str, Dict],
        field_path: str,
        page_url: str,
        job_id: str,
    ) -> List[Dict[str, Any]]:
        """Extract a collection (array of records) from the DOM."""
        # Determine the scope for iteration
        if boundary_selector:
            container = scope_el.select_one(boundary_selector)
            if not container:
                return []
        else:
            container = scope_el

        # Find iterator elements
        if iterator_selector:
            elements = container.select(iterator_selector)
        else:
            # No iterator — treat the container itself as a single element
            elements = [container]

        items = []
        for el in elements:
            item = self._extract_record(
                el, children, mapping_lookup, boundaries,
                field_path, page_url, job_id,
            )
            items.append(item)

        return items

    def _extract_leaf(
        self,
        scope_el: Tag,
        selector: Optional[str],
        attribute: Optional[str],
        field_type: str,
        page_url: str,
        job_id: str,
    ) -> Any:
        """Extract a single leaf value from the DOM."""
        if selector:
            # Support disjunctions: "a.link | span.alt"
            parts = [s.strip() for s in selector.split("|")]
            el = None
            for part in parts:
                try:
                    el = scope_el.select_one(part)
                except Exception:
                    continue
                if el:
                    break
            if not el:
                return None
        else:
            el = scope_el

        return self._extract_value(el, attribute, field_type, page_url, job_id)

    def _extract_leaf_array(
        self,
        scope_el: Tag,
        selector: Optional[str],
        attribute: Optional[str],
        field_type: str,
        page_url: str,
        job_id: str,
    ) -> List[Any]:
        """Extract multiple leaf values (array of primitives).

        Smart heuristic: when extracting an array of <td> cells from a row
        inside a table with a thead, the result is enriched to
        [{"header": <column-header>, "value": <cell-value>}, ...] instead of
        a flat list. Falls back to flat list when the heuristic doesn't apply.
        """
        if not selector:
            return []

        parts = [s.strip() for s in selector.split("|")]
        elements = []
        for part in parts:
            try:
                elements.extend(scope_el.select(part))
            except Exception:
                continue

        # Smart-stats: enrich with column headers when applicable
        headers = self._table_headers_for_row(scope_el, elements)
        if headers is not None:
            results = []
            for el in elements:
                header = headers.get(self._cell_index(el))
                value = self._extract_value(el, attribute, field_type, page_url, job_id)
                results.append({"header": header, "value": value})
            return results

        return [
            self._extract_value(el, attribute, field_type, page_url, job_id)
            for el in elements
        ]

    def _table_headers_for_row(self, scope_el: Tag, elements: List[Tag]) -> Optional[Dict[int, str]]:
        """Return {column_index: header_text} if elements look like row cells with column headers.

        Conditions:
          - All elements are <td>
          - They share the same <tr> ancestor (the iterator scope is a row)
          - The <tr> is inside a <table> with a <thead>
        Returns None if conditions aren't met (caller falls back to plain values).
        """
        if not elements:
            return None
        if any(el.name != "td" for el in elements):
            return None

        # All cells should share the same parent row
        parent_rows = {el.parent for el in elements if el.parent and el.parent.name == "tr"}
        if len(parent_rows) != 1:
            return None
        row = parent_rows.pop()

        table = row.find_parent("table")
        if not table:
            return None

        thead = table.find("thead")
        if not thead:
            return None

        # Build column-index → header text using the LAST sub-header row in thead
        # (covers both single-row and multi-row colgroup headers)
        header_rows = thead.find_all("tr")
        if not header_rows:
            return None
        header_row = header_rows[-1]
        header_cells = header_row.find_all(["th", "td"])
        return {i: c.get_text(strip=True) for i, c in enumerate(header_cells)}

    def _cell_index(self, td: Tag) -> int:
        """Return the 0-based column index of a <td> within its <tr>."""
        if not td.parent or td.parent.name != "tr":
            return -1
        cells = [c for c in td.parent.children if getattr(c, "name", None) == "td"]
        try:
            return cells.index(td)
        except ValueError:
            return -1

    def _extract_value(
        self,
        el: Tag,
        attribute: Optional[str],
        field_type: str,
        page_url: str,
        job_id: str,
    ) -> Any:
        """Extract a typed value from a DOM element."""
        if attribute:
            raw = el.get(attribute, "")
            if isinstance(raw, list):
                raw = " ".join(raw)
        elif field_type == "image":
            # Default to src for images
            raw = el.get("src", "") or el.get("data-src", "")
        else:
            raw = el.get_text(strip=True)

        if not raw:
            return None

        if field_type == "number":
            # Extract numeric value from text
            cleaned = re.sub(r"[^\d.\-]", "", str(raw))
            try:
                return float(cleaned) if "." in cleaned else int(cleaned)
            except (ValueError, TypeError):
                return None

        elif field_type == "image":
            # Resolve relative URL and store locally
            src = str(raw)
            if src.startswith("../assets/"):
                # Already a local path from page_storage rewriting
                return src.replace("../", f"/data/jobs/{job_id}/")
            elif src.startswith(("http://", "https://")):
                return src
            elif page_url:
                return urljoin(page_url, src)
            return src

        else:
            # String
            return str(raw)

    def _build_schema_tree(self, fields: List[Dict]) -> List[Dict]:
        """Ensure schema fields are in dict form (handle Pydantic models)."""
        result = []
        for f in fields:
            if hasattr(f, "model_dump"):
                d = f.model_dump()
            elif isinstance(f, dict):
                d = f
            else:
                d = dict(f)
            if d.get("children"):
                d["children"] = self._build_schema_tree(d["children"])
            result.append(d)
        return result

    def _extract_from_url(self, url: str, regex: str, field_type: str) -> Any:
        """Extract a value from the page URL via regex capture group 1.
        Returns the typed value or None if the regex doesn't match.
        """
        if not url or not regex:
            return None
        try:
            m = re.search(regex, url)
        except re.error as e:
            logger.warning(f"Invalid url_regex {regex!r}: {e}")
            return None
        if not m:
            return None
        # Use capture group 1 if present, else the full match
        try:
            value = m.group(1)
        except IndexError:
            value = m.group(0)
        if field_type == "number":
            try:
                return float(value) if "." in value else int(value)
            except (ValueError, TypeError):
                return None
        return value

    def _url_matches(self, url: str, pattern: str) -> bool:
        """Check if a URL matches a pattern (supports wildcards)."""
        # Convert pattern to regex
        regex = pattern.replace(".", r"\.").replace("*", ".*")
        if not regex.startswith("http"):
            regex = f".*{regex}"
        return bool(re.search(regex, url, re.IGNORECASE))
