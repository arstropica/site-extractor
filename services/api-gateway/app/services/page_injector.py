"""
Page injector — injects a picker script into served HTML pages.

The injected script handles:
  - Hover highlighting (overlay via getBoundingClientRect)
  - Click capture → CSS selector generation → postMessage to parent
  - Highlight-all-matches mode (parent sends selector → script highlights)
"""

PICKER_SCRIPT = """
<script data-extractor-picker>
(function() {
  var overlay = null;
  var highlights = [];
  var pickerActive = false;

  function createOverlay() {
    if (overlay) return overlay;
    overlay = document.createElement('div');
    overlay.id = '__extractor_overlay';
    overlay.style.cssText =
      'position:fixed;pointer-events:none;z-index:2147483647;' +
      'border:2px solid #3b82f6;background:rgba(59,130,246,0.1);transition:all 0.1s ease;';
    document.body.appendChild(overlay);
    return overlay;
  }

  function positionOverlay(el) {
    var r = el.getBoundingClientRect();
    var o = createOverlay();
    o.style.left = r.left + 'px';
    o.style.top = r.top + 'px';
    o.style.width = r.width + 'px';
    o.style.height = r.height + 'px';
    o.style.display = 'block';
  }

  function hideOverlay() {
    if (overlay) overlay.style.display = 'none';
  }

  function generateSelector(el) {
    if (el.id) return '#' + CSS.escape(el.id);

    var parts = [];
    var current = el;
    while (current && current !== document.body && parts.length < 5) {
      var seg = current.tagName.toLowerCase();
      if (current.id) {
        parts.unshift('#' + CSS.escape(current.id) + ' > ' + seg);
        break;
      }
      if (current.classList && current.classList.length > 0) {
        var classes = Array.from(current.classList)
          .filter(function(c) { return !c.startsWith('__extractor'); })
          .slice(0, 3)
          .map(function(c) { return '.' + CSS.escape(c); })
          .join('');
        if (classes) {
          seg += classes;
          // Check uniqueness within parent
          var parent = current.parentElement;
          if (parent && parent.querySelectorAll(seg).length === 1) {
            parts.unshift(seg);
            // Check if unique from body
            if (document.querySelectorAll(parts.join(' > ')).length === 1) break;
            current = parent;
            continue;
          }
        }
      }
      // Add nth-child if needed
      if (current.parentElement) {
        var siblings = Array.from(current.parentElement.children).filter(
          function(s) { return s.tagName === current.tagName; }
        );
        if (siblings.length > 1) {
          var idx = siblings.indexOf(current) + 1;
          seg += ':nth-of-type(' + idx + ')';
        }
      }
      parts.unshift(seg);
      current = current.parentElement;
    }
    return parts.join(' > ');
  }

  function clearHighlights() {
    highlights.forEach(function(h) { h.remove(); });
    highlights = [];
  }

  function highlightMatches(selector) {
    clearHighlights();
    try {
      var els = document.querySelectorAll(selector);
      els.forEach(function(el) {
        var r = el.getBoundingClientRect();
        var h = document.createElement('div');
        h.className = '__extractor_highlight';
        h.style.cssText =
          'position:fixed;pointer-events:none;z-index:2147483646;' +
          'border:2px solid #10b981;background:rgba(16,185,129,0.15);' +
          'left:' + r.left + 'px;top:' + r.top + 'px;' +
          'width:' + r.width + 'px;height:' + r.height + 'px;';
        document.body.appendChild(h);
        highlights.push(h);
      });
      return els.length;
    } catch(e) {
      return 0;
    }
  }

  // Listen for messages from parent
  window.addEventListener('message', function(e) {
    var msg = e.data;
    if (!msg || !msg.type) return;

    if (msg.type === 'PICKER_ENABLE') {
      pickerActive = true;
      document.body.style.cursor = 'crosshair';
    }
    else if (msg.type === 'PICKER_DISABLE') {
      pickerActive = false;
      document.body.style.cursor = '';
      hideOverlay();
    }
    else if (msg.type === 'HIGHLIGHT_SELECTOR') {
      var count = highlightMatches(msg.selector || '');
      window.parent.postMessage({
        type: 'HIGHLIGHT_RESULT',
        selector: msg.selector,
        matchCount: count
      }, '*');
    }
    else if (msg.type === 'CLEAR_HIGHLIGHTS') {
      clearHighlights();
    }
  });

  // Hover handler
  document.addEventListener('mouseover', function(e) {
    if (!pickerActive) return;
    e.stopPropagation();
    positionOverlay(e.target);
  }, true);

  document.addEventListener('mouseout', function(e) {
    if (!pickerActive) return;
    hideOverlay();
  }, true);

  // Click handler
  document.addEventListener('click', function(e) {
    if (!pickerActive) return;
    e.preventDefault();
    e.stopPropagation();

    var selector = generateSelector(e.target);
    var text = (e.target.textContent || '').trim().substring(0, 200);
    var tag = e.target.tagName.toLowerCase();
    var attrs = {};
    if (e.target.src) attrs.src = e.target.src;
    if (e.target.href) attrs.href = e.target.href;
    if (e.target.alt) attrs.alt = e.target.alt;

    window.parent.postMessage({
      type: 'ELEMENT_SELECTED',
      selector: selector,
      text: text,
      tag: tag,
      attributes: attrs,
    }, '*');
  }, true);

  // Intercept link navigation to stay in the mapper
  document.addEventListener('click', function(e) {
    if (pickerActive) return; // handled above
    var a = e.target.closest('a[href]');
    if (a) {
      e.preventDefault();
      window.parent.postMessage({
        type: 'NAVIGATE_REQUEST',
        url: a.href,
      }, '*');
    }
  }, true);

  // Signal ready
  window.parent.postMessage({ type: 'PICKER_READY' }, '*');
})();
</script>
"""


def inject_picker(html: str) -> str:
    """Inject the picker script into HTML before the closing </body> tag."""
    if '</body>' in html:
        return html.replace('</body>', PICKER_SCRIPT + '</body>')
    elif '</html>' in html:
        return html.replace('</html>', PICKER_SCRIPT + '</html>')
    else:
        return html + PICKER_SCRIPT
