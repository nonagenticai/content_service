"""Tests for API schema models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.api.scripts_schemas import (
    BatchStatusResponse,
    CampaignPackResponse,
    CampaignVariantMetaResponse,
    GenerateBatchRequest,
    GenerateCampaignPackRequest,
    GenerateRequest,
    GenerationResultResponse,
    ImprovementAttemptResponse,
    NarrationGuidanceInput,
    ScaffoldRequest,
    ScaffoldResponse,
    ShotDetailInput,
    UserContextInput,
    ValidationResultResponse,
)

# ---------------------------------------------------------------------------
# NarrationGuidanceInput
# ---------------------------------------------------------------------------


class TestNarrationGuidanceInput:
    def test_defaults(self) -> None:
        ng = NarrationGuidanceInput()
        assert ng.tone is None
        assert ng.key_messages == []
        assert ng.avoid_topics == []

    def test_with_values(self) -> None:
        ng = NarrationGuidanceInput(
            tone="professional",
            key_messages=["fast", "reliable"],
            avoid_topics=["pricing"],
        )
        assert ng.tone == "professional"
        assert ng.key_messages == ["fast", "reliable"]
        assert ng.avoid_topics == ["pricing"]

    def test_round_trip(self) -> None:
        data = {"tone": "casual", "key_messages": ["easy"], "avoid_topics": []}
        ng = NarrationGuidanceInput.model_validate(data)
        dumped = ng.model_dump()
        assert dumped == data


# ---------------------------------------------------------------------------
# UserContextInput
# ---------------------------------------------------------------------------


class TestUserContextInput:
    def test_defaults(self) -> None:
        uc = UserContextInput()
        assert uc.description is None
        assert uc.site_type is None
        assert uc.target_audience is None
        assert uc.goals == []
        assert uc.focus_pages == []
        assert uc.exclude_pages == []
        assert uc.narration_guidance is None

    def test_with_narration_guidance(self) -> None:
        uc = UserContextInput(
            description="SaaS dashboard",
            site_type="webapp",
            target_audience="developers",
            goals=["show onboarding"],
            narration_guidance=NarrationGuidanceInput(tone="friendly"),
        )
        assert uc.narration_guidance is not None
        assert uc.narration_guidance.tone == "friendly"

    def test_from_dict(self) -> None:
        data = {
            "description": "E-commerce site",
            "site_type": "ecommerce",
            "goals": ["checkout flow"],
            "narration_guidance": {"tone": "upbeat", "key_messages": ["deals"]},
        }
        uc = UserContextInput.model_validate(data)
        assert uc.site_type == "ecommerce"
        assert uc.narration_guidance is not None
        assert uc.narration_guidance.key_messages == ["deals"]


# ---------------------------------------------------------------------------
# ImprovementAttemptResponse
# ---------------------------------------------------------------------------


class TestImprovementAttemptResponse:
    def test_defaults(self) -> None:
        ad = ImprovementAttemptResponse(attempt=1)
        assert ad.attempt == 1
        assert ad.errors == []
        assert ad.fix_applied is False

    def test_with_values(self) -> None:
        ad = ImprovementAttemptResponse(
            attempt=2,
            errors=["TS2304: Cannot find name 'foo'"],
            fix_applied=True,
        )
        assert ad.attempt == 2
        assert len(ad.errors) == 1
        assert ad.fix_applied is True

    def test_attempt_required(self) -> None:
        with pytest.raises(ValidationError):
            ImprovementAttemptResponse()  # type: ignore[call-arg]  # ty: ignore[missing-argument]


# ---------------------------------------------------------------------------
# ShotDetailInput.id -- path-traversal hardening
# ---------------------------------------------------------------------------


class TestShotDetailInputIdValidation:
    def test_accepts_normal_ids(self) -> None:
        for good in ("part-1", "ch01.part_2", "seed-commercial-v1", "P"):
            assert ShotDetailInput(id=good).id == good

    @pytest.mark.parametrize(
        "bad",
        [
            "../etc/passwd",
            "..",
            "a/../../b",
            "foo/bar",
            "/abs/path",
            "back\\slash",
            "has space",
            "name;rm -rf",
            "",
            "x" * 129,
            "weird‮id",
        ],
    )
    def test_rejects_traversal_and_separators(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            ShotDetailInput(id=bad)

    def test_generate_request_rejects_bad_part_id(self) -> None:
        with pytest.raises(ValidationError):
            GenerateRequest.model_validate({"part": {"id": "../../escape"}})

    def test_batch_request_rejects_bad_part_id(self) -> None:
        with pytest.raises(ValidationError):
            GenerateBatchRequest.model_validate({"parts": [{"id": "ok"}, {"id": "../bad"}]})


# ---------------------------------------------------------------------------
# GenerateRequest -- new fields
# ---------------------------------------------------------------------------


class TestGenerateRequestNewFields:
    def _minimal_part(self) -> dict:
        return {"id": "part-1"}

    def test_new_field_defaults(self) -> None:
        req = GenerateRequest(part=ShotDetailInput(id="p1"))
        assert req.mode == "webapp_commercial"
        assert req.source_type == "web"
        assert req.user_context is None
        assert req.auth_enabled is False

    def test_new_fields_set(self) -> None:
        req = GenerateRequest(
            part=ShotDetailInput(id="p1"),
            mode="saas_tutorial",
            source_type="app",
            user_context=UserContextInput(description="My app"),
            auth_enabled=True,
        )
        assert req.mode == "saas_tutorial"
        assert req.source_type == "app"
        assert req.user_context is not None
        assert req.user_context.description == "My app"
        assert req.auth_enabled is True

    def test_backward_compatible_no_new_fields(self) -> None:
        """Old clients sending only the original fields should still work."""
        data = {
            "part": {"id": "p1"},
            "storyline_context": "intro",
        }
        req = GenerateRequest.model_validate(data)
        assert req.mode == "webapp_commercial"
        assert req.auth_enabled is False


# ---------------------------------------------------------------------------
# GenerateBatchRequest -- new fields
# ---------------------------------------------------------------------------


class TestGenerateBatchRequestNewFields:
    def test_new_field_defaults(self) -> None:
        req = GenerateBatchRequest(parts=[ShotDetailInput(id="p1")])
        assert req.mode == "webapp_commercial"
        assert req.source_type == "web"
        assert req.user_context is None
        assert req.auth_enabled is False

    def test_new_fields_set(self) -> None:
        req = GenerateBatchRequest(
            parts=[ShotDetailInput(id="p1"), ShotDetailInput(id="p2")],
            mode="mobile_demo",
            source_type="mobile",
            user_context=UserContextInput(target_audience="enterprise"),
            auth_enabled=True,
        )
        assert req.mode == "mobile_demo"
        assert req.auth_enabled is True
        assert req.user_context is not None


# ---------------------------------------------------------------------------
# GenerateCampaignPackRequest
# ---------------------------------------------------------------------------


class TestGenerateCampaignPackRequest:
    def test_defaults(self) -> None:
        req = GenerateCampaignPackRequest()
        assert req.part is None
        assert req.source_type == "web"
        assert req.auth_enabled is False
        assert req.script_styles == ["webapp_commercial", "commercial", "company_commercial"]
        assert req.variants_per_style == 3
        assert req.variant_duration_seconds == 15.0

    def test_custom_values(self) -> None:
        req = GenerateCampaignPackRequest(
            part=ShotDetailInput(id="seed"),
            script_styles=["tutorial", "commercial"],
            variants_per_style=2,
            variant_duration_seconds=12.0,
            hooks=["Hook A"],
            ctas=["CTA A"],
        )
        assert req.part is not None
        assert req.part.id == "seed"
        assert req.script_styles == ["tutorial", "commercial"]
        assert req.variants_per_style == 2
        assert req.variant_duration_seconds == 12.0
        assert req.hooks == ["Hook A"]
        assert req.ctas == ["CTA A"]


# ---------------------------------------------------------------------------
# ValidationResultResponse -- attempts_detail
# ---------------------------------------------------------------------------


class TestValidationResultResponseAttemptsDetail:
    def test_attempts_detail_default(self) -> None:
        resp = ValidationResultResponse(job_id="j1", part_id="p1")
        assert resp.attempts_detail == []

    def test_attempts_detail_populated(self) -> None:
        attempts = [
            ImprovementAttemptResponse(attempt=1, errors=["err1"], fix_applied=False),
            ImprovementAttemptResponse(attempt=2, errors=[], fix_applied=True),
        ]
        resp = ValidationResultResponse(
            job_id="j1",
            part_id="p1",
            compiles=True,
            accepted=True,
            attempts_detail=attempts,
        )
        assert len(resp.attempts_detail) == 2
        assert resp.attempts_detail[0].fix_applied is False
        assert resp.attempts_detail[1].fix_applied is True


# ---------------------------------------------------------------------------
# ScaffoldRequest / ScaffoldResponse
# ---------------------------------------------------------------------------


class TestScaffoldRequest:
    def test_defaults(self) -> None:
        req = ScaffoldRequest()
        assert req.theme is None
        assert req.source_type == "web"

    def test_with_theme(self) -> None:
        from src.api.scripts_schemas import ThemeInput

        req = ScaffoldRequest(
            theme=ThemeInput(primary_color="#FF0000"),
            source_type="app",
        )
        assert req.theme is not None
        assert req.theme.primary_color == "#FF0000"
        assert req.source_type == "app"


class TestScaffoldResponse:
    def test_success(self) -> None:
        resp = ScaffoldResponse(success=True, scripts_dir="/tmp/scripts")
        assert resp.success is True
        assert resp.scripts_dir == "/tmp/scripts"
        assert resp.error is None

    def test_failure(self) -> None:
        resp = ScaffoldResponse(success=False, error="disk full")
        assert resp.success is False
        assert resp.scripts_dir is None
        assert resp.error == "disk full"


# ---------------------------------------------------------------------------
# BatchStatusResponse
# ---------------------------------------------------------------------------


class TestBatchStatusResponse:
    def test_minimal(self) -> None:
        resp = BatchStatusResponse(
            batch_id="b1",
            total=5,
            completed=3,
            failed=1,
            status="in_progress",
        )
        assert resp.batch_id == "b1"
        assert resp.total == 5
        assert resp.completed == 3
        assert resp.failed == 1
        assert resp.status == "in_progress"
        assert resp.results == []

    def test_with_results(self) -> None:
        results = [
            GenerationResultResponse(job_id="j1", part_id="p1", status="completed"),
            GenerationResultResponse(job_id="j2", part_id="p2", status="failed", error="timeout"),
        ]
        resp = BatchStatusResponse(
            batch_id="b1",
            total=2,
            completed=1,
            failed=1,
            status="completed",
            results=results,
        )
        assert len(resp.results) == 2
        assert resp.results[1].error == "timeout"

    def test_required_fields(self) -> None:
        with pytest.raises(ValidationError):
            BatchStatusResponse(batch_id="b1")  # type: ignore[call-arg]  # ty: ignore[missing-argument]


# ---------------------------------------------------------------------------
# CampaignPackResponse
# ---------------------------------------------------------------------------


class TestCampaignPackResponse:
    def test_with_variant_metadata(self) -> None:
        resp = CampaignPackResponse(
            batch_id="b1",
            job_ids={"seed-commercial-v1": "j1"},
            total_variants=1,
            variants=[
                CampaignVariantMetaResponse(
                    part_id="seed-commercial-v1",
                    style="commercial",
                    variant_index=1,
                    hook="Hook",
                    cta="CTA",
                )
            ],
        )
        assert resp.batch_id == "b1"
        assert resp.total_variants == 1
        assert len(resp.variants) == 1
        assert resp.variants[0].style == "commercial"
