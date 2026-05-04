"""Canvas page HTML injector for YouTube caption overlay."""

import re
from bs4 import BeautifulSoup
import structlog

_logger = structlog.get_logger(__name__)


def _get_overlay_css() -> str:
    """Full CSS ported from youtube-caption-overlay.css."""
    return """
/* YouTube Caption Overlay — CLU Captions */

/* Container wrapping the YouTube iframe. */
.yco-container {
  position: relative;
  display: inline-block;
  width: 100%;
}

/* Caption display overlay — positioned over the bottom of the iframe. */
.yco-caption-display {
  position: absolute;
  bottom: 60px;
  left: 50%;
  transform: translateX(-50%);
  max-width: 90%;
  text-align: center;
  pointer-events: none;
  z-index: 10;
}

/* Caption text span with configurable styles via custom properties. */
.yco-caption-text {
  font-size: var(--yco-font-size, 1.2em);
  background: var(--yco-background, rgba(0, 0, 0, 0.75));
  color: var(--yco-color, #FFFFFF);
  padding: 4px 8px;
  line-height: 1.4;
  box-decoration-break: clone;
  -webkit-box-decoration-break: clone;
  border-radius: 3px;
}

/* Empty caption text should not display the background. */
.yco-caption-text:empty {
  display: none;
}

/* Controls bar below the video. */
.yco-controls {
  display: flex;
  gap: 8px;
  padding: 6px 0;
}

/* CC toggle button. */
.yco-cc-toggle {
  background: #333;
  color: #fff;
  border: 2px solid transparent;
  padding: 4px 10px;
  font-size: 14px;
  font-weight: bold;
  cursor: pointer;
  border-radius: 3px;
  font-family: inherit;
}

.yco-cc-toggle:hover {
  background: #555;
}

.yco-cc-toggle:focus-visible {
  outline: 2px solid #4A90D9;
  outline-offset: 2px;
}

/* Active state — captions are on. */
.yco-cc-toggle.yco-cc-active {
  border-color: #fff;
}

/* Transcript toggle button. */
.yco-transcript-toggle {
  background: #333;
  color: #fff;
  border: 2px solid transparent;
  padding: 4px 10px;
  font-size: 14px;
  cursor: pointer;
  border-radius: 3px;
  font-family: inherit;
}

.yco-transcript-toggle:hover {
  background: #555;
}

.yco-transcript-toggle:focus-visible {
  outline: 2px solid #4A90D9;
  outline-offset: 2px;
}

.yco-transcript-toggle[aria-pressed="true"] {
  border-color: #fff;
}

/* Transcript panel. */
.yco-transcript {
  max-height: 200px;
  overflow-y: auto;
  border: 1px solid #ddd;
  border-radius: 3px;
  padding: 8px;
  margin-top: 4px;
  background: #fafafa;
}

/* Individual transcript cue. */
.yco-transcript-cue {
  display: flex;
  gap: 12px;
  padding: 4px 6px;
  border-radius: 3px;
  transition: background-color 0.2s ease;
}

/* Timestamp in transcript. */
.yco-transcript-time {
  color: #666;
  font-size: 0.85em;
  font-family: monospace;
  white-space: nowrap;
  min-width: 40px;
}

/* Caption text in transcript. */
.yco-transcript-text {
  flex: 1;
  font-size: 0.9em;
}

/* Active cue highlighting. */
.yco-transcript-cue.yco-transcript-active {
  background-color: #e8f0fe;
  font-weight: 600;
}

/* --- Fullscreen support --- */
.yco-container:fullscreen {
  width: 100vw;
  height: 100vh;
  background: #000;
}

.yco-container:fullscreen iframe {
  width: 100%;
  height: 100%;
}

/* --- High contrast mode --- */
@media (prefers-contrast: high) {
  .yco-caption-text {
    background: #000;
    color: #fff;
    border: 2px solid #fff;
  }

  .yco-cc-toggle,
  .yco-transcript-toggle {
    border: 2px solid #fff;
  }

  .yco-transcript-cue.yco-transcript-active {
    background-color: #000;
    color: #fff;
    outline: 2px solid #fff;
  }
}

/* --- Reduced motion --- */
@media (prefers-reduced-motion: reduce) {
  .yco-transcript-cue {
    transition: none;
  }
}
"""


def _get_overlay_js(video_id: str, vtt_url: str) -> str:
    """Self-contained IIFE ported from youtube-caption-overlay.js + vtt-parser.js."""
    # Use {{ and }} for literal JS braces in f-string
    return f"""
(function () {{
  "use strict";

  var VIDEO_ID = "{video_id}";
  var VTT_URL = "{vtt_url}";
  var SHOW_TRANSCRIPT = true;

  // --- State ---
  var iframeIdCounter = 0;
  var ytApiReady = false;
  var ytApiQueue = [];

  // ============================================================
  // VTT Parser (ported from vtt-parser.js, Drupal namespace stripped)
  // ============================================================

  /**
   * Parses a VTT timestamp into seconds.
   * Accepts MM:SS.mmm or HH:MM:SS.mmm format.
   */
  function parseTimestamp(timestamp) {{
    var parts = timestamp.trim().split(':');
    if (parts.length === 2) {{
      var minutes = parseFloat(parts[0]) || 0;
      var seconds = parseFloat(parts[1]) || 0;
      return minutes * 60 + seconds;
    }}
    var hours = parseFloat(parts[0]) || 0;
    var mins = parseFloat(parts[1]) || 0;
    var secs = parseFloat(parts[2]) || 0;
    return hours * 3600 + mins * 60 + secs;
  }}

  /**
   * Strips VTT inline tags from caption text.
   * Removes tags like <v Speaker>, <c.classname>, <b>, <i>, etc.
   */
  function stripVttTags(text) {{
    return text.replace(/<\\/?[^>]+>/g, '');
  }}

  /**
   * Parses VTT content into an array of cue objects.
   */
  function parseVTT(vttContent) {{
    var cues = [];

    // Normalize line endings.
    var content = vttContent.replace(/\r\n/g, '\n').replace(/\r/g, '\n');

    // Strip WEBVTT header and any metadata before the first blank line.
    var headerEnd = content.indexOf('\n\n');
    if (headerEnd !== -1) {{
      content = content.substring(headerEnd + 2);
    }}

    // Split into blocks by double newlines.
    var blocks = content.split(/\n\n+/);

    for (var i = 0; i < blocks.length; i++) {{
      var block = blocks[i].trim();
      if (!block) {{
        continue;
      }}

      var lines = block.split('\n');

      // Find the line containing the timestamp arrow.
      var timestampLineIndex = -1;
      for (var j = 0; j < lines.length; j++) {{
        if (lines[j].indexOf('-->') !== -1) {{
          timestampLineIndex = j;
          break;
        }}
      }}

      if (timestampLineIndex === -1) {{
        continue;
      }}

      // Parse timestamps. The timestamp line may have positioning info after
      // the end time (e.g., "00:00:05.000 --> 00:00:08.500 position:50%").
      var timestampLine = lines[timestampLineIndex];
      var arrowIndex = timestampLine.indexOf('-->');
      var startStr = timestampLine.substring(0, arrowIndex).trim();
      var afterArrow = timestampLine.substring(arrowIndex + 3).trim();

      // The end timestamp is the first space-delimited token after the arrow.
      var endParts = afterArrow.split(/\\s+/);
      var endStr = endParts[0];

      var start = parseTimestamp(startStr);
      var end = parseTimestamp(endStr);

      // Everything after the timestamp line is caption text.
      var textLines = lines.slice(timestampLineIndex + 1);
      var text = stripVttTags(textLines.join('\n').trim());

      if (text) {{
        cues.push({{ start: start, end: end, text: text }});
      }}
    }}

    // Sort by start time.
    cues.sort(function (a, b) {{ return a.start - b.start; }});

    return cues;
  }}

  // ============================================================
  // Helpers
  // ============================================================

  /**
   * Escapes HTML special characters to prevent XSS.
   */
  function escapeHtml(text) {{
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(text));
    return div.innerHTML;
  }}

  /**
   * Escapes special regex characters in a string.
   */
  function escapeRegExp(str) {{
    return str.replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&');
  }}

  /**
   * Formats seconds into a timestamp string (MM:SS).
   */
  function formatTimestamp(seconds) {{
    var mins = Math.floor(seconds / 60);
    var secs = Math.floor(seconds % 60);
    return (mins < 10 ? '0' : '') + mins + ':' + (secs < 10 ? '0' : '') + secs;
  }}

  // ============================================================
  // YouTube IFrame API
  // ============================================================

  /**
   * Loads the YouTube IFrame API if not already loaded.
   */
  function loadYouTubeApi() {{
    // Already loaded and ready.
    if (window.YT && window.YT.Player) {{
      ytApiReady = true;
      processQueue();
      return;
    }}

    // Already loading (script tag exists).
    if (document.querySelector('script[src*="youtube.com/iframe_api"]')) {{
      return;
    }}

    // Preserve any existing callback.
    var existingCallback = window.onYouTubeIframeAPIReady;

    window.onYouTubeIframeAPIReady = function () {{
      ytApiReady = true;
      if (typeof existingCallback === 'function') {{
        existingCallback();
      }}
      processQueue();
    }};

    var tag = document.createElement('script');
    tag.src = 'https://www.youtube.com/iframe_api';
    var firstScript = document.getElementsByTagName('script')[0];
    firstScript.parentNode.insertBefore(tag, firstScript);
  }}

  /**
   * Processes the queue of pending initializations.
   */
  function processQueue() {{
    while (ytApiQueue.length > 0) {{
      var fn = ytApiQueue.shift();
      fn();
    }}
  }}

  /**
   * Ensures an iframe has enablejsapi=1 and origin parameters.
   */
  function ensureApiParams(iframe) {{
    var src = iframe.src;
    if (!src) {{
      return;
    }}

    var modified = false;

    if (src.indexOf('enablejsapi=1') === -1) {{
      src += (src.indexOf('?') === -1 ? '?' : '&') + 'enablejsapi=1';
      modified = true;
    }}

    if (src.indexOf('origin=') === -1) {{
      src += '&origin=' + encodeURIComponent(window.location.origin);
      modified = true;
    }}

    if (modified) {{
      iframe.src = src;
    }}
  }}

  // ============================================================
  // DOM Construction
  // ============================================================

  /**
   * Creates the caption overlay DOM structure around an iframe.
   * Returns an object with references to created DOM elements.
   */
  function wrapIframe(iframe, config) {{
    // Create container.
    var container = document.createElement('div');
    container.className = 'yco-container';

    // Apply custom properties.
    container.style.setProperty('--yco-font-size', config.fontSize);
    container.style.setProperty('--yco-background', config.background);
    container.style.setProperty('--yco-color', config.color);

    // Insert container before iframe and move iframe inside.
    iframe.parentNode.insertBefore(container, iframe);
    container.appendChild(iframe);

    // Create caption display overlay.
    var captionDisplay = document.createElement('div');
    captionDisplay.className = 'yco-caption-display';
    captionDisplay.setAttribute('aria-live', 'polite');
    captionDisplay.setAttribute('aria-atomic', 'true');
    captionDisplay.setAttribute('role', 'status');
    captionDisplay.setAttribute('aria-label', 'Video captions');

    var captionText = document.createElement('span');
    captionText.className = 'yco-caption-text';
    captionDisplay.appendChild(captionText);
    container.appendChild(captionDisplay);

    // Create controls bar.
    var controls = document.createElement('div');
    controls.className = 'yco-controls';

    // CC toggle button.
    var ccToggle = document.createElement('button');
    ccToggle.className = 'yco-cc-toggle yco-cc-active';
    ccToggle.setAttribute('type', 'button');
    ccToggle.setAttribute('role', 'button');
    ccToggle.setAttribute('aria-pressed', 'true');
    ccToggle.setAttribute('aria-label', 'Toggle captions');
    ccToggle.textContent = 'CC';
    controls.appendChild(ccToggle);

    // Track caption visibility state.
    var captionsVisible = true;

    ccToggle.addEventListener('click', function () {{
      captionsVisible = !captionsVisible;
      captionDisplay.style.visibility = captionsVisible ? 'visible' : 'hidden';
      ccToggle.setAttribute('aria-pressed', captionsVisible ? 'true' : 'false');
      ccToggle.classList.toggle('yco-cc-active', captionsVisible);
    }});

    // Transcript panel (optional).
    var transcriptPanel = null;
    var transcriptToggle = null;

    if (config.showTranscript) {{
      transcriptToggle = document.createElement('button');
      transcriptToggle.className = 'yco-transcript-toggle';
      transcriptToggle.setAttribute('type', 'button');
      transcriptToggle.setAttribute('aria-pressed', 'false');
      transcriptToggle.setAttribute('aria-label', 'Toggle transcript');
      transcriptToggle.textContent = 'Transcript';
      controls.appendChild(transcriptToggle);

      transcriptPanel = document.createElement('div');
      transcriptPanel.className = 'yco-transcript';
      transcriptPanel.setAttribute('role', 'log');
      transcriptPanel.setAttribute('aria-label', 'Video transcript');
      transcriptPanel.style.display = 'none';

      transcriptToggle.addEventListener('click', function () {{
        var isVisible = transcriptPanel.style.display !== 'none';
        transcriptPanel.style.display = isVisible ? 'none' : 'block';
        transcriptToggle.setAttribute('aria-pressed', isVisible ? 'false' : 'true');
      }});
    }}

    // Add controls after container.
    container.parentNode.insertBefore(controls, container.nextSibling);

    // Add transcript after controls.
    if (transcriptPanel) {{
      controls.parentNode.insertBefore(transcriptPanel, controls.nextSibling);
    }}

    return {{
      container: container,
      captionDisplay: captionDisplay,
      captionText: captionText,
      ccToggle: ccToggle,
      transcriptPanel: transcriptPanel,
      transcriptToggle: transcriptToggle
    }};
  }}

  /**
   * Builds the transcript panel from cues.
   * Returns an array of cue elements for highlighting.
   */
  function buildTranscript(panel, cues) {{
    var elements = [];

    for (var i = 0; i < cues.length; i++) {{
      var cueEl = document.createElement('div');
      cueEl.className = 'yco-transcript-cue';

      var time = document.createElement('span');
      time.className = 'yco-transcript-time';
      time.textContent = formatTimestamp(cues[i].start);

      var text = document.createElement('span');
      text.className = 'yco-transcript-text';
      text.textContent = cues[i].text;

      cueEl.appendChild(time);
      cueEl.appendChild(text);
      panel.appendChild(cueEl);
      elements.push(cueEl);
    }}

    return elements;
  }}

  // ============================================================
  // Caption Sync
  // ============================================================

  /**
   * Starts the caption sync loop for a player.
   */
  function startCaptionSync(player, cues, dom, transcriptElements) {{
    var syncInterval = null;
    var currentCueIndex = -1;

    player.addEventListener('onStateChange', function (event) {{
      if (event.data === YT.PlayerState.PLAYING) {{
        if (syncInterval) {{
          clearInterval(syncInterval);
        }}
        syncInterval = setInterval(function () {{
          var currentTime = player.getCurrentTime();
          var foundIndex = -1;

          for (var i = 0; i < cues.length; i++) {{
            if (currentTime >= cues[i].start && currentTime <= cues[i].end) {{
              foundIndex = i;
              break;
            }}
          }}

          if (foundIndex !== currentCueIndex) {{
            currentCueIndex = foundIndex;

            if (foundIndex >= 0) {{
              dom.captionText.innerHTML = escapeHtml(cues[foundIndex].text).replace(/\n/g, '<br>');
            }} else {{
              dom.captionText.innerHTML = '';
            }}

            if (transcriptElements.length > 0) {{
              for (var j = 0; j < transcriptElements.length; j++) {{
                transcriptElements[j].classList.remove('yco-transcript-active');
              }}
              if (foundIndex >= 0 && transcriptElements[foundIndex]) {{
                transcriptElements[foundIndex].classList.add('yco-transcript-active');
                transcriptElements[foundIndex].scrollIntoView({{
                  behavior: 'smooth',
                  block: 'center'
                }});
              }}
            }}
          }}
        }}, 100);
      }} else if (
        event.data === YT.PlayerState.PAUSED ||
        event.data === YT.PlayerState.ENDED
      ) {{
        if (syncInterval) {{
          clearInterval(syncInterval);
          syncInterval = null;
        }}
      }}
    }});
  }}

  /**
   * Fetches the VTT file, parses it, and starts overlay sync.
   */
  function fetchAndSync(iframe, config, dom) {{
    var xhr = new XMLHttpRequest();
    xhr.open('GET', config.captionUrl, true);
    xhr.onload = function () {{
      if (xhr.status !== 200) {{
        return;
      }}

      var cues = parseVTT(xhr.responseText);

      if (!cues || cues.length === 0) {{
        return;
      }}

      var transcriptElements = [];
      if (dom.transcriptPanel) {{
        transcriptElements = buildTranscript(dom.transcriptPanel, cues);
      }}

      var player = new YT.Player(iframe.id, {{
        events: {{
          onReady: function () {{
            startCaptionSync(player, cues, dom, transcriptElements);
          }}
        }}
      }});
    }};
    xhr.send();
  }}

  /**
   * Initializes the caption overlay for a single iframe + config pair.
   */
  function initOverlay(iframe, config) {{
    ensureApiParams(iframe);

    if (!iframe.id) {{
      iframe.id = 'yco-iframe-' + (++iframeIdCounter);
    }}

    var dom = wrapIframe(iframe, config);
    fetchAndSync(iframe, config, dom);
  }}

  // ============================================================
  // Init
  // ============================================================

  function init() {{
    loadYouTubeApi();

    var iframes = document.querySelectorAll('iframe');
    for (var i = 0; i < iframes.length; i++) {{
      var src = iframes[i].src || '';
      var pattern = new RegExp('youtube(?:-nocookie)?\\.com\\/embed\\/' + escapeRegExp(VIDEO_ID));
      if (pattern.test(src) && !iframes[i].closest('.yco-container')) {{
        var config = {{
          videoId: VIDEO_ID,
          captionUrl: VTT_URL,
          captionFormat: 'vtt',
          fontSize: '1.2em',
          background: 'rgba(0, 0, 0, 0.75)',
          color: '#FFFFFF',
          showTranscript: SHOW_TRANSCRIPT
        }};
        if (ytApiReady) {{
          initOverlay(iframes[i], config);
        }} else {{
          ytApiQueue.push((function (iframe, cfg) {{
            return function () {{ initOverlay(iframe, cfg); }};
          }})(iframes[i], config));
        }}
        break;
      }}
    }}
  }}

  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', init);
  }} else {{
    init();
  }}

}})();
"""


class OverlayInjector:
    """Inject/remove YouTube caption overlay into Canvas page HTML."""

    def inject(self, html: str, video_id: str, vtt_url: str) -> str:
        """Add caption overlay to page HTML containing a YouTube iframe."""
        soup = BeautifulSoup(html, "html.parser")

        iframe = self._find_youtube_iframe(soup, video_id)
        if not iframe:
            _logger.warning("inject_no_iframe", video_id=video_id)
            return html

        if soup.find("div", class_="clu-caption-marker", attrs={"data-video-id": video_id}):
            _logger.info("inject_already_present", video_id=video_id)
            return html

        # Add CSS if not already present on this page.
        if not soup.find("style", class_="yco-styles"):
            style = soup.new_tag("style")
            style["class"] = "yco-styles"
            style.string = _get_overlay_css()
            iframe.insert_before(style)

        # Add marker div (used to detect existing injection and for removal).
        marker = soup.new_tag("div")
        marker["class"] = "clu-caption-marker"
        marker["data-video-id"] = video_id
        marker["style"] = "display:none"
        iframe.insert_before(marker)

        # Add JS after iframe.
        script = soup.new_tag("script")
        script.string = _get_overlay_js(video_id, vtt_url)
        iframe.insert_after(script)

        _logger.info("overlay_injected", video_id=video_id)
        return str(soup)

    def remove(self, html: str, video_id: str) -> str:
        """Remove caption overlay from page HTML."""
        soup = BeautifulSoup(html, "html.parser")

        # Remove marker divs for this video.
        for marker in soup.find_all("div", class_="clu-caption-marker", attrs={"data-video-id": video_id}):
            marker.decompose()

        # Remove script tags that reference this video ID.
        for script in soup.find_all("script"):
            if script.string and f'"{video_id}"' in script.string:
                script.decompose()

        # Remove shared CSS only if no other overlays remain on this page.
        if not soup.find("div", class_="clu-caption-marker"):
            for style in soup.find_all("style", class_="yco-styles"):
                style.decompose()

        # Unwrap any yco-container divs that were injected by the JS at runtime.
        # (These won't normally be in the stored HTML, but handle them defensively.)
        for container in soup.find_all("div", class_="yco-container"):
            container.unwrap()

        _logger.info("overlay_removed", video_id=video_id)
        return str(soup)

    def _find_youtube_iframe(self, soup: BeautifulSoup, video_id: str):
        """Find the first YouTube iframe in soup that embeds the given video ID."""
        pattern = re.compile(
            rf'youtube(?:-nocookie)?\.com/embed/{re.escape(video_id)}'
        )
        for iframe in soup.find_all("iframe"):
            if pattern.search(iframe.get("src", "")):
                return iframe
        return None
