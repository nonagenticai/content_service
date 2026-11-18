"""Tests for cache key construction and stable hashing."""

from __future__ import annotations

from typing import Any, ClassVar

from src.api.narrative._shared import _narration_cache_key_for, _storyline_cache_key_for
from src.api.narrative_schemas import (
    AnalysisInput,
    LLMConfigOverride,
    NarrationRequest,
    StorylineRequest,
)
from src.cache.cache_keys import (
    _stable_hash,
    job_status_key,
    narration_cache_key,
    shotlist_cache_key,
    storyline_cache_key,
)


class TestStableHash:
    def test_deterministic(self) -> None:
        """Same input always produces the same hash."""
        data = {"model": "gpt-4", "temperature": 0.7}
        assert _stable_hash(data) == _stable_hash(data)

    def test_different_inputs_produce_different_hashes(self) -> None:
        """Distinct inputs must not collide."""
        hash_a = _stable_hash({"x": 1})
        hash_b = _stable_hash({"x": 2})
        assert hash_a != hash_b

    def test_key_order_independent(self) -> None:
        """Dict key insertion order must not affect the hash."""
        hash_ab = _stable_hash({"a": 1, "b": 2})
        hash_ba = _stable_hash({"b": 2, "a": 1})
        assert hash_ab == hash_ba

    def test_hash_length_is_16(self) -> None:
        """SHA-256 hex digest is truncated to 16 characters."""
        h = _stable_hash({"key": "value"})
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)


class TestKeyConstruction:
    def test_job_status_key_prefix(self) -> None:
        """job_status_key must use the narrative:job:{tenant}:{id}:status pattern."""
        key = job_status_key("tenant-a", "abc-123")
        assert key == "narrative:job:tenant-a:abc-123:status"

    def test_storyline_cache_key_prefix(self) -> None:
        """storyline_cache_key must start with narrative:storyline:{tenant} prefix."""
        key = storyline_cache_key(
            tenant_id="tenant-a",
            analysis={"url": "https://example.com"},
            duration=60.0,
            message="Create a storyline",
        )
        assert key.startswith("narrative:storyline:tenant-a:")

    def test_shotlist_cache_key_prefix(self) -> None:
        """shotlist_cache_key must start with narrative:shotlist:{tenant} prefix."""
        key = shotlist_cache_key(
            tenant_id="tenant-a",
            storyline={"chapters": []},
            analysis={"pages": []},
        )
        assert key.startswith("narrative:shotlist:tenant-a:")

    def test_same_inputs_produce_same_keys(self) -> None:
        """Identical arguments must yield identical cache keys."""
        args: dict[str, Any] = {
            "tenant_id": "tenant-a",
            "analysis": {"url": "https://example.com"},
            "duration": 90.0,
            "message": "test message",
        }
        key_a = storyline_cache_key(**args)
        key_b = storyline_cache_key(**args)
        assert key_a == key_b


class TestTenantIsolation:
    """Different tenants must never share a cache key (RLS bypass guard)."""

    def test_job_status_key_differs_by_tenant(self) -> None:
        assert job_status_key("tenant-a", "job-1") != job_status_key("tenant-b", "job-1")

    def test_storyline_key_differs_by_tenant(self) -> None:
        common: dict[str, Any] = {
            "analysis": {"url": "https://example.com"},
            "duration": 60.0,
            "message": "m",
        }
        assert storyline_cache_key(tenant_id="tenant-a", **common) != storyline_cache_key(
            tenant_id="tenant-b", **common
        )

    def test_narration_key_differs_by_tenant(self) -> None:
        common: dict[str, Any] = {
            "storyline": {"chapters": []},
            "analysis": {"pages": []},
            "mode": "webapp_commercial",
            "message": "m",
        }
        assert narration_cache_key(tenant_id="tenant-a", **common) != narration_cache_key(
            tenant_id="tenant-b", **common
        )

    def test_shotlist_key_differs_by_tenant(self) -> None:
        common = {"storyline": {"chapters": []}, "analysis": {"pages": []}}
        assert shotlist_cache_key(tenant_id="tenant-a", **common) != shotlist_cache_key(
            tenant_id="tenant-b", **common
        )


class TestStorylineKeyInputs:
    """All output-determining inputs must change the storyline key."""

    _BASE: ClassVar[dict] = {
        "tenant_id": "t",
        "analysis": {"url": "https://example.com"},
        "duration": 60.0,
        "message": "m",
    }

    def test_mode_changes_key(self) -> None:
        a = storyline_cache_key(**self._BASE, mode="webapp_commercial")
        b = storyline_cache_key(**self._BASE, mode="critique_video")
        assert a != b

    def test_reviews_change_key(self) -> None:
        a = storyline_cache_key(**self._BASE, reviews=None)
        b = storyline_cache_key(**self._BASE, reviews={"seo": {"score": 80}})
        assert a != b

    def test_user_context_changes_key(self) -> None:
        a = storyline_cache_key(**self._BASE, user_context=None)
        b = storyline_cache_key(**self._BASE, user_context={"target_audience": "devs"})
        assert a != b


class TestNarrationKeyInputs:
    """All output-determining inputs must change the narration key."""

    _BASE: ClassVar[dict] = {
        "tenant_id": "t",
        "storyline": {"chapters": []},
        "analysis": {"pages": []},
        "mode": "webapp_commercial",
        "message": "m",
    }

    def test_synthesize_audio_changes_key(self) -> None:
        """CRITICAL: audio vs text-only narration must not collide."""
        text_only = narration_cache_key(**self._BASE, synthesize_audio=False)
        with_audio = narration_cache_key(**self._BASE, synthesize_audio=True)
        assert text_only != with_audio

    def test_mode_changes_key(self) -> None:
        base = dict(self._BASE)
        base.pop("mode")
        assert narration_cache_key(**base, mode="webapp_commercial") != narration_cache_key(
            **base, mode="critique_video"
        )

    def test_user_context_changes_key(self) -> None:
        a = narration_cache_key(**self._BASE, user_context=None)
        b = narration_cache_key(**self._BASE, user_context={"target_audience": "devs"})
        assert a != b

    def test_code_quality_facts_changes_key(self) -> None:
        a = narration_cache_key(**self._BASE, code_quality_facts=None)
        b = narration_cache_key(**self._BASE, code_quality_facts={"loc": 1000})
        assert a != b


class TestModelIdentityInKey:
    """``llm_config.model`` is output-determining (it selects the LLM) and MUST
    be part of the storyline/narration cache keys, otherwise a request billed for
    a cheap model is served a cached premium-model body (or vice versa)."""

    def _storyline_body(self, model: str | None) -> StorylineRequest:
        return StorylineRequest(
            analysis=AnalysisInput(),
            llm_config=(LLMConfigOverride(model=model) if model is not None else None),
        )

    def _narration_body(self, model: str | None) -> NarrationRequest:
        return NarrationRequest(
            storyline={"chapters": []},
            analysis=AnalysisInput(),
            llm_config=(LLMConfigOverride(model=model) if model is not None else None),
        )

    def test_storyline_key_differs_by_model(self) -> None:
        """Two requests identical except llm_config.model must NOT collide."""
        premium = _storyline_cache_key_for(self._storyline_body("premium-X"), "t")
        cheap = _storyline_cache_key_for(self._storyline_body("cheap-Y"), "t")
        assert premium != cheap

    def test_narration_key_differs_by_model(self) -> None:
        premium = _narration_cache_key_for(self._narration_body("premium-X"), "t")
        cheap = _narration_cache_key_for(self._narration_body("cheap-Y"), "t")
        assert premium != cheap

    def test_storyline_key_stable_when_model_absent(self) -> None:
        """A no-model request keys exactly as it did before model was added, so
        existing cached entries are not needlessly invalidated."""
        body = self._storyline_body(None)
        k1 = _storyline_cache_key_for(body, "t")
        k2 = _storyline_cache_key_for(body, "t")
        assert k1 == k2
        # None model hashes identically to the model-agnostic (pre-change) call.
        legacy = storyline_cache_key(
            tenant_id="t",
            analysis=AnalysisInput().model_dump(),
            duration=body.duration_minutes,
            message=body.message,
            mode=body.mode,
            reviews=None,
            user_context=None,
        )
        assert k1 == legacy

    def test_narration_key_stable_when_model_absent(self) -> None:
        body = self._narration_body(None)
        k1 = _narration_cache_key_for(body, "t")
        legacy = narration_cache_key(
            tenant_id="t",
            storyline={"chapters": []},
            analysis=AnalysisInput().model_dump(),
            mode=body.mode,
            message=body.message,
            synthesize_audio=body.synthesize_audio,
            user_context=None,
            code_quality_facts=None,
        )
        assert k1 == legacy

    def test_storyline_model_function_level_omit_when_none(self) -> None:
        """At the function level, model=None must equal the no-model default."""
        base: dict = {
            "tenant_id": "t",
            "analysis": {"url": "https://example.com"},
            "duration": 60.0,
            "message": "m",
        }
        assert storyline_cache_key(**base) == storyline_cache_key(**base, model=None)
        assert storyline_cache_key(**base) != storyline_cache_key(**base, model="premium-X")

    def test_storyline_empty_model_equals_none(self) -> None:
        """WM normalizes ``llm_config.model or None``, so ``model=""`` and no
        model produce IDENTICAL output — they MUST share one cache key (else a
        redundant recompute). ``if model:`` folds empty exactly as None.
        """
        base: dict = {
            "tenant_id": "t",
            "analysis": {"url": "https://example.com"},
            "duration": 60.0,
            "message": "m",
        }
        assert storyline_cache_key(**base, model="") == storyline_cache_key(**base, model=None)
        assert storyline_cache_key(**base, model="") == storyline_cache_key(**base)

    def test_narration_empty_model_equals_none(self) -> None:
        base: dict = {
            "tenant_id": "t",
            "storyline": {"a": 1},
            "analysis": {"url": "https://example.com"},
            "mode": "webapp_commercial",
            "message": "m",
        }
        assert narration_cache_key(**base, model="") == narration_cache_key(**base, model=None)
        assert narration_cache_key(**base, model="") == narration_cache_key(**base)
