"""Alt-text provider adapters for Remedy Canvas LTI."""

from __future__ import annotations

import abc
import base64
from dataclasses import dataclass

import structlog

from lti_app.config import get_settings
from lti_app.core.ai.prompt_library import (
    get_alt_text_generation_prompt,
)
from lti_app.core.ai.vision_client import OllamaVisionClient

_logger = structlog.get_logger(__name__)


class AltTextProviderError(Exception):
    """Raised when a provider cannot generate usable alt text."""


@dataclass(frozen=True)
class ProviderGenerationResult:
    """Successful provider response."""

    text: str
    model: str


@dataclass(frozen=True)
class ProviderSelectionResult:
    """Selected candidate metadata after judging."""

    text: str
    model: str
    confidence: float
    judge_model: str | None = None
    candidate_count: int = 1


class AltTextProvider(abc.ABC):
    """Abstract alt-text generation provider."""

    provider_name: str

    @abc.abstractmethod
    async def generate_alt_text(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
        context: str = "",
        run_id: str | None = None,
    ) -> ProviderGenerationResult:
        """Generate alt text for image bytes."""

    async def generate_alt_text_candidates(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
        context: str = "",
        run_id: str | None = None,
    ) -> list[ProviderGenerationResult]:
        """Generate one or more candidate alt texts."""
        return [
            await self.generate_alt_text(
                image_bytes=image_bytes,
                mime_type=mime_type,
                context=context,
                run_id=run_id,
            )
        ]

    async def select_best_candidate(
        self,
        *,
        candidates: list[ProviderGenerationResult],
        context: str = "",
        run_id: str | None = None,
    ) -> ProviderSelectionResult:
        """Choose the strongest candidate from the generated set."""
        if not candidates:
            raise AltTextProviderError("No candidates available for selection")
        candidate = candidates[0]
        return ProviderSelectionResult(
            text=candidate.text,
            model=candidate.model,
            confidence=0.78,
            candidate_count=len(candidates),
        )


class OllamaAltTextProvider(AltTextProvider):
    """Ollama-backed alt-text generation via OpenAI-compatible endpoint."""

    provider_name = "ollama"

    def __init__(self) -> None:
        settings = get_settings()
        self.model = settings.ollama_model
        self._vision_client = OllamaVisionClient()

    async def generate_alt_text(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
        context: str = "",
        run_id: str | None = None,
    ) -> ProviderGenerationResult:
        # Check AI cache first
        try:
            from lti_app.core.ai.ai_cache import get_ai_cache
            cached = get_ai_cache().get_cached_alt_text(image_bytes)
            if cached:
                return ProviderGenerationResult(
                    text=cached["alt_text"], model=cached["model"]
                )
        except Exception:
            pass  # Cache miss or not initialized — proceed normally

        prompt = get_alt_text_generation_prompt(context)
        encoded = base64.b64encode(image_bytes).decode("utf-8")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                    },
                ],
            }
        ]
        try:
            content = await self._vision_client.chat(
                model=self.model, messages=messages, run_id=run_id
            )
        except AltTextProviderError:
            raise
        except Exception as exc:
            raise AltTextProviderError(f"Request failed: {exc}") from exc
        if not content or not isinstance(content, str):
            raise AltTextProviderError("Empty content from Ollama")

        # Store in cache (fire-and-forget)
        try:
            from lti_app.core.ai.ai_cache import get_ai_cache
            get_ai_cache().cache_alt_text(
                image_bytes=image_bytes,
                image_url="",
                alt_text=content,
                model=self.model,
                confidence=0.8,
            )
        except Exception:
            pass

        return ProviderGenerationResult(text=content, model=self.model)


def create_alt_text_provider(provider_name: str | None = None) -> AltTextProvider:
    """Instantiate the alt-text provider (Ollama only)."""
    return OllamaAltTextProvider()
