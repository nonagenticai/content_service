"""Shared test fixtures for content_service tests.

Merged from narrative_service and script_generator_service conftest files.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from w2v_common.cache.redis_client import RedisClient
from w2v_common.clients.wm_client import WMResponse
from w2v_common.testing import inject_fakeredis

# ---------------------------------------------------------------------------
# Shared tenant identities.
#
# All five tenant_id columns (narration_jobs, script_batches, script_jobs,
# script_usage, storyline_jobs) are native postgres ``uuid`` and
# ``src.domain.job.Job.tenant_id`` is a ``UUID``. Tests must therefore seed real
# uuids, never string sentinels. These three are DISTINCT so tenant-isolation
# tests can keep proving isolation.
# ---------------------------------------------------------------------------
TENANT_A = UUID("aaaaaaaa-0000-4000-8000-00000000000a")
TENANT_B = UUID("bbbbbbbb-0000-4000-8000-00000000000b")
TENANT_C = UUID("cccccccc-0000-4000-8000-00000000000c")

# The tenant reaches src as a STRING: AuthMiddleware / ``get_tenant_id`` read it
# from the token claim, and SQLAlchemy coerces a well-formed uuid string into the
# uuid column (only non-uuid sentinels blow up). Use the ``*_STR`` forms at src
# call sites -- that is the production shape -- and the ``UUID`` forms to assert
# on values that come back typed (``Job.tenant_id``, DB reads).
TENANT_A_STR = str(TENANT_A)
TENANT_B_STR = str(TENANT_B)
TENANT_C_STR = str(TENANT_C)

# Set test environment before importing anything from src
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("AUTH_ENABLED", "false")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:changeme@localhost:5455/content_service_test",  # pragma: allowlist secret
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6395/1")
# WORKER_MANAGEMENT_URL: tests don't actually hit WM (WMClient.message is
# AsyncMock-patched at call sites), but the URL is read on WMClient init.
os.environ.setdefault("WORKER_MANAGEMENT_URL", "http://worker-management.test.invalid:8887")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("STORAGE_LOCAL_PATH", "/tmp/content_service_test")
# Required field on StorageSettings (no localhost fallback in w2v_common 0.15.0+);
# unused with backend=local but Pydantic still validates it.
os.environ.setdefault("STORAGE_MINIO_ENDPOINT", "localhost:9000")

# Capture the test DB URL BEFORE the autouse _clear_settings fixture
# monkeypatches DATABASE_URL to its in-test default. CI sets DATABASE_URL
# to its content_service_ci postgres; locally falls back to the default above
# (developer must run a local postgres to execute db_engine tests).
_TEST_DATABASE_URL = os.environ["DATABASE_URL"]

# Ensure the configured local storage path exists so health checks see a writable dir.
Path(os.environ["STORAGE_LOCAL_PATH"]).mkdir(parents=True, exist_ok=True)


def pytest_configure(config: object) -> None:
    """Register custom markers so ``--strict-markers`` does not complain."""
    if not isinstance(config, pytest.Config):
        return

    config.addinivalue_line("markers", "unit: unit tests (fast, no external deps)")
    config.addinivalue_line("markers", "integration: integration tests (may need services)")


@pytest.fixture(autouse=True)
def _clear_settings(monkeypatch):
    """Remove CI env vars that interfere with config-default tests, then restore test defaults."""
    for key in list(os.environ):
        if any(
            key.startswith(p)
            for p in (
                "DATABASE_",
                "AUTH_",
                "REDIS_",
                "API_",
                "STORAGE_",
                "LOG_",
                "CACHE_",
                "TRACING_",
                "LLM_",
                "WORKER_MANAGEMENT_",
                "FFMPEG_",
            )
        ):
            monkeypatch.delenv(key, raising=False)
    # Restore test-only env vars that were cleared above
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres:changeme@localhost:5455/content_service_test",  # pragma: allowlist secret
    )
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6395/1")
    monkeypatch.setenv("WORKER_MANAGEMENT_URL", "http://worker-management.test.invalid:8887")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_PATH", "/tmp/content_service_test")
    # Required field on StorageSettings (no localhost fallback in w2v_common 0.15.0+).
    monkeypatch.setenv("STORAGE_MINIO_ENDPOINT", "localhost:9000")
    Path("/tmp/content_service_test").mkdir(parents=True, exist_ok=True)


# The in-cluster trust lanes an operator would opt into via TRUSTED_CIDRS.
# w2v_common >= 0.89.0 defaults TRUSTED_CIDRS to EMPTY (nothing trusted), so
# tests that exercise trusted-network behaviour must configure it explicitly.
IN_CLUSTER_TRUSTED_CIDRS = "127.0.0.1/32,::1/128,172.16.0.0/12,10.0.0.0/8"


@pytest.fixture
def trusted_cidrs(monkeypatch):
    """Run the test with the in-cluster ranges configured as TRUSTED_CIDRS.

    w2v_common parses TRUSTED_CIDRS into ``TRUSTED_NETWORKS`` at import time, and
    ``_is_trusted_network`` / ``dispatch`` read that module global at call time.
    Patch the global with exactly what the import-time parse yields for
    ``TRUSTED_CIDRS=IN_CLUSTER_TRUSTED_CIDRS`` (env set too, for consistency).
    A module reload is deliberately NOT used: it rebinds ``AuthMiddleware`` to
    a new class object and breaks identity checks elsewhere in the session
    (``tests/test_auth_middleware.py`` asserts the re-export *is* the lib class).
    Yields the parsed network list.
    """
    import ipaddress

    import w2v_common.auth.middleware as lib_mod

    networks = [
        ipaddress.ip_network(cidr.strip(), strict=False)
        for cidr in IN_CLUSTER_TRUSTED_CIDRS.split(",")
        if cidr.strip()
    ]
    monkeypatch.setenv("TRUSTED_CIDRS", IN_CLUSTER_TRUSTED_CIDRS)
    monkeypatch.setattr(lib_mod, "TRUSTED_NETWORKS", networks)
    yield networks


@pytest.fixture(autouse=True)
def _reset_db_globals():
    """Reset database module-level globals to prevent cached connections leaking across tests."""
    import w2v_common.database.connection as conn_mod
    import w2v_common.database.session as sess_mod

    conn_mod._engine = None
    sess_mod._async_session_maker = None
    yield
    conn_mod._engine = None
    sess_mod._async_session_maker = None


@pytest.fixture
def mock_settings():
    """Return a Settings instance for testing."""
    from src.config import Settings

    return Settings(
        environment="test",
        app_name="Content Service Test",
    )


# ---------------------------------------------------------------------------
# WM gateway mocking helpers
# ---------------------------------------------------------------------------


def make_wm_response(
    output: Any,
    *,
    output_type: str = "string",
    usage_metrics: dict[str, int] | None = None,
    metadata: dict[str, Any] | None = None,
) -> WMResponse:
    """Build a :class:`WMResponse` for tests that mock WMClient.message."""
    return WMResponse(
        output=output,
        output_type=output_type,
        output_schema_used=None,
        metadata=metadata,
        usage_metrics=usage_metrics,
    )


@pytest.fixture
def wm_response_factory():
    """Return a factory that builds WMResponse instances for tests."""
    return make_wm_response


@pytest.fixture
def patch_wm_message(monkeypatch):
    """Patch :meth:`WMClient.message` to return canned responses.

    Usage::

        async def test_thing(patch_wm_message):
            mock = patch_wm_message(make_wm_response("hello"))
            ...
            mock.assert_awaited_once()
    """

    def _install(*responses: Any) -> AsyncMock:
        from w2v_common.clients import WMClient

        if not responses:
            mock = AsyncMock(return_value=make_wm_response(""))
        elif len(responses) == 1:
            payload = responses[0]
            if isinstance(payload, WMResponse):
                mock = AsyncMock(return_value=payload)
            else:
                mock = AsyncMock(return_value=payload)
        else:
            mock = AsyncMock(side_effect=list(responses))
        monkeypatch.setattr(WMClient, "message", mock)
        return mock

    return _install


# ---------------------------------------------------------------------------
# Narrative-domain fixtures (from narrative_service conftest)
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_analysis():
    """Return a sample AnalysisInput for testing (narrative domain)."""
    from src.api.narrative_schemas import AnalysisInput, PageInput, SiteInput

    return AnalysisInput(
        pages=[
            PageInput(
                url="https://example.com/",
                title="Home",
                page_type="homepage",
                page_id="p1",
                headings=["Welcome to Example", "Our Amazing Features"],
                content_sections=[
                    {"section_type": "hero", "heading": "Welcome"},
                    {
                        "section_type": "features",
                        "heading": "Fast and Reliable",
                    },
                    {
                        "section_type": "testimonials",
                        "text_preview": "This product changed my business.",
                    },
                ],
                has_forms=True,
                has_media=True,
            ),
            PageInput(
                url="https://example.com/pricing",
                title="Pricing",
                page_type="pricing",
                page_id="p2",
                headings=["Simple, Transparent Pricing"],
                content_sections=[
                    {
                        "section_type": "pricing",
                        "heading": "Choose Your Plan",
                    },
                ],
            ),
            PageInput(
                url="https://example.com/about",
                title="About Us",
                page_type="about",
                page_id="p3",
                meta_description="Learn about our company",
            ),
        ],
        site=SiteInput(
            description="A great SaaS product",
            target_audience=["developers", "startups"],
            use_cases=["project management", "team collaboration"],
            tone="professional",
        ),
    )


@pytest.fixture
def sample_storyline():
    """Return a sample storyline dict for testing."""
    return {
        "video_title": "Example Demo",
        "video_description": "A tour of Example",
        "target_duration_minutes": 3.0,
        "tone": "professional",
        "intro": {
            "title": "Welcome",
            "subtitle": "See Example in action",
            "duration_seconds": 8.0,
            "narrative_text": "Discover how Example transforms your workflow.",
        },
        "chapters": [
            {
                "number": 1,
                "title": "Getting Started",
                "subtitle": "Quick setup",
                "pages": ["https://example.com/"],
                "narrative_arc": "Introduction to the product",
                "key_moments": ["hero section", "signup form"],
                "estimated_duration_seconds": 60.0,
            },
            {
                "number": 2,
                "title": "Pricing",
                "subtitle": "Plans for everyone",
                "pages": ["https://example.com/pricing"],
                "narrative_arc": "Explore pricing options",
                "key_moments": ["pricing tiers"],
                "estimated_duration_seconds": 45.0,
            },
        ],
        "outro": {
            "title": "Get Started Today",
            "subtitle": "Sign up now",
            "duration_seconds": 6.0,
            "narrative_text": "Start your free trial today.",
        },
    }


@pytest.fixture
def sample_user_context():
    """Return a sample UserContext for testing (narrative domain)."""
    from src.api.narrative_types import (
        FeaturePriority,
        NarrationGuidance,
        UserContext,
    )

    return UserContext(
        goals=["increase signups", "showcase features"],
        target_audience="developers",
        site_type="SaaS",
        feature_priorities=[
            FeaturePriority(feature="pricing", priority="high"),
            FeaturePriority(feature="blog", priority="skip"),
        ],
        narration_guidance=NarrationGuidance(
            tone="casual",
            key_messages=["fast setup", "reliable"],
            avoid_topics=["competitors"],
        ),
    )


# ---------------------------------------------------------------------------
# Script-domain fixtures (from script_generator_service unit conftest)
# ---------------------------------------------------------------------------


@pytest.fixture()
def shot_detail_factory():
    """Return a factory that builds ``ShotDetailInput`` instances."""

    from src.api.scripts_schemas import ShotDetailInput

    def _factory(**overrides: Any) -> ShotDetailInput:
        defaults: dict[str, Any] = {
            "id": "part-001",
            "part_type": "browser_recording",
            "title": "Sample Part",
            "pages": ["page-1"],
            "actions": ["click #btn"],
            "key_elements": [".hero"],
            "estimated_duration_seconds": 12.0,
            "narration_text": "This part demonstrates the main feature.",
        }
        defaults.update(overrides)
        return ShotDetailInput(**defaults)

    return _factory


@pytest.fixture()
def sample_shot(shot_detail_factory):
    """A ready-made ``ShotDetailInput`` with sensible defaults."""
    return shot_detail_factory()


@pytest.fixture()
def page_analysis_factory():
    """Return a factory that builds ``PageAnalysisInput`` instances."""

    from src.api.scripts_schemas import PageAnalysisInput

    def _factory(**overrides: Any) -> PageAnalysisInput:
        defaults: dict[str, Any] = {
            "page_id": "page-1",
            "url": "https://example.com",
            "page_type": "landing",
            "title": "Example Landing Page",
            "interactive_elements": [{"selector": "#cta", "type": "button"}],
            "content_sections": [{"heading": "Hero", "text": "Welcome"}],
            "navigation_elements": [{"label": "Home", "href": "/"}],
        }
        defaults.update(overrides)
        return PageAnalysisInput(**defaults)

    return _factory


@pytest.fixture()
def sample_theme():
    """A ``ThemeInput`` with the default colour palette."""
    from src.api.scripts_schemas import ThemeInput

    return ThemeInput()


@pytest.fixture()
def user_context_factory():
    """Return a factory that builds ``UserContextInput`` instances."""

    from src.api.scripts_schemas import NarrationGuidanceInput, UserContextInput

    def _factory(**overrides: Any) -> UserContextInput:
        defaults: dict[str, Any] = {
            "description": "An e-commerce storefront",
            "site_type": "ecommerce",
            "target_audience": "online shoppers",
            "goals": ["increase conversions", "showcase products"],
            "focus_pages": ["page-1"],
            "exclude_pages": [],
            "narration_guidance": NarrationGuidanceInput(
                tone="professional",
                key_messages=["Easy to use", "Secure checkout"],
            ),
        }
        defaults.update(overrides)
        return UserContextInput(**defaults)

    return _factory


# ---------------------------------------------------------------------------
# Real database fixtures (postgres+psycopg, TRUNCATE-based isolation)
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_engine():
    """Per-test async postgres engine with TRUNCATE-based isolation.

    Connects to _TEST_DATABASE_URL captured at module load (CI postgres in
    CI, localhost default locally). Runs Base.metadata.create_all (idempotent)
    then TRUNCATEs every model table so each test starts from a clean slate.
    """
    from src.database.models import Base

    engine = create_async_engine(_TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if Base.metadata.sorted_tables:
            tables = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
            await conn.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session_factory(db_engine):
    """Real async_sessionmaker for content_service tests."""
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def db_session(db_session_factory):
    """Real AsyncSession for a single test."""
    async with db_session_factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Real Redis fixtures (fakeredis)
# ---------------------------------------------------------------------------


@pytest.fixture
def real_redis_client():
    """RedisClient backed by fakeredis (real commands, in-memory)."""
    client = RedisClient(redis_url="redis://localhost:6379/0")
    inject_fakeredis(client)
    return client
