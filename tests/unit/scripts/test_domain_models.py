"""Tests for domain models: batch, generation, usage, script, job."""

from __future__ import annotations

from datetime import date, datetime

from src.domain.batch import Batch, BatchStatus
from src.domain.generation import GenerationRequest
from src.domain.job import JobStatus
from src.domain.script import GeneratedScript, ImprovementAttempt, ValidationResult
from src.domain.usage import UsageRecord


class TestBatchStatus:
    def test_values(self):
        assert BatchStatus.PENDING == "pending"
        assert BatchStatus.IN_PROGRESS == "in_progress"
        assert BatchStatus.COMPLETED == "completed"
        assert BatchStatus.PARTIAL == "partial"

    def test_batch_model(self):
        b = Batch(batch_id="b1", tenant_id="t1", total_parts=5)
        assert b.batch_id == "b1"
        assert b.tenant_id == "t1"
        assert b.total_parts == 5
        assert b.status == BatchStatus.PENDING
        assert b.created_at is None
        assert b.completed_at is None

    def test_batch_with_all_fields(self):
        now = datetime.now()
        b = Batch(
            batch_id="b2",
            tenant_id="t2",
            total_parts=3,
            status=BatchStatus.COMPLETED,
            created_at=now,
            completed_at=now,
        )
        assert b.status == BatchStatus.COMPLETED
        assert b.created_at == now
        assert b.completed_at == now


class TestGenerationRequest:
    def test_defaults(self):
        g = GenerationRequest(part={"id": "p1"})
        assert g.part == {"id": "p1"}
        assert g.page_analysis is None
        assert g.storyline_context is None
        assert g.theme is None
        assert g.user_context is None
        assert g.message is None
        assert g.mode == "webapp_commercial"
        assert g.source_type == "web"
        assert g.auth_enabled is False

    def test_all_fields(self):
        g = GenerationRequest(
            part={"id": "p1"},
            page_analysis={"p1": {"url": "http://example.com"}},
            storyline_context="context",
            theme={"color": "blue"},
            user_context={"goals": []},
            message="hello",
            mode="tutorial",
            source_type="ios",
            auth_enabled=True,
        )
        assert g.mode == "tutorial"
        assert g.source_type == "ios"
        assert g.auth_enabled is True


class TestUsageRecord:
    def test_defaults(self):
        u = UsageRecord(tenant_id="t1", date=date.today())
        assert u.tenant_id == "t1"
        assert u.request_count == 0
        assert u.scripts_generated == 0
        assert u.scripts_accepted == 0
        assert u.llm_tokens_used == 0
        assert u.error_count == 0

    def test_full(self):
        u = UsageRecord(
            tenant_id="t1",
            date=date(2026, 1, 1),
            request_count=10,
            scripts_generated=5,
            scripts_accepted=3,
            llm_tokens_used=1000,
            error_count=2,
        )
        assert u.scripts_generated == 5
        assert u.llm_tokens_used == 1000


class TestJobStatus:
    def test_all_statuses(self):
        assert JobStatus.PENDING == "pending"
        assert JobStatus.PROCESSING == "processing"
        assert JobStatus.COMPLETED == "completed"
        assert JobStatus.FAILED == "failed"
        assert JobStatus.CANCELLED == "cancelled"


class TestGeneratedScript:
    def test_defaults(self):
        s = GeneratedScript(script_source="code")
        assert s.script_source == "code"
        assert s.prompt_used is None
        assert s.generation_time_seconds == 0.0
        assert s.llm_tokens_used == 0
        assert s.provider_used == ""


class TestImprovementAttempt:
    def test_defaults(self):
        a = ImprovementAttempt(attempt=1)
        assert a.attempt == 1
        assert a.errors == []
        assert a.fix_applied is False
        assert a.accepted is False
        assert a.reason is None


class TestValidationResult:
    def test_defaults(self):
        v = ValidationResult()
        assert v.compiles is False
        assert v.discoverable is False
        assert v.runs_headless is False
        assert v.accepted is False
        assert v.errors is None
        assert v.improvement_attempts == 0
        assert v.attempts_detail == []
