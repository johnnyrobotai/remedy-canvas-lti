"""Handwritten equation OCR via vision model -> LaTeX/MathML.

Uses the unified vision client to analyze images of handwritten
mathematical equations and return structured representations.
"""

from __future__ import annotations

import base64
import re

import structlog

from lti_app.core.ai.vision_client import get_vision_client

_logger = structlog.get_logger(__name__)

EQUATION_PROMPT = (
    "Analyze this image of a handwritten mathematical equation.\n"
    "Return ONLY the equation in LaTeX format. Do not include any explanation,\n"
    "markdown formatting, or surrounding text.\n"
    "If the equation uses dollar signs or \\begin{equation}, omit those wrappers.\n"
    "Just return the raw LaTeX expression.\n"
    "If you cannot determine the equation, return exactly: UNKNOWN"
)


def _latex_to_mathml(latex: str) -> str:
    """Convert a LaTeX equation string to MathML (best-effort).

    This is a lightweight converter that handles common patterns.
    For full fidelity, a library like latex2mathml would be used.
    """
    try:
        import latex2mathml.converter

        return latex2mathml.converter.convert(latex)
    except ImportError:
        pass
    except Exception as exc:
        _logger.warning("latex2mathml_conversion_failed", error=str(exc))

    # Fallback: wrap in basic MathML annotation
    escaped = latex.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        '<math xmlns="http://www.w3.org/1998/Math/MathML">'
        "<semantics>"
        f"<annotation encoding=\"application/x-tex\">{escaped}</annotation>"
        "</semantics>"
        "</math>"
    )


async def recognize_equation(image_bytes: bytes) -> dict:
    """Recognize a handwritten equation from an image.

    Args:
        image_bytes: Raw bytes of the image (PNG, JPEG, etc.).

    Returns:
        Dict with keys: latex, mathml, error.
    """
    client = get_vision_client()
    model = client.get_primary_model()

    # Encode image as base64 for the vision API
    b64 = base64.b64encode(image_bytes).decode("ascii")

    # Detect media type from magic bytes
    media_type = "image/png"
    if image_bytes[:2] == b"\xff\xd8":
        media_type = "image/jpeg"
    elif image_bytes[:4] == b"\x89PNG":
        media_type = "image/png"
    elif image_bytes[:4] == b"GIF8":
        media_type = "image/gif"
    elif image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        media_type = "image/webp"

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{media_type};base64,{b64}",
                    },
                },
                {
                    "type": "text",
                    "text": EQUATION_PROMPT,
                },
            ],
        }
    ]

    try:
        raw = await client.chat(model=model, messages=messages, timeout=60.0)
        latex = raw.strip()

        # Clean up common wrappers the model might include
        latex = re.sub(r"^```(?:latex)?\s*", "", latex)
        latex = re.sub(r"\s*```$", "", latex)
        latex = latex.strip("`").strip()

        # Remove equation wrappers if present (check paired wrappers first)
        for start, end in ((r"\[", r"\]"), (r"\(", r"\)"), ("$$", "$$"), ("$", "$")):
            if latex.startswith(start) and latex.endswith(end) and len(latex) > len(start) + len(end):
                latex = latex[len(start) : -len(end)]
                break
        latex = latex.strip()

        if latex == "UNKNOWN" or not latex:
            return {"latex": "", "mathml": "", "error": "Could not recognize equation"}

        mathml = _latex_to_mathml(latex)

        _logger.info(
            "equation_recognized",
            latex_length=len(latex),
            model=model,
        )

        return {"latex": latex, "mathml": mathml, "error": ""}

    except Exception as exc:
        _logger.error("equation_ocr_failed", error=str(exc), model=model)
        # Try fallback model
        try:
            fallback_model = client.get_fallback_model()
            raw = await client.chat(
                model=fallback_model, messages=messages, timeout=60.0
            )
            latex = raw.strip()
            latex = re.sub(r"^```(?:latex)?\s*", "", latex)
            latex = re.sub(r"\s*```$", "", latex)
            latex = latex.strip("`").strip()

            for start, end in ((r"\[", r"\]"), (r"\(", r"\)"), ("$$", "$$"), ("$", "$")):
                if latex.startswith(start) and latex.endswith(end) and len(latex) > len(start) + len(end):
                    latex = latex[len(start) : -len(end)]
                    break
            latex = latex.strip()

            if latex == "UNKNOWN" or not latex:
                return {
                    "latex": "",
                    "mathml": "",
                    "error": "Could not recognize equation",
                }

            mathml = _latex_to_mathml(latex)
            return {"latex": latex, "mathml": mathml, "error": ""}

        except Exception as fallback_exc:
            return {"latex": "", "mathml": "", "error": str(exc)}
