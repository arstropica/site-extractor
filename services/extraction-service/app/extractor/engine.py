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
import itertools
import logging
import re
import shutil
from pathlib import Path
from typing import Optional, List, Dict, Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)


# Sentinel for "extracted value is a scraper-rewritten ../assets/<name>
# reference but the file is missing on disk." Distinguished from None
# (genuine no-match / empty value) so callers can drop these from
# arrays rather than leave holes. See _extract_leaf and
# _extract_leaf_array for the propagation rules.
_MISSING_ASSET = object()


_LEADING_COMBINATORS = (">", "+", "~")


def _normalize_selector(s: str) -> str:
    """Prepend ':scope ' when a selector starts with a top-level combinator.

    Soup Sieve rejects leading-combinator selectors at the top level (same
    rule browsers enforce in querySelectorAll). Users intuitively expect
    '> tr' to mean 'direct child of the matched element'; this normalization
    makes that work by injecting ':scope '. Selectors that don't start with
    a combinator are returned unchanged.
    """
    if not s:
        return s
    stripped = s.lstrip()
    if stripped and stripped[0] in _LEADING_COMBINATORS:
        return f":scope {stripped}"
    return s


def _resolve_selector(s: str, subs: Optional[Dict[str, int]] = None) -> str:
    """Apply iterator-index substitution then normalize.

    `subs` is a dict of {iterator_name: index} bindings to apply via
    `str.format(**subs)`. If the selector contains no placeholders or `subs`
    is empty/None, this is equivalent to `_normalize_selector(s)`. KeyError
    or IndexError during substitution leaves the selector as-is — caller
    can decide whether the resulting (still-templated) selector is an error.
    """
    if not s:
        return s
    if subs:
        try:
            s = s.format(**subs)
        except (KeyError, IndexError, ValueError):
            pass
    return _normalize_selector(s)


# CSS rule shape: `selectors { declarations }`. Comments are stripped before
# matching, so `_RULE_RE` only sees rule bodies. `_DECL_RE` parses individual
# `property: value` pairs from a declaration block or an inline style attr.
_CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_CSS_DECL_RE = re.compile(r"([\w-]+)\s*:\s*([^;]+?)\s*(?:;|$)")
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
# Inner-URL extractor for url() refs inside a property value. Lets
# _extract_style unwrap background-image, list-style-image, mask-image,
# cursor, etc. and emit the bare URLs instead of `url("...")` literals.
_URL_INNER_RE = re.compile(r"url\(\s*(?:\"([^\"]+)\"|'([^']+)'|([^)\s\"']+))\s*\)")


def _urls_in_value(value: str) -> List[str]:
    """Pull every url() inner string out of a CSS property value.

    Skips data: and #fragment refs — they aren't fetchable assets.
    """
    out: List[str] = []
    for m in _URL_INNER_RE.finditer(value or ""):
        u = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        if u and not u.startswith(("data:", "#")):
            out.append(u)
    return out


def _parse_declarations(blob: str) -> Dict[str, str]:
    """Parse a CSS declaration block / inline style attr into {prop: value}."""
    out: Dict[str, str] = {}
    for m in _CSS_DECL_RE.finditer(blob or ""):
        prop = m.group(1).strip().lower()
        val = m.group(2).strip()
        if prop and val:
            out[prop] = val
    return out


class _StyleResolver:
    """Per-page CSS lookup for the `style_property` field source.

    Builds a list of (selector_text, declarations) entries in document order
    by walking the page's inline <style> blocks and each external stylesheet
    saved to disk (via the rewritten ../assets/ href). Lookup is exact match
    on selector text — the user opted in to "first-match in the cascaded
    ruleset" rather than a full specificity-aware cascade, so we don't try to
    resolve which rules actually apply to the element.
    """

    def __init__(self, soup, page_html_path: Path, data_dir: Path, job_id: str):
        self._rules: List = []  # [(selector_text, {prop: value})]

        # Inline <style> blocks first — they sit in <head> before/after the
        # external link, but for first-match semantics the absolute order
        # doesn't matter much. Author intent is usually "inline overrides
        # external," which document order tends to follow anyway.
        for style_tag in soup.find_all("style"):
            self._ingest(style_tag.string or style_tag.get_text() or "")

        # External stylesheets. The scraper rewrites <link rel=stylesheet>
        # hrefs to ../assets/<filename>. Resolve those relative to the saved
        # HTML's parent dir, then clamp to the job root so a hostile relative
        # path can't escape into other jobs' data.
        job_root = (data_dir / "jobs" / job_id).resolve()
        page_dir = page_html_path.parent.resolve()
        for link in soup.find_all("link"):
            rel = link.get("rel")
            if not rel or "stylesheet" not in rel:
                continue
            href = link.get("href")
            if not href:
                continue
            try:
                css_path = (page_dir / href).resolve()
                css_path.relative_to(job_root)
            except (ValueError, OSError):
                continue
            if not css_path.is_file():
                continue
            try:
                text = css_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            self._ingest(text)

    def _ingest(self, css_text: str) -> None:
        text = _CSS_COMMENT_RE.sub("", css_text)
        for m in _CSS_RULE_RE.finditer(text):
            sel_blob = m.group(1).strip()
            decls = _parse_declarations(m.group(2))
            if not decls:
                continue
            # A rule like `.a, .b { color: red }` registers under each
            # selector independently so exact-match lookup works either way.
            for sel in sel_blob.split(","):
                sel = sel.strip()
                if sel:
                    self._rules.append((sel, decls))

    def resolve(self, element, field_selector: Optional[str], prop: str) -> Optional[str]:
        """Return the CSS value for `prop` on `element`, or None.

        Lookup order:
          1. Element's own style="..." attribute (true inline).
          2. Rules in document order whose selector text equals
             `field_selector` (exact string match, after .strip()).
        First match wins.
        """
        p = prop.strip().lower()
        if not p:
            return None
        if element is not None:
            inline = element.get("style") if hasattr(element, "get") else None
            if inline:
                decls = _parse_declarations(inline)
                if p in decls:
                    return decls[p]
        if field_selector:
            target = field_selector.strip()
            for sel, decls in self._rules:
                if sel == target and p in decls:
                    return decls[p]
        return None


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
        root_iterators = doc_config.get("iterators", [])

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

            # Per-page CSS resolver. Built once per page so multiple
            # style_property fields don't re-parse the stylesheets.
            style_resolver = _StyleResolver(soup, html_path, self.data_dir, job_id)

            # Find root scope elements
            if root_boundary:
                scope_elements = body.select(_normalize_selector(root_boundary))
            else:
                scope_elements = [body]

            for scope_el in scope_elements:
                for record in self._iter_records(
                    scope_el, root_iterators, schema_tree, mapping_lookup,
                    boundaries, parent_path="", page_url=page.get("url", ""),
                    job_id=job_id, base_subs={}, style_resolver=style_resolver,
                ):
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
                if not compiled.search(filename):
                    continue
                local_path = resource.get("local_path", "")
                disk_path = self.data_dir / "jobs" / job_id / local_path
                if not local_path or not disk_path.is_file():
                    logger.warning(
                        "Skipping missing resource for file_pattern '%s': %s (expected at %s)",
                        key, filename, disk_path,
                    )
                    continue
                result[key].append({
                    "filename": filename,
                    "path": local_path,
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
                matches = body.select(_normalize_selector(selector))
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

    def _iter_records(
        self,
        scope_el: Tag,
        iterators: List[Dict[str, Any]],
        schema_tree: List[Dict],
        mapping_lookup: Dict[str, Dict],
        boundaries: Dict[str, Dict],
        parent_path: str,
        page_url: str,
        job_id: str,
        base_subs: Dict[str, int],
        style_resolver: Optional["_StyleResolver"] = None,
    ) -> List[Dict[str, Any]]:
        """Expand iterators into per-iteration records (Cartesian product).

        With no iterators, returns a one-element list containing the single
        record extracted from `scope_el`. With iterators, evaluates each
        count selector against the scope, loops indices, substitutes them
        into selector templates during extraction, and applies the anchor-skip
        rule per iteration.
        """
        if not iterators:
            return [self._extract_record(
                scope_el, schema_tree, mapping_lookup, boundaries,
                parent_path, page_url, job_id, base_subs, style_resolver,
            )]

        # Evaluate count for each iterator (relative to scope_el; parent
        # subs available for nested cases).
        counts: List[tuple] = []  # [(name, n), ...]
        for it in iterators:
            sel = _resolve_selector(it["count_selector"], base_subs)
            try:
                n = len(scope_el.select(sel))
            except Exception as e:
                logger.warning(f"Iterator count selector failed: {sel!r}: {e}")
                return []
            if n == 0:
                return []
            counts.append((it["name"], n))

        records: List[Dict[str, Any]] = []
        ranges = [range(1, n + 1) for _, n in counts]
        for combo in itertools.product(*ranges):
            subs = dict(base_subs)
            for (name, _), idx in zip(counts, combo):
                subs[name] = idx

            if self._iteration_skipped(iterators, scope_el, subs, mapping_lookup, schema_tree, parent_path):
                continue

            records.append(self._extract_record(
                scope_el, schema_tree, mapping_lookup, boundaries,
                parent_path, page_url, job_id, subs, style_resolver,
            ))
        return records

    def _iteration_skipped(
        self,
        iterators: List[Dict[str, Any]],
        scope_el: Tag,
        subs: Dict[str, int],
        mapping_lookup: Dict[str, Dict],
        schema_tree: List[Dict],
        parent_path: str,
    ) -> bool:
        """Apply anchor-skip rule. Returns True if this iteration should be dropped.

        For each iterator, identify its anchor field(s) — explicit list, single
        string, or default (first declared field at this scope). Skip the
        iteration when ALL listed anchor fields produce empty content
        (OR-of-presence: keep the record if ANY anchor has content).
        """
        for it in iterators:
            anchor = it.get("anchor")
            if anchor is None:
                # Default: first field at this scope level.
                first = schema_tree[0] if schema_tree else None
                if not first:
                    continue
                first_name = first["name"]
                anchor_paths = [
                    f"{parent_path}.{first_name}" if parent_path else first_name
                ]
            elif isinstance(anchor, str):
                anchor_paths = [anchor]
            else:
                anchor_paths = list(anchor)

            if all(self._anchor_empty(path, scope_el, subs, mapping_lookup)
                   for path in anchor_paths):
                return True
        return False

    def _anchor_empty(
        self,
        field_path: str,
        scope_el: Tag,
        subs: Dict[str, int],
        mapping_lookup: Dict[str, Dict],
    ) -> bool:
        """True if the anchor field's substituted selector yields no content."""
        mapping = mapping_lookup.get(field_path)
        if not mapping:
            return True
        sel = mapping.get("selector")
        if not sel:
            return True
        # url_regex anchors are page-scoped, not iteration-scoped — never empty
        # from iteration perspective.
        if mapping.get("url_regex"):
            return False
        try:
            el = scope_el.select_one(_resolve_selector(sel, subs))
        except Exception:
            return True
        if el is None:
            return True
        text = el.get_text(strip=True)
        # &nbsp; (U+00A0) collapses to non-empty — treat it as empty.
        return text == "" or text == "\xa0"

    def _extract_record(
        self,
        scope_el: Tag,
        schema_tree: List[Dict],
        mapping_lookup: Dict[str, Dict],
        boundaries: Dict[str, Dict],
        parent_path: str,
        page_url: str,
        job_id: str,
        index_subs: Optional[Dict[str, int]] = None,
        style_resolver: Optional["_StyleResolver"] = None,
    ) -> Dict[str, Any]:
        """Extract a single record from a DOM scope element."""
        record: Dict[str, Any] = {}
        subs = index_subs or {}

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
                nested_iterators = boundary_config.get("iterators", [])

                if is_array:
                    # Collection: iterate over elements
                    record[field_name] = self._extract_collection(
                        scope_el, boundary_selector, iterator_selector,
                        nested_iterators, children, mapping_lookup, boundaries,
                        field_path, page_url, job_id, subs, style_resolver,
                    )
                else:
                    # Nested record: narrow scope
                    if boundary_selector:
                        nested_el = scope_el.select_one(_resolve_selector(boundary_selector, subs))
                        if not nested_el:
                            record[field_name] = {}
                            continue
                    else:
                        nested_el = scope_el

                    record[field_name] = self._extract_record(
                        nested_el, children, mapping_lookup, boundaries,
                        field_path, page_url, job_id, subs, style_resolver,
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
                style_property = mapping.get("style_property")

                # Source priority: url_regex > style_property > attribute > text.
                # UI enforces mutual exclusion (each input disables the others
                # when it has content); this ordering only matters for stale
                # data or manual edits that leave more than one populated.
                if url_regex:
                    value = self._extract_from_url(page_url, url_regex, field_type)
                    if is_array:
                        # Single-capture regex yields one scalar; an array
                        # field expects a list. Wrap it (or empty-list a miss)
                        # so consumers that iterate the field don't get a
                        # character-by-character explosion of the string.
                        record[field_name] = [value] if value is not None else []
                    else:
                        record[field_name] = value
                elif style_property:
                    record[field_name] = self._extract_style(
                        scope_el, selector, style_property, style_resolver,
                        page_url, job_id, is_array, subs,
                    )
                elif is_array:
                    record[field_name] = self._extract_leaf_array(
                        scope_el, selector, attribute, field_type,
                        page_url, job_id, subs,
                    )
                else:
                    record[field_name] = self._extract_leaf(
                        scope_el, selector, attribute, field_type,
                        page_url, job_id, subs,
                    )

        return record

    def _extract_collection(
        self,
        scope_el: Tag,
        boundary_selector: Optional[str],
        iterator_selector: Optional[str],
        nested_iterators: List[Dict[str, Any]],
        children: List[Dict],
        mapping_lookup: Dict[str, Dict],
        boundaries: Dict[str, Dict],
        field_path: str,
        page_url: str,
        job_id: str,
        index_subs: Dict[str, int],
        style_resolver: Optional["_StyleResolver"] = None,
    ) -> List[Dict[str, Any]]:
        """Extract a collection (array of records) from the DOM.

        Three modes, in priority order:
          1. nested_iterators set — fan out via _iter_records (new primitive)
          2. iterator_selector set — repeating-element selector (existing)
          3. neither — treat container as a single element
        """
        # Determine the scope for iteration
        if boundary_selector:
            container = scope_el.select_one(_resolve_selector(boundary_selector, index_subs))
            if not container:
                return []
        else:
            container = scope_el

        # New: iterator-based fan-out (column-projected layouts etc.)
        if nested_iterators:
            return self._iter_records(
                container, nested_iterators, children, mapping_lookup,
                boundaries, field_path, page_url, job_id, index_subs, style_resolver,
            )

        # Existing: repeating-element selector
        if iterator_selector:
            elements = container.select(_resolve_selector(iterator_selector, index_subs))
        else:
            # No iterator — treat the container itself as a single element
            elements = [container]

        items = []
        for el in elements:
            item = self._extract_record(
                el, children, mapping_lookup, boundaries,
                field_path, page_url, job_id, index_subs, style_resolver,
            )
            items.append(item)

        return items

    def _extract_style(
        self,
        scope_el: Tag,
        selector: Optional[str],
        property_name: str,
        style_resolver: Optional["_StyleResolver"],
        page_url: str,
        job_id: str,
        is_array: bool,
        index_subs: Optional[Dict[str, int]] = None,
    ) -> Any:
        """Resolve a CSS property value for the element matched by `selector`.

        Lookup is first-match across (element style attr → page <style>
        blocks → external stylesheets), keyed on exact selector-text equality.

        url-bearing values (background-image, list-style-image, mask-image,
        cursor, etc.) are unwrapped into the individual URL strings, each
        verified through `_verify_asset_ref` exactly like `<img src>` goes
        through it, and emitted verbatim — same `../assets/<file>` form
        page_storage and the crawler's CSS postprocess both write. Scalar
        fields return the first verified URL; array fields return all of
        them. URLs the scraper didn't rewrite to `../assets/` (external,
        protocol-relative, unrewritten relative) aren't ours to vouch for
        and drop under the strict no-leakage policy.

        Non-url values (`#ff0000`, `12px`, `10px 20px`) pass through.
        """
        empty = [] if is_array else None
        if not style_resolver or not property_name:
            return empty
        subs = index_subs or {}
        if selector:
            parts = [s.strip() for s in selector.split("|")]
            el = None
            picked = selector
            for part in parts:
                try:
                    el = scope_el.select_one(_resolve_selector(part, subs))
                except Exception:
                    continue
                if el:
                    picked = part
                    break
            if not el:
                return empty
        else:
            el = scope_el
            picked = None
        # Use the iterator-substituted form of the picked selector when
        # looking up stylesheet rules. CSS rules are written in concrete
        # form, not with {i} placeholders.
        lookup_sel = _resolve_selector(picked, subs) if picked else None
        raw = style_resolver.resolve(el, lookup_sel, property_name)
        if raw is None:
            return empty

        urls = _urls_in_value(raw)
        if not urls:
            # Non-url value (color, padding, etc.) — pass through.
            return [raw] if is_array else raw

        verified = []
        for u in urls:
            result = self._verify_asset_ref(u, job_id, page_url)
            if result is None or result is _MISSING_ASSET:
                continue
            verified.append(u)
        if is_array:
            return verified
        return verified[0] if verified else None

    def _verify_asset_ref(self, ref: str, job_id: str, page_url: str = "") -> Any:
        """Verify a scraper-rewritten `../assets/<file>` reference exists on disk.

        Returns:
          - the cleaned relative filename (suitable for joining with `assets/`
            or other output prefixes) when the file is present;
          - `_MISSING_ASSET` when the prefix matches but the file isn't there;
          - `None` when the input isn't in `../assets/` form at all.

        Centralizes the disk-check policy so leaf-text extraction and
        style-property extraction share one implementation (and one log
        message format). Callers map the three return shapes to whatever
        output their field type needs.
        """
        if not ref or not ref.startswith("../assets/"):
            return None
        rel = ref[len("../assets/"):]
        # On-disk filenames are URL-derived; query/fragment never reach disk.
        rel_clean = rel.split("?", 1)[0].split("#", 1)[0]
        asset_path = self.data_dir / "jobs" / job_id / "assets" / rel_clean
        if not asset_path.is_file():
            logger.warning(
                "Skipping missing asset on %s: %s (expected at %s)",
                page_url, ref, asset_path,
            )
            return _MISSING_ASSET
        return rel_clean

    def _extract_leaf(
        self,
        scope_el: Tag,
        selector: Optional[str],
        attribute: Optional[str],
        field_type: str,
        page_url: str,
        job_id: str,
        index_subs: Optional[Dict[str, int]] = None,
    ) -> Any:
        """Extract a single leaf value from the DOM."""
        subs = index_subs or {}
        if selector:
            # Support disjunctions: "a.link | span.alt"
            parts = [s.strip() for s in selector.split("|")]
            el = None
            for part in parts:
                try:
                    el = scope_el.select_one(_resolve_selector(part, subs))
                except Exception:
                    continue
                if el:
                    break
            if not el:
                return None
        else:
            el = scope_el

        value = self._extract_value(el, attribute, field_type, page_url, job_id)
        # Scalar context: a missing asset becomes a null field, not the
        # internal sentinel. Sentinel only travels back to array callers.
        if value is _MISSING_ASSET:
            return None
        return value

    def _extract_leaf_array(
        self,
        scope_el: Tag,
        selector: Optional[str],
        attribute: Optional[str],
        field_type: str,
        page_url: str,
        job_id: str,
        index_subs: Optional[Dict[str, int]] = None,
    ) -> List[Any]:
        """Extract multiple leaf values (array of primitives).

        Smart heuristic: when extracting an array of <td> cells from a row
        inside a table with a thead, the result is enriched to
        [{"header": <column-header>, "value": <cell-value>}, ...] instead of
        a flat list. Falls back to flat list when the heuristic doesn't apply.
        """
        if not selector:
            return []
        subs = index_subs or {}
        parts = [s.strip() for s in selector.split("|")]
        elements = []
        for part in parts:
            try:
                elements.extend(scope_el.select(_resolve_selector(part, subs)))
            except Exception:
                continue

        # Smart-stats: enrich with column headers when applicable
        headers = self._table_headers_for_row(scope_el, elements)
        if headers is not None:
            results = []
            for el in elements:
                header = headers.get(self._cell_index(el))
                value = self._extract_value(el, attribute, field_type, page_url, job_id)
                if value is _MISSING_ASSET:
                    # Drop the row entirely — a missing asset doesn't
                    # belong in the output as a null cell either.
                    continue
                results.append({"header": header, "value": value})
            return results

        values = [
            self._extract_value(el, attribute, field_type, page_url, job_id)
            for el in elements
        ]
        # Drop missing-asset sentinels from the array. None is preserved
        # so callers can distinguish "DOM matched, value was empty" from
        # "DOM matched, asset missing" — only the latter is filtered.
        return [v for v in values if v is not _MISSING_ASSET]

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
            raw = re.sub(r"\s+", " ", el.get_text(separator=" ", strip=True))

        if not raw:
            return None

        # Disk-check ../assets/<name> references regardless of field
        # type. The scraper writes this prefix into HTML for every
        # <img>/<link>/style URL it rewrites — whether or not the asset
        # was successfully saved. Sources of divergence: silently-skipped
        # dedup hits, blocked filters, fetch failures, or simply
        # references that didn't exist on the source server. The
        # extractor must verify the file exists on disk before emitting
        # the reference; otherwise consumers see dangling URLs that 404.
        raw_str = str(raw)
        if self._verify_asset_ref(raw_str, job_id, page_url) is _MISSING_ASSET:
            return _MISSING_ASSET

        if field_type == "number":
            # Extract numeric value from text
            cleaned = re.sub(r"[^\d.\-]", "", str(raw))
            try:
                return float(cleaned) if "." in cleaned else int(cleaned)
            except (ValueError, TypeError):
                return None

        elif field_type == "image":
            # Resolve to a fetchable URL. `../assets/<name>` is the
            # scraper's rewrite for downloaded assets; existence on disk
            # was already verified above. We translate to the gateway's
            # asset endpoint so consumers can fetch without knowing the
            # on-disk layout.
            src = raw_str
            if src.startswith("../assets/"):
                return src.replace("../assets/", f"/api/asset/{job_id}/assets/")
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
