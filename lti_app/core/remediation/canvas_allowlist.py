"""Canvas Rich Content Editor HTML allowlist constants.

Single source of truth for Canvas RCE constraints. Used by the AI prompt
builder and the Canvas HTML validator.

Derived from the Canvas LMS source (github.com/instructure/canvas-lms):
- TinyMCE valid_elements config in packages/canvas-rce/src/defaultTinymceConfig.ts
- CanvasSanitize sanitizer in lib/canvas_sanitize.rb
- Element denylist in packages/canvas-rce/src/rce/elementDenylist.ts

Key behaviours:
- <h1> is reserved for Canvas page titles; import auto-demotes to <h2>.
- <figure>/<figcaption> are stripped on save. The validator converts them
  to <div role="group"> + <p> with aria-label to preserve semantics.
- <style> blocks are stripped; only inline style= attributes survive.
- Custom CSS classes survive the allowlist but Canvas does NOT ship
  matching stylesheets, so class-based styling is effectively invisible.
  Use inline styles for all visual presentation.
"""

# Tags Canvas RCE allows in page content
CANVAS_ALLOWED_TAGS: set[str] = {
    "a", "abbr", "acronym", "address", "article", "aside", "audio",
    "b", "bdi", "bdo", "big", "blockquote", "br",
    "caption", "cite", "code", "col", "colgroup",
    "dd", "del", "details", "dfn", "div", "dl", "dt",
    "em",
    "h2", "h3", "h4", "h5", "h6", "hr",
    "i", "iframe", "img", "ins",
    "kbd",
    "li",
    "mark", "menu",
    "nav",
    "ol",
    "p", "picture", "pre",
    "q",
    "rp", "rt", "ruby",
    "s", "samp", "section", "small", "source", "span",
    "strong", "sub", "summary", "sup",
    "table", "tbody", "td", "tfoot", "th", "thead", "time", "tr",
    "u", "ul",
    "var", "video",
    "wbr",
}

# Tags Canvas explicitly forbids or strips during save
CANVAS_FORBIDDEN_TAGS: set[str] = {
    "h1",          # Canvas reserves H1 for the page title
    "figure",      # Stripped by Canvas RCE
    "figcaption",  # Stripped with figure
    "style",       # Inline <style> blocks
    "script",      # All script elements
    "form",        # Form elements
    "input",
    "select",
    "textarea",
    "button",
    "link",        # <link> stylesheet references
    "meta",        # Meta tags in body
    "object",
    "embed",
    "applet",
    "font",        # Deprecated
    "center",      # Deprecated
    "blink",       # Deprecated
    "marquee",     # Deprecated
}

# CSS properties Canvas allows in inline style attributes
CANVAS_ALLOWED_CSS_PROPERTIES: set[str] = {
    "background", "background-color", "background-image", "background-position",
    "background-repeat", "background-size",
    "border", "border-bottom", "border-collapse", "border-color",
    "border-left", "border-radius", "border-right", "border-spacing",
    "border-style", "border-top", "border-width",
    "box-shadow",
    "clear", "clip", "color", "cursor",
    "direction", "display",
    "flex", "flex-basis", "flex-direction", "flex-flow", "flex-grow",
    "flex-shrink", "flex-wrap",
    "float", "font", "font-family", "font-size", "font-style",
    "font-variant", "font-weight",
    "gap",
    "grid", "grid-area", "grid-column", "grid-gap", "grid-row",
    "grid-template", "grid-template-areas", "grid-template-columns",
    "grid-template-rows",
    "height",
    "justify-content",
    "left", "letter-spacing", "line-height", "list-style",
    "list-style-type",
    "margin", "margin-bottom", "margin-left", "margin-right", "margin-top",
    "max-height", "max-width", "min-height", "min-width",
    "opacity", "outline", "overflow",
    "padding", "padding-bottom", "padding-left", "padding-right",
    "padding-top",
    "position",
    "right",
    "table-layout", "text-align", "text-decoration", "text-indent",
    "text-overflow", "text-shadow", "text-transform", "top",
    "vertical-align", "visibility",
    "white-space", "width", "word-break", "word-spacing", "word-wrap",
    "z-index",
}

# ARIA attributes Canvas supports
CANVAS_ALLOWED_ARIA_ATTRIBUTES: set[str] = {
    "aria-describedby",
    "aria-hidden",
    "aria-label",
    "aria-labelledby",
    "aria-live",
    "aria-relevant",
    "aria-atomic",
    "role",
}

# Standard ARIA role values that are safe in Canvas content
CANVAS_ALLOWED_ROLE_VALUES: set[str] = {
    # Landmark roles
    "banner", "complementary", "contentinfo", "main", "navigation", "region", "search",
    # Widget roles
    "alert", "dialog", "group", "img", "list", "listitem",
    "presentation", "none", "tab", "tablist", "tabpanel",
    # Document structure
    "cell", "columnheader", "row", "rowgroup", "rowheader", "table",
    "heading", "separator", "toolbar",
}

# Global HTML attributes Canvas allows on most elements
CANVAS_ALLOWED_GLOBAL_ATTRIBUTES: set[str] = {
    "id", "class", "style", "title", "lang", "dir",
    "tabindex", "data-*",
}

# Per-element allowed attributes (beyond globals and ARIA)
CANVAS_ELEMENT_ATTRIBUTES: dict[str, set[str]] = {
    "a": {"href", "target", "name", "rel"},
    "img": {"src", "alt", "width", "height"},
    "iframe": {"src", "width", "height", "title", "allow", "allowfullscreen", "frameborder", "sandbox", "data-media-id", "data-media-type"},
    "video": {"src", "controls", "width", "height", "poster", "preload"},
    "audio": {"src", "controls", "preload"},
    "source": {"src", "type"},
    "td": {"colspan", "rowspan", "headers"},
    "th": {"scope", "colspan", "rowspan", "headers"},
    "col": {"span"},
    "colgroup": {"span"},
    "ol": {"type", "start", "reversed"},
    "ul": set(),
    "li": {"value"},
    "table": {"border", "cellpadding", "cellspacing", "summary"},
    "caption": set(),
    "thead": set(),
    "tbody": set(),
    "tfoot": set(),
    "tr": set(),
    "blockquote": {"cite"},
    "q": {"cite"},
    "del": {"cite", "datetime"},
    "ins": {"cite", "datetime"},
    "time": {"datetime"},
    "details": {"open"},
    "abbr": set(),
}
