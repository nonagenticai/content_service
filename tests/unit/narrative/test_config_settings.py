"""Tests for all configuration settings classes."""

from typing import Any

import pytest
from pydantic import ValidationError
from pydantic_settings import BaseSettings

from src.config import (
    NarrationSettings,
    Settings,
    ShotlistSettings,
    StorylineSettings,
)


def _no_env[T: BaseSettings](cls: type[T], **kwargs: Any) -> T:
    """Instantiate a pydantic-settings class without loading .env files."""
    return cls(_env_file=None, **kwargs)


class TestWMTimeoutSettings:
    """Per-call WM timeouts on Settings — the only LLM-shaped knobs left.

    Provider/model/api-key live in worker_management; content_service no
    longer owns any LLM provider config.
    """

    def test_defaults(self) -> None:
        s = _no_env(Settings)
        # 900s, not 90s: WM runs narration with thinking ON (>10min measured),
        # and this key is unset in gitops so the code default IS production.
        assert s.narrative_llm_timeout_seconds == 900
        assert s.script_llm_timeout == 120.0

    def test_legacy_llm_fields_removed(self) -> None:
        """Old LLM provider fields should no longer exist on Settings."""
        s = _no_env(Settings)
        for removed in (
            "narrative_llm_model",
            "narrative_llm_base_url",
            "narrative_llm_api_key",
            "narrative_llm_max_tokens",
            "narrative_llm_temperature",
            "narrative_llm_provider",
            "script_llm_model",
            "script_llm_base_url",
            "script_llm_api_key",
            "script_llm_max_tokens",
            "script_llm_max_retries",
            "script_llm_provider_name",
            "script_llm_provider_type",
        ):
            assert not hasattr(s, removed), f"{removed} should be gone after WMClient migration"


class TestStorylineSettings:
    def test_defaults(self) -> None:
        s = StorylineSettings()
        assert s.target_duration_minutes == 3.0
        assert s.chapter_title_card_duration == 5.0
        assert s.duration_overage_threshold == 1.5

    def test_auto_detect_duration_zero(self) -> None:
        s = StorylineSettings(target_duration_minutes=0)
        assert s.target_duration_minutes == 0

    def test_valid_duration_range(self) -> None:
        s = StorylineSettings(target_duration_minutes=0.5)
        assert s.target_duration_minutes == 0.5
        s = StorylineSettings(target_duration_minutes=15.0)
        assert s.target_duration_minutes == 15.0

    def test_invalid_duration(self) -> None:
        with pytest.raises(ValidationError):
            StorylineSettings(target_duration_minutes=0.3)

    def test_invalid_duration_too_high(self) -> None:
        with pytest.raises(ValidationError):
            StorylineSettings(target_duration_minutes=20.0)

    def test_overage_threshold_minimum(self) -> None:
        with pytest.raises(ValidationError):
            StorylineSettings(duration_overage_threshold=0.5)


class TestNarrationSettings:
    def test_defaults(self) -> None:
        s = NarrationSettings()
        assert s.enabled is True
        assert s.tts_provider == "kokoro"
        assert s.voice == "af_heart"
        assert s.speed == 1.0
        assert s.words_per_minute == 150
        assert s.tts_timeout_seconds == 60.0
        assert s.duration_safety_margin == 0.85

    def test_tts_service_url_field_removed(self) -> None:
        """tts_service_url has been removed; URL now comes from w2v_common.discovery."""
        s = NarrationSettings()
        assert not hasattr(s, "tts_service_url"), (
            "tts_service_url moved to w2v_common.discovery.service_url('tts')."
        )

    def test_tts_url_resolved_via_discovery(self, monkeypatch) -> None:
        """The discovery helper provides the canonical TTS URL."""
        monkeypatch.setenv("W2V_TTS_URL", "http://tts.test:9999")
        from w2v_common.discovery import service_url

        assert service_url("tts") == "http://tts.test:9999"

    def test_tts_url_default_uses_k8s_dns(self, monkeypatch) -> None:
        monkeypatch.delenv("W2V_LOCAL", raising=False)
        monkeypatch.delenv("W2V_TTS_URL", raising=False)
        from w2v_common.discovery import service_url

        # tts replaced by mlx-Kokoro launchd on mini2 (Tailnet split-DNS, port 9202).
        assert service_url("tts") == "http://tts:9202"

    def test_invalid_wpm(self) -> None:
        with pytest.raises(ValidationError):
            NarrationSettings(words_per_minute=0)

    def test_invalid_timeout(self) -> None:
        with pytest.raises(ValidationError):
            NarrationSettings(tts_timeout_seconds=-1)


class TestShotlistSettings:
    def test_defaults(self) -> None:
        s = ShotlistSettings()
        assert s.ai_creative is True
        assert s.timeout_seconds == 120

    def test_invalid_timeout(self) -> None:
        with pytest.raises(ValidationError):
            ShotlistSettings(timeout_seconds=0)

    def test_valid_timeout(self) -> None:
        s = ShotlistSettings(timeout_seconds=60)
        assert s.timeout_seconds == 60
