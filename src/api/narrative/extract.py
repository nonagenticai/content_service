"""Narrative extraction endpoint (sync): pull hooks/quotes/highlights from pages."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from src.api.narrative_schemas import NarrativeExtractRequest, NarrativeExtractResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["narrative"])


@router.post(
    "/narrative/extract",
    operation_id="extract_narrative",
    responses={
        422: {"description": "Request body validation error"},
    },
)
async def extract_narrative(
    body: NarrativeExtractRequest,
    request: Request,
) -> NarrativeExtractResponse:
    """Extract narrative hooks from pages (sync).

    Args:
        body: List of pages to extract narrative material from.
        request: FastAPI request with app state.

    Returns:
        NarrativeExtractResponse with hooks, quotes, and feature highlights.
    """
    logger.info("Received narrative extract request: %d pages", len(body.pages))
    extractor = request.app.state.narrative_extractor
    result = extractor.extract(body.pages)
    logger.info(
        "Narrative extract complete: %d hooks, %d quotes, %d highlights",
        len(result.get("hooks", [])),
        len(result.get("key_quotes", [])),
        len(result.get("feature_highlights", [])),
    )
    return NarrativeExtractResponse(**result)
