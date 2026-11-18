"""Configuration management for content_service using Pydantic Settings.

Merged settings from narrative_service and script_generator_service.
Hierarchical settings with sub-configs.

LLM provider config is intentionally absent — content_service routes ALL
LLM calls through worker_management (WM) via :class:`WMClient`. WM picks
the underlying provider/model server-side; the only LLM-shaped knob the
service still owns is per-call timeout. See memory
``wm-as-sole-llm-gateway.md``.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from w2v_common.config import APISettings as BaseAPISettings
from w2v_common.config import AuthSettings as AuthSettings
from w2v_common.config import CacheSettings as BaseCacheSettings
from w2v_common.config import DatabaseSettings as BaseDatabaseSettings
from w2v_common.config import LoggingSettings as LoggingSettings
from w2v_common.config import RedisSettings as BaseRedisSettings
from w2v_common.config import StorageSettings as BaseStorageSettings

# ---------------------------------------------------------------------------
# Shared infrastructure settings (imported from w2v_common, with overrides)
# ---------------------------------------------------------------------------


class DatabaseSettings(BaseDatabaseSettings):
    """Database settings — DATABASE_URL required env (no localhost fallback)."""


class RedisSettings(BaseRedisSettings):
    """Redis settings — REDIS_URL required env (no localhost fallback)."""


class APISettings(BaseAPISettings):
    """API settings with content_service default port."""

    port: int = Field(default=6305, description="API server port")


class StorageSettings(BaseStorageSettings):
    """Storage settings with content_service defaults."""

    minio_bucket: str = Field(default="content-service", description="MinIO bucket name")


# ---------------------------------------------------------------------------
# Narrative-domain settings (from narrative_service)
# ---------------------------------------------------------------------------


class StorylineSettings(BaseSettings):
    """Storyline generation configuration."""

    target_duration_minutes: float = Field(
        default=3.0,
        description="Target video duration in minutes. 0 for auto-detection.",
    )
    chapter_title_card_duration: float = Field(
        default=5.0, description="Duration of chapter title cards in seconds"
    )
    duration_overage_threshold: float = Field(
        default=1.5,
        description="Multiplier against target_duration_minutes for max allowed duration",
    )
    max_retry_attempts: int = Field(
        default=3, description="Max LLM retry attempts for storyline generation"
    )
    high_priority_min_duration: float = Field(
        default=20.0, description="Minimum duration for high-priority chapters in seconds"
    )
    min_chapter_duration_seconds: float = Field(
        default=15.0, description="Minimum duration for any chapter in seconds"
    )

    @field_validator("target_duration_minutes")
    @classmethod
    def validate_target_duration(cls, v: float) -> float:
        if v != 0 and not 0.5 <= v <= 15.0:
            raise ValueError(f"target_duration_minutes must be 0 (auto) or 0.5-15.0, got {v}")
        return v

    @field_validator("duration_overage_threshold")
    @classmethod
    def validate_duration_overage_threshold(cls, v: float) -> float:
        if v < 1.0:
            raise ValueError(f"duration_overage_threshold must be >= 1.0, got {v}")
        return v

    model_config = SettingsConfigDict(
        env_prefix="STORYLINE_", env_file=".env", case_sensitive=False, extra="ignore"
    )


class NarrationSettings(BaseSettings):
    """Narration and TTS configuration."""

    enabled: bool = Field(default=True, description="Enable narration generation")
    tts_provider: str = Field(default="kokoro", description="TTS provider")
    voice: str = Field(default="af_heart", description="TTS voice identifier")
    speed: float = Field(default=1.0, description="TTS playback speed")
    words_per_minute: int = Field(default=150, description="Speaking rate for duration estimation")
    tts_timeout_seconds: float = Field(default=60.0, description="TTS generation timeout")
    max_retry_attempts: int = Field(
        default=2, description="Max LLM retry attempts for narration generation"
    )
    duration_safety_margin: float = Field(
        default=0.85,
        description="Safety margin for word targets (0.85 = aim for 85% of segment duration)",
    )

    @field_validator("duration_safety_margin")
    @classmethod
    def validate_duration_safety_margin(cls, v: float) -> float:
        if not 0.5 <= v <= 1.0:
            raise ValueError(f"duration_safety_margin must be between 0.5 and 1.0, got {v}")
        return v

    @field_validator("words_per_minute")
    @classmethod
    def validate_words_per_minute(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"words_per_minute must be >= 1, got {v}")
        return v

    @field_validator("tts_timeout_seconds")
    @classmethod
    def validate_tts_timeout_seconds(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"tts_timeout_seconds must be positive, got {v}")
        return v

    model_config = SettingsConfigDict(
        env_prefix="NARRATION_", env_file=".env", case_sensitive=False, extra="ignore"
    )


class CacheSettings(BaseCacheSettings):
    """Extended cache configuration for content_service domains."""

    storyline_ttl: int = Field(default=3600, description="Storyline cache TTL in seconds")
    shotlist_ttl: int = Field(default=86400, description="Shotlist cache TTL in seconds")
    narration_ttl: int = Field(default=3600, description="Narration cache TTL in seconds")


class ShotlistSettings(BaseSettings):
    """Shot list configuration."""

    ai_creative: bool = Field(default=True, description="Enable AI creative enhancement")
    timeout_seconds: int = Field(default=120, description="Enhancement timeout in seconds")
    min_part_duration_seconds: float = Field(
        default=20.0, description="Minimum duration for browser recording parts in seconds"
    )
    min_title_card_duration_seconds: float = Field(
        default=2.0, description="Minimum duration for title card parts in seconds"
    )

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout_seconds(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"timeout_seconds must be positive, got {v}")
        return v

    model_config = SettingsConfigDict(
        env_prefix="SHOTLIST_", env_file=".env", case_sensitive=False, extra="ignore"
    )


# ---------------------------------------------------------------------------
# Script-domain settings (from script_generator_service)
# ---------------------------------------------------------------------------


class GeneratorSettings(BaseSettings):
    """Script generation configuration."""

    parallel_workers: int = Field(default=8, description="Number of parallel generation workers")
    max_retries: int = Field(default=1, description="Max retries on completion token missing")
    completion_token: str = Field(
        default="<promise>COMPLETE</promise>", description="Token to verify complete output"
    )
    mode: str = Field(default="webapp_commercial", description="Generation mode")
    source_type: str = Field(default="web", description="Source type for generation")
    auth_enabled: bool = Field(default=False, description="Enable auth for generator")
    templates_dir: str | None = Field(default=None, description="Path to templates directory")
    scaffold_dir: str | None = Field(default=None, description="Path to scaffold directory")
    model_config = SettingsConfigDict(
        env_prefix="GENERATOR_", env_file=".env", case_sensitive=False, extra="ignore"
    )


class ScaffoldSettings(BaseSettings):
    """Scaffold project configuration."""

    enabled: bool = Field(default=True, description="Enable scaffold generation")
    npm_install: bool = Field(default=True, description="Run npm install in scaffold")
    npm_install_timeout: int = Field(default=120, description="npm install timeout in seconds")
    model_config = SettingsConfigDict(
        env_prefix="SCAFFOLD_", env_file=".env", case_sensitive=False, extra="ignore"
    )


class ValidationSettings(BaseSettings):
    """Script validation configuration."""

    ts_compile_timeout: int = Field(
        default=60, description="TypeScript compilation timeout in seconds"
    )
    discover_timeout: int = Field(default=30, description="Playwright test discovery timeout")
    dry_run_timeout: int = Field(default=120, description="Headless dry run timeout")
    max_improvement_attempts: int = Field(default=3, description="Max auto-fix attempts")
    error_truncation_limit: int = Field(default=2000, description="Max error message length")
    min_code_chars: int = Field(default=20, description="Minimum code characters for valid script")
    model_config = SettingsConfigDict(
        env_prefix="VALIDATION_", env_file=".env", case_sensitive=False, extra="ignore"
    )


# ---------------------------------------------------------------------------
# Root Settings
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """Root settings aggregating all sub-configurations for content_service."""

    app_name: str = Field(default="Content Service", description="Application name")
    app_version: str = Field(default="0.1.0", description="Application version")
    environment: str = Field(
        default="development", description="Environment (development, staging, production)"
    )

    # Shared infrastructure
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    api: APISettings = Field(default_factory=APISettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    # Narrative-domain settings
    storyline: StorylineSettings = Field(default_factory=StorylineSettings)
    narration: NarrationSettings = Field(default_factory=NarrationSettings)
    shotlist: ShotlistSettings = Field(default_factory=ShotlistSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)

    # ----- WM gateway tunables -----
    # The only LLM-shaped knobs content_service still owns are timeouts —
    # provider/model/api_key live in worker_management (WM is the sole LLM
    # gateway). Per-request model overrides are still honored via
    # LLMConfigOverride.model on the API and forwarded to WM.
    # 900s, NOT 90s. The old 90s default was unreachable in production: WM runs
    # these narration calls with thinking ON (w2v cannot turn it off on the
    # pydantic_ai path), and the same prompt has been measured at >10 minutes
    # with thinking vs ~28s without. At 90s every chapter call timed out, every
    # chapter fell back to boilerplate, and the job still reported
    # ``status="completed"``. This key is also UNSET in gitops
    # (``clusters/strato/services/w2v/configmaps.yaml`` carries no LLM key for
    # content_service), so the code default IS the production value — there is
    # no ConfigMap override silently saving us. If the ConfigMap ever gains one
    # it must be ``NARRATIVE_LLM_TIMEOUT_SECONDS: "900"``.
    #
    # Upper bound: WM's own ``AGENT_RUN_TIMEOUT_S`` is 1200. Staying below it
    # means WM's real error surfaces rather than a client-side cut that hides
    # the cause.
    narrative_llm_timeout_seconds: int = Field(
        default=900,
        validation_alias=AliasChoices("NARRATIVE_LLM_TIMEOUT_SECONDS", "LLM_TIMEOUT_SECONDS"),
        description="Per-call timeout for narrative-domain WM calls (seconds)",
    )
    script_llm_timeout: float = Field(
        default=120.0,
        validation_alias=AliasChoices("SCRIPT_LLM_TIMEOUT", "LLM_TIMEOUT"),
        description="Per-call timeout for script-domain WM calls (seconds)",
    )

    # Script-domain settings
    generator: GeneratorSettings = Field(default_factory=GeneratorSettings)
    validation: ValidationSettings = Field(default_factory=ValidationSettings)
    scaffold: ScaffoldSettings = Field(default_factory=ScaffoldSettings)

    # ----- Stale-job reconciler (crash backstop) -----
    # Periodic sweep that fails out rows stuck in ``processing`` after a worker
    # crash/OOM. See src/services/reconciler.py.
    reconciler_enabled: bool = Field(
        default=True, description="Enable the periodic stale-job reconciler loop"
    )
    reconciler_interval_seconds: int = Field(
        default=300, description="Seconds between reconciler sweeps"
    )
    reconciler_stale_after_seconds: int = Field(
        default=1800,
        description="A 'processing' job older than this many seconds is reconciled to 'failed'",
    )
    # 'pending' needs its OWN, far longer cutoff. Unlike 'processing', pending is a
    # LEGITIMATE queued state — TaskManager bounds concurrency at
    # generator.parallel_workers, so a large batch can hold real work in 'pending'
    # for a long time and reusing the 1800s figure would fail out live queued jobs.
    # 6h is far beyond any real queue wait, while still bounding the rows that
    # TaskManager's shutdown path drops on the floor (see reconciler.py).
    reconciler_pending_stale_after_seconds: int = Field(
        default=21600,
        description="A 'pending' job older than this many seconds never started; fail it out",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    @model_validator(mode="after")
    def validate_settings(self) -> Settings:
        """Validate cross-field constraints."""
        if self.api.port == 8000:
            raise ValueError("API port 8000 not allowed. Default: 6305.")
        return self

    def get_database_url(self) -> str:
        """Return the configured PostgreSQL database URL."""
        return self.database.url

    def get_redis_url(self) -> str:
        """Return the configured Redis connection URL."""
        return self.redis.url

    def is_production(self) -> bool:
        """Return True if the environment is production."""
        return self.environment.lower() == "production"

    def is_development(self) -> bool:
        """Return True if the environment is development."""
        return self.environment.lower() == "development"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


def clear_settings_cache() -> None:
    """Clear the cached settings instance (useful for testing)."""
    get_settings.cache_clear()
