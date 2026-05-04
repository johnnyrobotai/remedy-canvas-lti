"""Alt text generation pipeline using pluggable vision providers."""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urlparse, unquote
from pathlib import Path

import structlog


from lti_app.config import get_settings
from lti_app.core.accessibility.image_alt import (
    get_inadequate_alt_reason,
    truncate_alt_text,
)
from lti_app.core.ai.alt_text_providers import (
    AltTextProvider,
    AltTextProviderError,
    ProviderGenerationResult,
    create_alt_text_provider,
)
from lti_app.core.ai.image_fetcher import ImageFetcher
from lti_app.models import (
    AltTextGenerationResponse,
    AltTextGenerationResult,
    AltTextGenerationStatus,
    CourseImage,
    CoursePage,
)

_logger = structlog.get_logger(__name__)

PERCENT_ESCAPE_RE = re.compile(r"%[0-9a-fA-F]{2}")


class AltTextGenerator:
    """Generate alt text for course images."""

    def __init__(
        self,
        image_fetcher: ImageFetcher,
        *,
        provider: AltTextProvider | None = None,
        run_id: str | None = None,
    ) -> None:
        self._image_fetcher = image_fetcher
        self._provider = provider or create_alt_text_provider()
        self._run_id = run_id

    async def generate_for_image(
        self,
        image: CourseImage,
        page_context: str = "",
        course_id: int = 0,
    ) -> AltTextGenerationResult:
        """Generate alt text for a single image."""
        # Skip if already assessed as not needing alt text
        if image.alt_text_skip_reason:
            return self._build_result(
                image=image,
                status=AltTextGenerationStatus.SKIPPED,
                reason=image.alt_text_skip_reason,
            )

        if not image.needs_alt_text:
            return self._build_result(
                image=image,
                status=AltTextGenerationStatus.SKIPPED,
                reason="existing_alt_text_acceptable",
            )

        context = page_context.strip()

        # Fetch image bytes
        try:
            payload = await self._image_fetcher.fetch_image(image.src, course_id)
        except Exception as exc:
            _logger.warning("image_fetch_failed", src=image.src[:80], error=str(exc))
            # CLU-66: the exception path used to return ERROR with no
            # fallback, leaving the image without alt text and IMG001
            # firing forever. Now we apply the same filename-derived
            # fallback as the `payload is None` case (consistency with
            # the other 4 fallback call sites in this file).
            settings = get_settings()
            if settings.alt_text_auto_fallback:
                fallback_text = self._build_safe_fallback(image, context)
                return self._build_result(
                    image=image,
                    status=AltTextGenerationStatus.GENERATED,
                    alt_text=fallback_text,
                    provider=self._provider.provider_name,
                    confidence=0.20,
                    fallback_used=True,
                    fallback_reason=f"image_fetch_exception: {type(exc).__name__}",
                )
            return self._build_result(
                image=image,
                status=AltTextGenerationStatus.ERROR,
                reason=str(exc),
            )

        if payload is None:
            # Image is genuinely unreachable (404, deleted file, broken
            # legacy reference). Without a fallback, IMG001 stays violated
            # forever because the HTML transformer's safety net only adds
            # alt to images with NO alt attribute, and most broken images
            # in real courses have alt="" (CLU-66).
            settings = get_settings()
            if settings.alt_text_auto_fallback:
                fallback_text = self._build_safe_fallback(image, context)
                return self._build_result(
                    image=image,
                    status=AltTextGenerationStatus.GENERATED,
                    alt_text=fallback_text,
                    provider=self._provider.provider_name,
                    confidence=0.30,
                    fallback_used=True,
                    fallback_reason="image_not_fetchable",
                )
            return self._build_result(
                image=image,
                status=AltTextGenerationStatus.SKIPPED,
                reason="image_not_fetchable",
            )

        # Generate candidates
        try:
            candidate_method = getattr(self._provider, "generate_alt_text_candidates", None)
            if callable(candidate_method):
                provider_candidates = await candidate_method(
                    image_bytes=payload.image_bytes,
                    mime_type=payload.mime_type,
                    context=context,
                    run_id=self._run_id,
                )
            else:
                provider_candidates = [
                    await self._provider.generate_alt_text(
                        image_bytes=payload.image_bytes,
                        mime_type=payload.mime_type,
                        context=context,
                        run_id=self._run_id,
                    )
                ]
        except AltTextProviderError as exc:
            _logger.error("ai_generation_failed", src=image.src[:80], error=str(exc))
            return self._build_result(
                image=image,
                status=AltTextGenerationStatus.ERROR,
                provider=self._provider.provider_name,
                reason=str(exc),
            )

        # Clean and validate candidates
        cleaned_candidates: list[ProviderGenerationResult] = []
        for candidate in provider_candidates:
            cleaned = self._clean_alt_text(candidate.text)
            validation_reason = self._validate_generated_alt(cleaned)
            if validation_reason:
                _logger.info(
                    "alt_text_candidate_rejected",
                    model=candidate.model,
                    src=image.src[:80],
                    reason=validation_reason,
                )
                continue
            cleaned_candidates.append(
                ProviderGenerationResult(text=cleaned, model=candidate.model)
            )

        settings = get_settings()

        if not cleaned_candidates:
            if settings.alt_text_auto_fallback:
                fallback_text = self._build_safe_fallback(image, context)
                return self._build_result(
                    image=image,
                    status=AltTextGenerationStatus.GENERATED,
                    alt_text=fallback_text,
                    provider=self._provider.provider_name,
                    confidence=0.45,
                    candidate_count=len(provider_candidates),
                    fallback_used=True,
                    fallback_reason="all_candidates_invalid",
                )
            return self._build_result(
                image=image,
                status=AltTextGenerationStatus.MANUAL_REVIEW,
                provider=self._provider.provider_name,
                reason="all_candidates_invalid",
            )

        # Judge / select best
        try:
            selection_method = getattr(self._provider, "select_best_candidate", None)
            if callable(selection_method):
                selection = await selection_method(
                    candidates=cleaned_candidates,
                    context=context,
                    run_id=self._run_id,
                )
            else:
                selection = None
        except AltTextProviderError as exc:
            _logger.warning("alt_text_judge_failed", src=image.src[:80], error=str(exc))
            selection = None

        if selection is None:
            candidate = cleaned_candidates[0]
            selection_text = candidate.text
            selection_model = candidate.model
            selection_confidence = 0.78
            judge_model = None
        else:
            selection_text = self._clean_alt_text(selection.text)
            selection_model = selection.model
            selection_confidence = selection.confidence
            judge_model = selection.judge_model

        # Final validation on selected text
        validation_reason = self._validate_generated_alt(selection_text)
        if validation_reason:
            if settings.alt_text_auto_fallback:
                fallback_text = self._build_safe_fallback(image, context)
                return self._build_result(
                    image=image,
                    status=AltTextGenerationStatus.GENERATED,
                    alt_text=fallback_text,
                    provider=self._provider.provider_name,
                    model=selection_model,
                    confidence=0.45,
                    candidate_count=len(cleaned_candidates),
                    judge_model=judge_model,
                    fallback_used=True,
                    fallback_reason=validation_reason,
                )
            return self._build_result(
                image=image,
                status=AltTextGenerationStatus.MANUAL_REVIEW,
                provider=self._provider.provider_name,
                model=selection_model,
                candidate_count=len(cleaned_candidates),
                judge_model=judge_model,
                reason=validation_reason,
            )

        # Low confidence fallback
        if (
            settings.alt_text_auto_fallback
            and selection_confidence < settings.alt_text_min_confidence
        ):
            fallback_text = self._build_safe_fallback(image, context)
            return self._build_result(
                image=image,
                status=AltTextGenerationStatus.GENERATED,
                alt_text=fallback_text,
                provider=self._provider.provider_name,
                model=selection_model,
                confidence=selection_confidence,
                candidate_count=len(cleaned_candidates),
                judge_model=judge_model,
                fallback_used=True,
                fallback_reason="low_confidence_selection",
            )

        return self._build_result(
            image=image,
            status=AltTextGenerationStatus.GENERATED,
            alt_text=selection_text,
            provider=self._provider.provider_name,
            model=selection_model,
            confidence=selection_confidence,
            candidate_count=len(cleaned_candidates),
            judge_model=judge_model,
        )

    async def generate_for_course(
        self,
        images: list[CourseImage],
        pages: list[CoursePage],
        course_id: int = 0,
    ) -> AltTextGenerationResponse:
        """Generate alt text for all images in a course."""
        import time

        page_contexts: dict[str, str] = {
            p.id: f"a page titled '{p.title}'" for p in pages
        }

        settings = get_settings()
        semaphore = asyncio.Semaphore(max(settings.ai_max_concurrency, 1))

        # Per-job global budget. Once exhausted, remaining images skip
        # provider calls entirely and return as MANUAL_REVIEW so the
        # remediation phase can continue without stalling on rate limits
        # (CLU-58). Budget starts when the first task acquires the slot,
        # not at scheduling time, so queuing delay doesn't eat the budget.
        budget_seconds = max(settings.alt_text_global_budget_seconds, 1)
        deadline_holder: dict[str, float] = {}
        budget_exhausted_count = 0

        async def _bounded(image: CourseImage) -> AltTextGenerationResult:
            nonlocal budget_exhausted_count
            async with semaphore:
                if "deadline" not in deadline_holder:
                    deadline_holder["deadline"] = time.monotonic() + budget_seconds
                if time.monotonic() >= deadline_holder["deadline"]:
                    budget_exhausted_count += 1
                    return self._build_result(
                        image=image,
                        status=AltTextGenerationStatus.MANUAL_REVIEW,
                        provider=self._provider.provider_name,
                        reason="budget_exhausted",
                    )
                return await self.generate_for_image(
                    image,
                    page_contexts.get(image.page_id, ""),
                    course_id,
                )

        tasks = [_bounded(image) for image in images]
        results = await asyncio.gather(*tasks)

        if budget_exhausted_count:
            _logger.warning(
                "alt_text_budget_exhausted",
                budget_seconds=budget_seconds,
                images_skipped=budget_exhausted_count,
                images_total=len(images),
            )

        # Build response
        alt_texts: dict[str, str] = {}
        generated_count = 0
        skipped_count = 0
        manual_review_count = 0
        error_count = 0

        for result in results:
            _logger.info(
                "alt_text_result",
                src=result.src[:80],
                status=result.status.value,
                info=result.reason or result.alt_text or "ok",
            )
            if result.status == AltTextGenerationStatus.GENERATED and result.alt_text:
                alt_texts[result.src] = result.alt_text
                generated_count += 1
            elif result.status == AltTextGenerationStatus.SKIPPED:
                skipped_count += 1
            elif result.status == AltTextGenerationStatus.MANUAL_REVIEW:
                manual_review_count += 1
            else:
                error_count += 1

        return AltTextGenerationResponse(
            course_id=str(course_id),
            generated_count=generated_count,
            skipped_count=skipped_count,
            manual_review_count=manual_review_count,
            error_count=error_count,
            alt_texts=alt_texts,
            results=list(results),
        )

    # --- Private helpers ---

    def _clean_alt_text(self, alt_text: str) -> str:
        import re as _re

        alt_text = alt_text.strip().strip("\"'")
        prefixes_to_remove = [
            "Alt text:", "Alt:", "Description:", "Image shows",
            "An image of", "A photo of", "A picture of", "A diagram of",
            "Image of", "Picture of", "Photo of", "Diagram of",
            "Illustration of", "Chart of", "Graph of", "Figure of",
            "Graphic of", "This image shows", "This is an image of",
            "This is a",
        ]
        for prefix in prefixes_to_remove:
            if alt_text.lower().startswith(prefix.lower()):
                alt_text = alt_text[len(prefix):].strip()

        alt_text = _re.sub(
            r"^(?:image|photo|picture|diagram|illustration|chart|graph|figure|graphic)"
            r"\s+(?:showing|depicting|displaying|of)\s+",
            "",
            alt_text,
            flags=_re.I,
        )
        alt_text = _re.sub(
            r"\s+(image|graphic|photo|picture|photograph|img)\s*\.?\s*$",
            "",
            alt_text,
            flags=_re.I,
        )
        alt_text = alt_text.rstrip(".")
        alt_text = " ".join(alt_text.split())

        if alt_text:
            alt_text = alt_text[0].upper() + alt_text[1:]

        return truncate_alt_text(alt_text)

    def _validate_generated_alt(self, alt_text: str) -> str | None:
        if not alt_text:
            return "empty_alt_text"
        if PERCENT_ESCAPE_RE.search(alt_text):
            return "unusable_output_percent_encoded"
        if "\\" in alt_text or "{" in alt_text or "}" in alt_text:
            return "unusable_output_formula_like"
        reason = get_inadequate_alt_reason(alt_text)
        if reason:
            return f"unusable_output_{reason}"
        return None

    def _build_safe_fallback(self, image: CourseImage, context: str) -> str:
        page_hint = ""
        if "page titled" in context.lower():
            page_hint = context.split("'", 2)[1] if "'" in context else ""
        elif context:
            page_hint = context

        src_path = unquote(urlparse(image.src).path or image.src)
        stem = Path(src_path).stem
        stem = re.sub(r"[_-]+", " ", stem)
        stem = re.sub(
            r"\b(img|image|photo|graphic|scan|figure)\b", "", stem, flags=re.I
        )
        stem = " ".join(stem.split()).strip()

        if stem and get_inadequate_alt_reason(stem) is None:
            candidate = stem
        elif page_hint:
            candidate = f"Illustration for {page_hint}"
        else:
            candidate = "Course illustration"

        candidate = candidate.rstrip(".")
        if candidate:
            candidate = candidate[0].upper() + candidate[1:]
        return truncate_alt_text(candidate)

    def _build_result(
        self,
        *,
        image: CourseImage,
        status: AltTextGenerationStatus,
        alt_text: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        confidence: float = 0.0,
        candidate_count: int = 0,
        judge_model: str | None = None,
        fallback_used: bool = False,
        fallback_reason: str | None = None,
        reason: str | None = None,
    ) -> AltTextGenerationResult:
        return AltTextGenerationResult(
            image_id=image.id,
            page_id=image.page_id,
            src=image.src,
            status=status,
            alt_text=alt_text,
            provider=provider,
            model=model,
            confidence=confidence,
            candidate_count=candidate_count,
            judge_model=judge_model,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
            reason=reason,
        )
