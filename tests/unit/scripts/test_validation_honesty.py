"""Honesty guards for the script validation / storage / prompt path.

Three separate "silently wrong" classes are pinned here:

1. A wholesale ``tsc`` failure (timeout, missing ``npx``, broken tsconfig)
   must never be reported as ``compiles=True``. "The checker did not run" is
   not "the code compiles".
2. An improved script accepted by the fix loop must be re-stored where
   validation reads it (``scripts/parts/``) AND re-uploaded to
   artifact_service, so the durable artifact is not the un-improved source.
3. ``page_type`` must not be able to steer template loading outside
   ``templates_dir`` -- both at the schema edge and inside the loader.

Each test also pins the PRODUCTION call site, not just the helper.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from src.api.scripts_schemas import PageAnalysisInput
from src.domain.job import Job, JobStatus
from src.domain.script import ValidationResult
from src.llm.types import LLMResponse
from src.services.scripts.improvement_service import ImprovementService
from src.services.scripts.orchestrator import ScriptOrchestrator
from src.services.scripts.prompt_builder import PromptBuilder
from src.services.scripts.storage_service import StorageService
from src.services.scripts.validation_service import ValidationService

# tenant_id is a real uuid (fleet ruling); Job.tenant_id is typed UUID.
TEST_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")

_VALID_JOB_ID = "00000000-0000-0000-0000-000000000042"


@pytest.fixture()
def validator() -> ValidationService:
    return ValidationService(
        ts_compile_timeout=30,
        discover_timeout=15,
        dry_run_timeout=60,
        error_truncation_limit=500,
    )


# ── 1. "checker did not run" must never read as "compiles" ─────────────


class TestTypeScriptCheckerCouldNotRun:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "wholesale_output",
        [
            "Command timed out after 60s",
            "Command not found: [Errno 2] No such file or directory: 'npx'",
            "error TS5058: The specified path does not exist: 'tsconfig.json'.",
            "",
        ],
        ids=["timeout", "npx-missing", "no-tsconfig", "silent-nonzero"],
    )
    async def test_wholesale_failure_is_not_compiles_true(
        self, validator: ValidationService, wholesale_output: str
    ) -> None:
        """No output line names ``parts/p1`` -- the old code returned (True, "")."""
        with patch.object(
            validator, "_run_command", AsyncMock(return_value=(False, wholesale_output))
        ):
            compiles, err = await validator.check_typescript_file(Path("/tmp"), "p1")

        assert compiles is False, "a checker that never checked reported success"
        assert err, "a failed check must carry a reason"

    @pytest.mark.asyncio
    async def test_unrelated_part_errors_still_pass_this_part(
        self, validator: ValidationService
    ) -> None:
        """tsc DID run and produced diagnostics -- none of them name this part."""
        output = "parts/other.demo.spec.ts(3,7): error TS2304: Cannot find name 'foo'."
        with patch.object(validator, "_run_command", AsyncMock(return_value=(False, output))):
            compiles, err = await validator.check_typescript_file(Path("/tmp"), "p1")

        assert compiles is True
        assert err == ""

    @pytest.mark.asyncio
    async def test_own_errors_are_reported(self, validator: ValidationService) -> None:
        output = "parts/p1.demo.spec.ts(3,7): error TS2304: Cannot find name 'foo'."
        with patch.object(validator, "_run_command", AsyncMock(return_value=(False, output))):
            compiles, err = await validator.check_typescript_file(Path("/tmp"), "p1")

        assert compiles is False
        assert "TS2304" in err

    @pytest.mark.asyncio
    async def test_success_passes(self, validator: ValidationService) -> None:
        with patch.object(validator, "_run_command", AsyncMock(return_value=(True, ""))):
            compiles, _ = await validator.check_typescript_file(Path("/tmp"), "p1")
        assert compiles is True

    @pytest.mark.asyncio
    async def test_production_validate_reports_compiles_false_on_timeout(
        self, validator: ValidationService, tmp_path: Path
    ) -> None:
        """PRODUCTION CALL SITE: ``validate()`` must not claim compiles=True."""
        (tmp_path / "parts").mkdir()
        (tmp_path / "parts" / "p1.demo.spec.ts").write_text(
            "import { test } from '@playwright/test';\n", encoding="utf-8"
        )

        with patch.object(
            validator,
            "_run_command",
            AsyncMock(return_value=(False, "Command timed out after 30s")),
        ):
            result = await validator.validate(tmp_path, "p1")

        assert result.compiles is False
        assert result.accepted is False
        assert result.errors is not None
        assert any("TypeScript" in e for e in result.errors)


# ── 2. accepted script must overwrite the durable artifact ─────────────


def _make_orchestrator(**overrides: Any) -> ScriptOrchestrator:
    defaults: dict[str, Any] = {
        "job_repo": AsyncMock(),
        "batch_repo": AsyncMock(),
        "usage_repo": AsyncMock(),
        "generation_service": AsyncMock(),
        "validation_service": AsyncMock(),
        "improvement_service": AsyncMock(),
        "scaffold_service": AsyncMock(),
        "storage": MagicMock(),
    }
    defaults.update(overrides)
    return ScriptOrchestrator(**defaults)


class TestImprovedScriptIsStoredWhereItIsRead:
    @pytest.mark.asyncio
    async def test_improved_source_lands_in_parts_and_is_reuploaded(self, tmp_path: Path) -> None:
        improved = "// IMPROVED\nimport { test } from '@playwright/test';\n"

        job = Job(
            job_id=_VALID_JOB_ID,
            tenant_id=TEST_TENANT_ID,
            part_id="p1",
            part_type="browser_recording",
            status=JobStatus.COMPLETED,
            shot_details={"id": "p1", "part_type": "browser_recording"},
            script_source="// ORIGINAL broken\n",
        )
        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=job)
        job_repo.mark_validated = AsyncMock(return_value=False)

        storage = StorageService(local_path=str(tmp_path))

        validation_svc = AsyncMock()
        validation_svc.validate = AsyncMock(
            return_value=ValidationResult(accepted=False, errors=["TypeScript error: boom"])
        )

        async def _improve(sdir: Path, script_path: Path, *args: Any, **kwargs: Any):
            script_path.write_text(improved, encoding="utf-8")
            return ValidationResult(
                accepted=True, compiles=True, discoverable=True, runs_headless=True
            )

        improvement_svc = AsyncMock()
        improvement_svc.improve = AsyncMock(side_effect=_improve)

        artifact_client = AsyncMock()
        artifact_client.upload_bytes = AsyncMock(return_value=MagicMock(artifact_id=uuid4()))

        orch = _make_orchestrator(
            job_repo=job_repo,
            validation_service=validation_svc,
            improvement_service=improvement_svc,
            storage=storage,
            artifact_client=artifact_client,
        )

        result = await orch.validate_job(_VALID_JOB_ID, "t1")
        assert result.accepted is True

        job_dir = tmp_path / _VALID_JOB_ID
        part_file = job_dir / "scripts" / "parts" / "p1.demo.spec.ts"
        assert part_file.read_text(encoding="utf-8") == improved

        # The un-improved-source bug: store_script without part_type/source_type
        # wrote the JOB ROOT instead, leaving the real part file untouched.
        assert not (job_dir / "p1.demo.spec.ts").exists(), (
            "improved script was written to the job root, not scripts/parts/"
        )

        # And the durable artifact must be refreshed, else record downloads the
        # un-improved script by artifact_id.
        artifact_client.upload_bytes.assert_awaited_once()
        upload_call = artifact_client.upload_bytes.await_args
        assert upload_call is not None
        assert upload_call.kwargs["content"] == improved.encode()


class TestExhaustedImproveDoesNotEraseMeasurements:
    """A failed improve loop must not persist ``compiles=false`` for a script
    that compiled.

    ``tests/unit/scripts/test_improvement_service.py`` asserts only
    ``accepted`` / ``improvement_attempts`` on the exhausted path and never the
    three booleans, so nothing caught the exhausted ``ValidationResult``
    falling back to its ``False`` defaults and being handed straight to
    ``mark_validated``.
    """

    @pytest.mark.asyncio
    async def test_baseline_booleans_reach_mark_validated(self, tmp_path: Path) -> None:
        job = Job(
            job_id=_VALID_JOB_ID,
            tenant_id=TEST_TENANT_ID,
            part_id="p1",
            part_type="browser_recording",
            status=JobStatus.COMPLETED,
            shot_details={"id": "p1", "part_type": "browser_recording"},
            script_source="import { test } from '@playwright/test';\ntest('x', async () => {});\n",
        )
        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=job)
        job_repo.mark_validated = AsyncMock(return_value=False)

        storage = StorageService(local_path=str(tmp_path))

        # Compiled and discoverable; only the headless dry-run failed.
        baseline = ValidationResult(
            compiles=True,
            discoverable=True,
            runs_headless=False,
            accepted=False,
            errors=["dry-run failed"],
        )
        validation_svc = AsyncMock()
        validation_svc.validate = AsyncMock(return_value=baseline)

        # Real improvement service; the LLM never returns a code block, so every
        # attempt is exhausted without a re-validation.
        llm = AsyncMock()
        llm.complete = AsyncMock(return_value=LLMResponse(content="sorry", provider="test"))
        improvement_svc = ImprovementService(llm, max_attempts=1)

        orch = _make_orchestrator(
            job_repo=job_repo,
            validation_service=validation_svc,
            improvement_service=improvement_svc,
            storage=storage,
        )

        result = await orch.validate_job(_VALID_JOB_ID, "t1")

        assert result.accepted is False
        call = job_repo.mark_validated.await_args
        assert call is not None
        kwargs = call.kwargs
        assert kwargs["compiles"] is True
        assert kwargs["discoverable"] is True
        assert kwargs["runs_headless"] is False


# ── 3. page_type must not steer template loading out of templates_dir ──


class TestTemplateContainment:
    def test_load_template_rejects_traversal(self, tmp_path: Path) -> None:
        (tmp_path / "secret.md").write_text("SECRET-CONTENT", encoding="utf-8")
        templates = tmp_path / "templates"
        templates.mkdir()
        (templates / "generic.md").write_text("GENERIC", encoding="utf-8")

        builder = PromptBuilder(templates_dir=templates)
        assert builder._load_template("../secret.md") is None

    def test_load_template_rejects_absolute_path(self, tmp_path: Path) -> None:
        secret = tmp_path / "secret.md"
        secret.write_text("SECRET-CONTENT", encoding="utf-8")
        templates = tmp_path / "templates"
        templates.mkdir()

        builder = PromptBuilder(templates_dir=templates)
        assert builder._load_template(str(secret)) is None

    def test_load_template_still_loads_real_templates(self, tmp_path: Path) -> None:
        templates = tmp_path / "templates"
        templates.mkdir()
        (templates / "landing.md").write_text("LANDING", encoding="utf-8")
        builder = PromptBuilder(templates_dir=templates)
        assert builder._load_template("landing.md") == "LANDING"

    def test_production_page_type_template_cannot_read_outside(self, tmp_path: Path) -> None:
        """PRODUCTION CALL SITE: a traversal page_type must fall back to generic."""
        (tmp_path / "secret.md").write_text("SECRET-CONTENT", encoding="utf-8")
        templates = tmp_path / "templates"
        templates.mkdir()
        (templates / "generic.md").write_text("GENERIC", encoding="utf-8")

        builder = PromptBuilder(templates_dir=templates)
        out = builder._page_type_template(
            {"pages": ["pg1"]},
            {"pg1": {"page_type": "../secret"}},
        )
        assert "SECRET-CONTENT" not in out
        assert out == "GENERIC"

    def test_schema_rejects_traversal_page_type(self) -> None:
        with pytest.raises(ValidationError):
            PageAnalysisInput(page_id="p", url="https://x", page_type="../secret")

    def test_schema_rejects_absolute_page_type(self) -> None:
        with pytest.raises(ValidationError):
            PageAnalysisInput(page_id="p", url="https://x", page_type="/etc/passwd")

    def test_schema_accepts_normal_page_type(self) -> None:
        assert PageAnalysisInput(page_id="p", url="https://x", page_type="landing").page_type == (
            "landing"
        )
