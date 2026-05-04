"""Generate Canvas custom JS/CSS for inline accessibility score icons."""


def generate_score_badge_js(api_base_url: str) -> str:
    """Generate JavaScript that shows accessibility score badges in Canvas."""
    return f'''
// Remedy Canvas LTI Accessibility Score Badges
(function() {{
  const API_BASE = "{api_base_url}";

  async function fetchScore(courseId) {{
    try {{
      const resp = await fetch(API_BASE + "/api/courses/" + courseId + "/report");
      if (!resp.ok) return null;
      return await resp.json();
    }} catch {{ return null; }}
  }}

  function createBadge(score) {{
    const badge = document.createElement("span");
    badge.className = "clu-a11y-badge";
    badge.textContent = score + "%";
    badge.style.cssText = "display:inline-block;padding:2px 6px;border-radius:10px;font-size:11px;font-weight:600;margin-left:6px;";
    if (score >= 80) badge.style.cssText += "background:#dcfce7;color:#166534;";
    else if (score >= 50) badge.style.cssText += "background:#fef9c3;color:#854d0e;";
    else badge.style.cssText += "background:#fecaca;color:#991b1b;";
    return badge;
  }}

  // Run on course pages
  const courseLinks = document.querySelectorAll("a[href*='/courses/']");
  const seen = new Set();
  for (const link of courseLinks) {{
    const match = link.href.match(/\\/courses\\/(\\d+)/);
    if (!match || seen.has(match[1])) continue;
    seen.add(match[1]);
    fetchScore(match[1]).then(report => {{
      if (report && report.score != null) {{
        link.parentElement.appendChild(createBadge(report.score));
      }}
    }});
  }}
}})();
'''.strip()


def generate_score_badge_css() -> str:
    """Generate CSS for accessibility score badges."""
    return '''
.clu-a11y-badge { transition: opacity 0.2s; cursor: help; }
.clu-a11y-badge:hover { opacity: 0.8; }
'''.strip()
