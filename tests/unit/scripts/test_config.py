"""Tests for src/config.py — Pydantic Settings configuration."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic_settings import BaseSettings

from src.config import (
    APISettings,
    AuthSettings,
    DatabaseSettings,
    GeneratorSettings,
    LoggingSettings,
    RedisSettings,
    ScaffoldSettings,
    Settings,
    StorageSettings,
    ValidationSettings,
    clear_settings_cache,
    get_settings,
)


def _no_env[T: BaseSettings](cls: type[T], **kwargs: Any) -> T:
    """Instantiate a pydantic-settings class without loading .env files.

    ``_env_file`` is a valid pydantic-settings runtime argument but is not
    visible to static type checkers.  Centralising the call here keeps the
    single unavoidable ``ty: ignore`` in one place instead of every test.
    """
    return cls(_env_file=None, **kwargs)


# ── Sub-config defaults ─────────────────────────────────────────────────


class TestDatabaseSettings:
    def test_defaults(self) -> None:
        s = _no_env(DatabaseSettings)
        assert "psycopg" in s.url
        assert s.pool_size == 10
        assert s.max_overflow == 20
        assert s.pool_timeout == 30
        assert s.pool_recycle == 3600
        assert s.echo is False


class TestAuthSettings:
    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTH_ENABLED", raising=False)
        s = _no_env(AuthSettings)
        assert s.enabled is True
        assert s.ssl_verify is True
        # ``AuthSettings.default_tenant_id`` is DELETED as of w2v-common 0.67.0.
        # It held the nil-UUID sentinel that every unattributed request was
        # silently attributed to. The assertion is replaced by its inverse rather
        # than dropped: if a future bump reintroduces a configurable default
        # tenant, that is a regression to the sentinel design and this fails.
        assert not hasattr(s, "default_tenant_id")


class TestRedisSettings:
    def test_defaults(self) -> None:
        s = _no_env(RedisSettings)
        assert "redis://" in s.url
        assert s.max_connections == 10
        assert s.socket_timeout == 5
        assert s.socket_connect_timeout == 5
        assert s.decode_responses is True


class TestAPISettings:
    def test_defaults(self) -> None:
        s = _no_env(APISettings)
        assert s.host == "0.0.0.0"
        assert s.port == 6305
        assert s.debug is False
        assert s.reload is False
        assert s.cors_allow_credentials is True

    def test_parse_cors_origins_list(self) -> None:
        """A list of origins should be accepted directly."""
        s = APISettings(cors_origins=["http://a.com", "http://b.com"])
        assert s.cors_origins == ["http://a.com", "http://b.com"]

    def test_parse_cors_origins_empty_list(self) -> None:
        s = APISettings(cors_origins=[])
        assert s.cors_origins == []

    def test_parse_cors_origins_single(self) -> None:
        s = APISettings(cors_origins=["http://x.com"])
        assert s.cors_origins == ["http://x.com"]

    def test_parse_allow_methods_list(self) -> None:
        s = APISettings(cors_allow_methods=["GET", "POST"])
        assert s.cors_allow_methods == ["GET", "POST"]

    def test_parse_allow_headers_list(self) -> None:
        s = APISettings(cors_allow_headers=["Authorization", "Content-Type"])
        assert s.cors_allow_headers == ["Authorization", "Content-Type"]


class TestStorageSettings:
    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("STORAGE_LOCAL_PATH", raising=False)
        s = _no_env(StorageSettings)
        assert s.backend == "local"
        assert s.local_path == "/app/data"


class TestWMTimeoutSettings:
    """Per-call WM timeouts — the only LLM-shaped knobs left on Settings.

    Provider/model/api-key live in worker_management; content_service no
    longer owns LLM provider config.
    """

    def test_defaults(self) -> None:
        s = _no_env(Settings)
        assert s.script_llm_timeout == 120.0
        # 900s, not 90s: WM runs narration with thinking ON (>10min measured),
        # and this key is unset in gitops so the code default IS production.
        assert s.narrative_llm_timeout_seconds == 900

    def test_legacy_llm_fields_removed(self) -> None:
        """Old script_llm_* provider fields are gone after the WMClient migration."""
        s = _no_env(Settings)
        for removed in (
            "script_llm_model",
            "script_llm_base_url",
            "script_llm_api_key",
            "script_llm_max_tokens",
            "script_llm_max_retries",
            "script_llm_provider_name",
            "script_llm_provider_type",
            "script_llm_temperature",
            "script_llm_retry_base_delay",
            "script_llm_retry_max_delay",
        ):
            assert not hasattr(s, removed), f"{removed} should be removed in WMClient migration"
        assert not hasattr(s, "get_script_llm_primary_config")


class TestGeneratorSettings:
    def test_defaults(self) -> None:
        s = _no_env(GeneratorSettings)
        assert s.parallel_workers == 8
        assert s.max_retries == 1
        assert "COMPLETE" in s.completion_token
        assert s.mode == "webapp_commercial"
        assert s.source_type == "web"
        assert s.auth_enabled is False
        assert s.templates_dir is None
        assert s.scaffold_dir is None


class TestScaffoldSettings:
    def test_defaults(self) -> None:
        s = _no_env(ScaffoldSettings)
        assert s.enabled is True
        assert s.npm_install is True
        assert s.npm_install_timeout == 120


class TestValidationSettings:
    def test_defaults(self) -> None:
        s = _no_env(ValidationSettings)
        assert s.ts_compile_timeout == 60
        assert s.discover_timeout == 30
        assert s.dry_run_timeout == 120
        assert s.max_improvement_attempts == 3
        assert s.error_truncation_limit == 2000
        assert s.min_code_chars == 20


class TestLoggingSettings:
    def test_defaults(self) -> None:
        s = _no_env(LoggingSettings)
        assert s.level == "INFO"
        assert s.json_logs is False

    def test_valid_level(self) -> None:
        s = LoggingSettings(level="debug")
        assert s.level == "DEBUG"

    def test_invalid_level_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid log level"):
            LoggingSettings(level="NOTREAL")


# ── Root Settings ────────────────────────────────────────────────────────


class TestSettings:
    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        s = _no_env(Settings)
        assert s.app_name == "Content Service"
        assert s.app_version == "0.1.0"
        assert s.environment == "development"
        assert isinstance(s.database, DatabaseSettings)
        assert isinstance(s.auth, AuthSettings)
        assert isinstance(s.redis, RedisSettings)
        assert isinstance(s.api, APISettings)
        assert isinstance(s.storage, StorageSettings)
        assert isinstance(s.script_llm_timeout, float) and s.script_llm_timeout > 0
        assert isinstance(s.narrative_llm_timeout_seconds, int)
        assert isinstance(s.generator, GeneratorSettings)
        assert isinstance(s.validation, ValidationSettings)
        assert isinstance(s.scaffold, ScaffoldSettings)
        assert isinstance(s.logging, LoggingSettings)

    def test_port_8000_rejected(self) -> None:
        with pytest.raises(ValueError, match="8000 is reserved"):
            _no_env(Settings, api=APISettings(port=8000))

    def test_get_database_url(self) -> None:
        s = Settings()
        assert s.get_database_url() == s.database.url

    def test_get_redis_url(self) -> None:
        s = Settings()
        assert s.get_redis_url() == s.redis.url

    def test_is_production(self) -> None:
        s = Settings(environment="production")
        assert s.is_production() is True
        assert s.is_development() is False

    def test_is_development(self) -> None:
        s = Settings(environment="development")
        assert s.is_development() is True
        assert s.is_production() is False

    def test_is_production_case_insensitive(self) -> None:
        s = Settings(environment="Production")
        assert s.is_production() is True


# ── get_settings / clear_settings_cache ──────────────────────────────────


class TestGetSettings:
    def test_returns_settings_instance(self) -> None:
        clear_settings_cache()
        s = get_settings()
        assert isinstance(s, Settings)

    def test_cached_returns_same_instance(self) -> None:
        clear_settings_cache()
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

    def test_clear_cache_resets(self) -> None:
        clear_settings_cache()
        s1 = get_settings()
        clear_settings_cache()
        s2 = get_settings()
        # After clearing, a new instance is created
        assert s1 is not s2
