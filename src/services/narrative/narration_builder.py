"""Generate voice-over narration scripts via the WM LLM gateway.

w2v services NEVER call openai/anthropic/inference-router directly — every
LLM call goes through ``worker_management.POST /workers/{name}/message``
so cost is attributed per tenant and providers can be swapped server-side.
See memory ``wm-as-sole-llm-gateway.md`` for the canonical pattern.
"""

from __future__ import annotations

import asyncio
import base64
import contextvars
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

import jinja2
from w2v_common.auth.tenant_licence_lane import TenantLicenceLane
from w2v_common.clients import WMClient

from src.api.narrative_schemas import AnalysisInput, LLMConfigOverride
from src.api.narrative_types import CodeQualityFacts, UserContext
from src.config import Settings, get_settings

if TYPE_CHECKING:
    from w2v_common.providers import TenantSpeech

# Per-request token accumulator scoped via ContextVar so concurrent calls to
# the shared NarrationBuilder app-state singleton cannot corrupt each other's
# totals. ContextVar values are inherited by asyncio.gather() child coroutines
# inside the same Task and isolated between Tasks (each FastAPI request runs
# in its own Task).
_token_accumulator: contextvars.ContextVar[dict[str, int] | None] = contextvars.ContextVar(
    "narration_builder_token_accumulator", default=None
)


def _accumulate_usage(usage: dict[str, int]) -> None:
    """Add usage counters into the current request's accumulator (no-op if absent)."""
    acc = _token_accumulator.get()
    if acc is None:
        return
    for k in acc:
        acc[k] += usage.get(k, 0)


# Worker name handling narration generation. WM auto-creates the worker on
# first call and the per-call ``system_prompt`` steers behavior; no PUT
# required up-front.
_NARRATION_WORKER_NAME = "narration-builder"

_NARRATION_SYSTEM_PROMPT = (
    "You are a professional voice-over scriptwriter for product demo videos. "
    "Write engaging, natural narration text that pairs with screen recordings. "
    "Output ONLY the narration text — no markdown, no preamble, no stage directions."
)

logger = logging.getLogger(__name__)

DEFAULT_WORDS_PER_MINUTE = 150

# Minimum acceptable Flesch-Kincaid grade level for narration text.
# Grade < 7 means overly simple prose; target is 8-12 (high school).
_FK_GRADE_MIN = 7.0

# Fan-out ceilings for the two per-request gather() sites below.
#
# Both iterate over request-derived collections (`storyline["chapters"]`, and
# the parts list derived from them) with no upper bound, so an unbounded
# gather() lets one caller open N simultaneous downstream requests. There is no
# concurrency knob in src/config.py that fits: GeneratorSettings.parallel_workers
# is the script-generation worker pool, a different domain and a different
# downstream. Hence module constants.
#
# 4 for LLM: every chapter is one worker_management call, and WM fronts a single
# small vLLM device. Beyond ~4 in flight the extra requests only queue
# server-side while still burning the shared per-call budget
# (`narrative_llm_timeout_seconds`, 90s), so a wide fan-out converts "slow" into
# "all chapters time out at once".
#
# 4 for TTS: tts.test is a single-device kokoro unit with the same
# queue-not-parallelise behaviour against a 60s `tts_timeout_seconds`, and each
# in-flight synthesis also pins a connection in the per-request httpx pool.
_MAX_CONCURRENT_CHAPTER_LLM_CALLS = 4
_MAX_CONCURRENT_TTS_CALLS = 4


def _tenant_audio_bytes(speech: TenantSpeech) -> bytes:
    """A tenant ``tts`` slot's synth as the part's audio bytes (same type as the platform)."""
    return speech.audio_bytes


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences using simple regex."""
    if not text.strip():
        return []
    parts = re.split(r"[.!?]+\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _compute_fk_grade(text: str) -> float:
    """Compute a simplified Flesch-Kincaid grade level.

    Same formula used by the evaluation rubric in narrative.py.
    """
    sentences = _split_sentences(text)
    words = text.split()
    if not sentences or not words:
        return 0.0
    avg_sentence_len = len(words) / len(sentences)
    avg_word_len = sum(len(w) for w in words) / len(words)
    grade = 0.39 * avg_sentence_len + 11.8 * (avg_word_len / 5.0) - 15.59
    return max(0.0, min(grade, 20.0))


def _safe_int(val: object) -> int:
    """Coerce *val* to ``int``, returning ``0`` for non-numeric types."""
    if isinstance(val, int):
        return val
    if isinstance(val, (float, str)):
        try:
            return int(val)
        except TypeError, ValueError:
            return 0
    try:
        return int(str(val))
    except TypeError, ValueError:
        return 0


def _extract_usage_from_metrics(metrics: dict[str, object] | None) -> dict[str, int]:
    """Extract token usage from a WM ``usage_metrics`` dict."""
    if not metrics:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return {
        "prompt_tokens": _safe_int(metrics.get("prompt_tokens", 0)),
        "completion_tokens": _safe_int(metrics.get("completion_tokens", 0)),
        "total_tokens": _safe_int(metrics.get("total_tokens", 0)),
    }


_PREAMBLE_PATTERNS = re.compile(
    r"^(Here is|Here's|Sure,|Sure!|Below is|Certainly|Of course)[^\n]*\n",
    re.IGNORECASE,
)

_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates" / "narration"

# Modes whose narration templates are part-level only (no chapter-level template).
# For chapter narration, these modes fall back to chapter_narration.md.
_PART_ONLY_TEMPLATE_MODES = {"critique_video", "review_video"}

_KNOWN_TEMPLATE_VARS = {
    "chapter_title",
    "chapter_subtitle",
    "chapter_pages",
    "narrative_arc",
    "key_moments",
    "tone",
    "hooks",
    "feature_highlights",
    "estimated_duration_seconds",
    "word_target",
    "code_quality_summary",
    "primary_language",
    "loc",
    "maintainability_score",
    "top_issues_summary",
    "part_title",
    "part_actions",
    "part_pages",
    "mode",
    "position_in_chapter",
    "previous_narration",
}


def _build_chapter_fallback_text(chapter: dict) -> str:
    title = chapter.get("title", "This section")
    arc = chapter.get("narrative_arc", "")
    pages = chapter.get("pages", [])
    moments = chapter.get("key_moments", [])
    parts_text = [f"Let's take a closer look at {title}."]
    if arc:
        parts_text.append(arc)
    if moments:
        parts_text.append("Key highlights include " + ", ".join(moments[:3]) + ".")
    if pages:
        parts_text.append(
            f"We'll explore {len(pages)} page{'s' if len(pages) > 1 else ''} "
            "to see how everything comes together."
        )
    parts_text.append(
        f"As you can see, {title} brings together the tools and features you need to be productive."
    )
    return " ".join(parts_text)


def clean_narration_text(raw: str) -> str:
    """Strip markdown fences and common AI preamble from raw Claude output."""
    text = raw.strip()
    text = _PREAMBLE_PATTERNS.sub("", text).strip()
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


class NarrationBuilder:
    """Generate narration text for each chapter via the WM LLM gateway."""

    def __init__(
        self,
        settings: Settings | None = None,
        wm_client: WMClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.words_per_minute = self.settings.narration.words_per_minute
        # One shared WMClient per builder — its underlying httpx.AsyncClient
        # is connection-pooled and the identity event hook reads request-scoped
        # ContextVars, so the same instance serves concurrent end-user
        # requests correctly.
        self._wm = (
            wm_client
            if wm_client is not None
            else WMClient(timeout=float(self.settings.narrative_llm_timeout_seconds))
        )
        # MODALITY_GATEWAY_AUTH_MODE (w2v_common TenantLicenceLane): shared
        # (default, unchanged) or tenant_licence, where narration TTS carries the
        # bound tenant's own minted w2v licence instead of a forwarded/shared
        # token. Built here (the builder is built at boot, asgi.py), so a typo or
        # a missing BUNDLE_MINTER_* setting fails the pod, not a narration.
        self._tts_lane = TenantLicenceLane("modality_gateway")
        # Token totals are NOT stored as instance state: ``generate()`` and
        # ``generate_for_shot_list()`` set up a per-call accumulator in
        # ``_token_accumulator`` (a ContextVar) so concurrent requests against
        # this shared instance get isolated counts. See module docstring.

    @staticmethod
    def _resolve_model_override(llm_config: LLMConfigOverride | None) -> str | None:
        """Pick the model override string for this request, if any."""
        if llm_config is None:
            return None
        return llm_config.model or None

    def _resolve_tone_and_message(
        self,
        storyline: dict,
        user_context: UserContext | None,
        message: str,
    ) -> tuple[str, str]:
        """Pick the narration tone and prepend any user-context preamble to the message."""
        tone = storyline.get("tone", "professional")
        if user_context and user_context.narration_guidance.tone:
            tone = user_context.narration_guidance.tone

        if user_context:
            context_msg = self._build_narration_context(user_context)
            if context_msg:
                message = f"{context_msg}\n\n{message}" if message else context_msg
        return tone, message

    def _make_part(self, part_id: str, text: str) -> dict:
        """Build a narration part dict with word count and duration estimate."""
        word_count = len(text.split())
        return {
            "part_id": part_id,
            "text": text,
            "estimated_duration_seconds": word_count / self.words_per_minute * 60,
            "word_count": word_count,
        }

    async def _build_bookend_part(
        self,
        storyline: dict,
        kind: str,
        tone: str,
        message: str,
        model_override: str | None,
    ) -> dict | None:
        """Build the intro/outro narration part, or None if absent."""
        section = storyline.get(kind, {})
        text = (section.get("narrative_text") or "").strip()
        if not text:
            return None
        rewritten, degraded = await self._rewrite_bookend(
            text,
            kind,
            storyline.get("video_title", ""),
            tone,
            message,
            model_override=model_override,
        )
        part = self._make_part(kind, rewritten)
        # Fail-open visibility: an un-rewritten stage direction is NOT narration.
        # Every other fallback in this builder marks itself; without this one an
        # intro/outro WM failure is invisible to ``_has_degraded_parts`` and the
        # whole narration is written to the hour-long result cache.
        part["degraded"] = degraded
        return part

    def _fallback_chapter_part(self, chapter: dict) -> dict:
        """Return a deterministic fallback narration part when LLM generation fails."""
        ch_num = chapter.get("number", "?")
        logger.warning("Narration failed for chapter %s, using fallback", ch_num)
        fallback_text = (
            f"In this section we take a look at "
            f"{chapter.get('title', 'this feature')}. "
            f"{chapter.get('subtitle') or chapter.get('narrative_arc', '')}"
        )
        part = self._make_part(f"ch{chapter.get('number', 0):02d}-narration", fallback_text)
        # Fail-open visibility: mark deterministic fallback parts so callers can
        # distinguish placeholder content from real LLM narration.
        part["degraded"] = True
        return part

    async def _build_chapter_parts(
        self,
        chapters: list[dict],
        tone: str,
        message: str,
        mode: str,
        code_quality_facts: CodeQualityFacts | None,
        model_override: str | None,
    ) -> list[dict]:
        """Generate narration for every chapter in parallel, falling back per-chapter on error."""
        if not chapters:
            return []

        # Per-call (not module-level) so the Semaphore binds to the running
        # loop and one request's fan-out cannot starve another's.
        gate = asyncio.Semaphore(_MAX_CONCURRENT_CHAPTER_LLM_CALLS)

        async def _safe_chapter_narration(chapter: dict) -> dict:
            # Catch every exception type: this is the per-chapter absorb point and
            # the documented contract is "fall back to a deterministic part, never
            # take out sibling chapters". A narrow tuple here (its previous shape:
            # OSError, TimeoutError, ValueError, RuntimeError) lets anything off-list
            # — KeyError, AttributeError, custom domain errors — escape into the
            # gather() below and cancel every other chapter via task cancellation.
            async with gate:
                try:
                    return await self._generate_chapter_narration(
                        chapter,
                        tone,
                        message,
                        mode=mode,
                        code_quality_facts=code_quality_facts,
                        model_override=model_override,
                    )
                except Exception as exc:  # noqa: BLE001 — per-chapter absorb, see comment above
                    logger.warning(
                        "Narration for chapter %s raised %s: %s — using fallback",
                        chapter.get("number", "?"),
                        type(exc).__name__,
                        exc,
                    )
                    return self._fallback_chapter_part(chapter)

        # One coroutine per chapter, but only _MAX_CONCURRENT_CHAPTER_LLM_CALLS
        # of them hold the gate (and therefore an in-flight WM call) at a time.
        #
        # return_exceptions=True is belt-and-braces: the inner except Exception
        # should always absorb, but if a BaseException (e.g. CancelledError) ever
        # slips, this prevents one chapter's failure from cancelling siblings.
        results = await asyncio.gather(
            *[_safe_chapter_narration(ch) for ch in chapters],
            return_exceptions=True,
        )
        return [
            r if isinstance(r, dict) else self._fallback_chapter_part(ch)
            for ch, r in zip(chapters, results, strict=True)
        ]

    async def generate(
        self,
        storyline: dict,
        analysis: AnalysisInput,
        user_context: UserContext | None = None,
        message: str = "",
        synthesize_audio: bool = False,
        mode: str = "webapp_commercial",
        code_quality_facts: CodeQualityFacts | None = None,
        llm_config: LLMConfigOverride | None = None,
        license_token: str | None = None,
    ) -> dict:
        """Generate narration for every chapter in the storyline.

        Returns a dict matching NarrationScript structure:
        {parts, total_words, estimated_total_duration_seconds, tone}
        """
        local_tokens: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        token = _token_accumulator.set(local_tokens)
        try:
            return await self._generate_inner(
                storyline,
                analysis,
                user_context,
                message,
                synthesize_audio,
                mode,
                code_quality_facts,
                llm_config,
                license_token,
                local_tokens,
            )
        finally:
            _token_accumulator.reset(token)

    async def _generate_inner(
        self,
        storyline: dict,
        analysis: AnalysisInput,
        user_context: UserContext | None,
        message: str,
        synthesize_audio: bool,
        mode: str,
        code_quality_facts: CodeQualityFacts | None,
        llm_config: LLMConfigOverride | None,
        license_token: str | None,
        local_tokens: dict[str, int],
    ) -> dict:
        t0 = time.monotonic()
        chapters = storyline.get("chapters", [])
        logger.info(
            "NarrationBuilder.generate: mode=%s, chapters=%d, tts=%s",
            mode,
            len(chapters),
            synthesize_audio,
        )

        tone, message = self._resolve_tone_and_message(storyline, user_context, message)

        model_override = self._resolve_model_override(llm_config)

        parts: list[dict] = []

        intro_part = await self._build_bookend_part(
            storyline, "intro", tone, message, model_override
        )
        if intro_part is not None:
            parts.append(intro_part)

        parts.extend(
            await self._build_chapter_parts(
                chapters, tone, message, mode, code_quality_facts, model_override
            )
        )

        outro_part = await self._build_bookend_part(
            storyline, "outro", tone, message, model_override
        )
        if outro_part is not None:
            parts.append(outro_part)

        # TTS audio synthesis (fail-open per part)
        if synthesize_audio:
            await self._synthesize_parts(parts, license_token=license_token)

        total_words = sum(p["word_count"] for p in parts)
        elapsed = time.monotonic() - t0
        logger.info(
            "NarrationBuilder.generate: completed in %.2fs, %d parts, %d words",
            elapsed,
            len(parts),
            total_words,
        )
        result: dict = {
            "parts": parts,
            "total_words": total_words,
            "estimated_total_duration_seconds": total_words / self.words_per_minute * 60,
            "tone": tone,
        }
        if local_tokens["total_tokens"] > 0:
            result["token_usage"] = dict(local_tokens)
        return result

    async def _synthesize_parts(
        self, parts: list[dict], *, license_token: str | None = None
    ) -> None:
        """Synthesize audio for each part via TTS in parallel (fail-open per part).

        ``license_token`` (the request-scoped token) is forwarded to the TTS
        client so it authenticates via ``X-Tenant-Id: Bearer <token>`` rather
        than relying on the trusted-network fallback.
        """
        from w2v_common.discovery import service_url
        from w2v_common.providers import TTSRequest, tts_synthesize

        from src.services.narrative.tts_client import TTSClient

        narration = self.settings.narration
        tts = TTSClient(
            base_url=service_url("tts"),
            timeout=narration.tts_timeout_seconds,
            license_token=license_token,
            licence_lane=self._tts_lane,
        )

        # Per-call (not module-level) so the Semaphore binds to the running loop.
        gate = asyncio.Semaphore(_MAX_CONCURRENT_TTS_CALLS)

        async def _synthesize_one(part: dict) -> None:
            text = part.get("text", "")
            if not text.strip():
                return
            async with gate:
                try:
                    # The run's ``tts`` provider slot: W2V_PROVIDER_SLOTS=off (default)
                    # or a ``deployment`` slot awaits exactly the platform call below;
                    # enforce + a tenant slot synthesizes on the tenant's own provider
                    # (its URI + Bearer token; WAV bytes, same as the platform).
                    audio_bytes = await tts_synthesize(
                        TTSRequest(text=text, voice=narration.voice, speed=narration.speed),
                        platform=lambda: tts.generate(
                            text,
                            voice=narration.voice,
                            provider=narration.tts_provider,
                            speed=narration.speed,
                        ),
                        from_tenant=_tenant_audio_bytes,
                    )
                    part["audio_base64"] = base64.b64encode(audio_bytes).decode("ascii")
                except Exception as exc:  # noqa: BLE001 — per-part fail-open is the contract
                    # Previous narrow tuple (OSError, TimeoutError, ValueError, RuntimeError)
                    # let httpx.HTTPError (RequestError + HTTPStatusError) escape — those are
                    # the most common TTS failure modes — and one bad part cancelled every
                    # sibling synthesis via gather() task cancellation.
                    logger.warning(
                        "TTS synthesis failed for part %s: %s: %s",
                        part.get("part_id", "?"),
                        type(exc).__name__,
                        exc,
                    )
                    # Fail-open visibility: a part with text but NO audio is a
                    # SILENT segment in the rendered video. Unmarked it is shaped
                    # exactly like a healthy part, ``_has_degraded_parts`` sees
                    # nothing, and the whole narration is written to the
                    # hour-long result cache -- so every identical re-request
                    # replays the silence and a retry cannot heal it. Assign only
                    # True: a text fallback upstream may already have set it.
                    part["degraded"] = True

        # One coroutine per part, but only _MAX_CONCURRENT_TTS_CALLS of them hold
        # the gate (and therefore an in-flight TTS request) at a time.
        #
        # return_exceptions=True is belt-and-braces against any BaseException slipping
        # through the inner Exception absorb. _synthesize_one returns None on success
        # and on absorbed failure, so we have nothing to do with the results.
        #
        # try/finally guarantees the per-request TTSClient's underlying httpx
        # connection pool is closed on every path (success, absorbed failure, or a
        # BaseException like CancelledError) — otherwise each narration job that
        # synthesises audio leaked sockets/a pool.
        try:
            results = await asyncio.gather(
                *[_synthesize_one(p) for p in parts],
                return_exceptions=True,
            )
            for part, r in zip(parts, results, strict=True):
                if isinstance(r, BaseException):
                    logger.warning(
                        "TTS synthesis raised unexpectedly for part %s: %s: %s",
                        part.get("part_id", "?"),
                        type(r).__name__,
                        r,
                    )
                    # Same contract as the inner absorb above: no audio => degraded.
                    part["degraded"] = True
        finally:
            await tts.close()

    @staticmethod
    def _chapter_prompt_with_retry(base_prompt: str, attempt: int, prev_text: str) -> str:
        if attempt <= 1:
            return base_prompt
        return base_prompt + (
            "\n## CRITICAL: Reading Level Requirement\n\n"
            f"Your previous narration scored FK grade {_compute_fk_grade(prev_text):.1f} "
            f"(target: 8-12). It is TOO SIMPLE.\n"
            "You MUST:\n"
            "- Use compound and complex sentences with subordinate clauses\n"
            "- Include domain-specific vocabulary\n"
            "- Mix sentence lengths: 40% short (<12 words), 40% medium (12-20), "
            "20% long (20+ words)\n"
            "- Use transitional phrases like 'consequently', 'in particular', "
            "'what sets this apart'\n"
            "- NEVER write only simple subject-verb-object sentences\n\n"
            f"Previous output (DO NOT repeat):\n{prev_text[:1500]}\n"
        )

    async def _wm_call(self, prompt: str, model_override: str | None) -> tuple[str, dict[str, int]]:
        """Send a single narration prompt to WM and return (text, usage)."""
        wm_response = await asyncio.wait_for(
            self._wm.message(
                worker_name=_NARRATION_WORKER_NAME,
                message=prompt,
                system_prompt=_NARRATION_SYSTEM_PROMPT,
                model_override=model_override,
            ),
            timeout=self.settings.narrative_llm_timeout_seconds,
        )
        content = wm_response.output
        if isinstance(content, dict):
            # WM never sets output_schema for narration; defensive stringify.
            import json

            content = json.dumps(content)
        text = str(content) if content is not None else ""
        return text, _extract_usage_from_metrics(wm_response.usage_metrics)

    async def _chapter_llm_call(
        self,
        prompt: str,
        ch_num: int,
        attempt: int,
        max_attempts: int,
        model_override: str | None,
    ) -> str:
        llm_t0 = time.monotonic()
        text, usage = await self._wm_call(prompt, model_override)
        _accumulate_usage(usage)
        logger.debug(
            "LLM chapter %d narration call completed in %.2fs (attempt %d/%d)",
            ch_num,
            time.monotonic() - llm_t0,
            attempt,
            max_attempts,
        )
        return clean_narration_text(text) if text else ""

    async def _generate_chapter_narration(
        self,
        chapter: dict,
        tone: str,
        message: str = "",
        mode: str = "webapp_commercial",
        code_quality_facts: CodeQualityFacts | None = None,
        model_override: str | None = None,
    ) -> dict:
        """Generate narration for a single chapter via WM.

        Validates Flesch-Kincaid grade level and retries with a stronger
        prompt if the generated text is too simple (grade < 7).
        """
        ch_num = chapter.get("number", 0)
        est_dur = int(chapter.get("estimated_duration_seconds", 60))
        word_target = int(
            est_dur * self.words_per_minute / 60 * self.settings.narration.duration_safety_margin
        )

        base_prompt = self._build_prompt(
            chapter=chapter,
            tone=tone,
            est_dur=est_dur,
            word_target=word_target,
            mode=mode,
            code_quality_facts=code_quality_facts,
        )
        if message:
            base_prompt += f"\n## User Direction\n{message}\n"

        max_attempts = self.settings.narration.max_retry_attempts + 1  # 1 initial + retries

        text = ""
        # Fail-open visibility: any path that substitutes deterministic
        # boilerplate for LLM narration must say so. The dominant failure is a
        # WM timeout, and an unmarked placeholder is indistinguishable from
        # real narration to every downstream consumer (and to the operator).
        degraded = False
        for attempt in range(1, max_attempts + 1):
            prompt = self._chapter_prompt_with_retry(base_prompt, attempt, text)
            try:
                text = await self._chapter_llm_call(
                    prompt, ch_num, attempt, max_attempts, model_override
                )
                if not text.strip():
                    text = (
                        f"{chapter.get('title', 'This section')} demonstrates the key capabilities."
                    )
                    degraded = True
                    break
                grade = _compute_fk_grade(text)
                if grade >= _FK_GRADE_MIN:
                    logger.debug(
                        "Chapter %d narration FK grade %.1f >= %.1f — accepted (attempt %d)",
                        ch_num,
                        grade,
                        _FK_GRADE_MIN,
                        attempt,
                    )
                    break
                logger.info(
                    "Chapter %d narration FK grade %.1f < %.1f — retrying (%d/%d)",
                    ch_num,
                    grade,
                    _FK_GRADE_MIN,
                    attempt,
                    max_attempts,
                )
            except Exception as exc:  # noqa: BLE001 — per-chapter absorb; widened so a
                # WM httpx.HTTPError (the dominant failure) produces the deterministic
                # fallback instead of escaping and wedging the job in ``processing``.
                logger.warning("LLM narration failed for chapter %d: %s", ch_num, exc)
                text = _build_chapter_fallback_text(chapter)
                degraded = True
                break

        word_count = len(text.split())
        duration = word_count / self.words_per_minute * 60

        return {
            "part_id": f"ch{ch_num:02d}-narration",
            "text": text,
            "estimated_duration_seconds": duration,
            "word_count": word_count,
            "degraded": degraded,
        }

    def _build_prompt(
        self,
        chapter: dict,
        tone: str,
        est_dur: int,
        word_target: int,
        mode: str = "webapp_commercial",
        code_quality_facts: CodeQualityFacts | None = None,
    ) -> str:
        """Build the LLM prompt from a mode-specific template or inline default."""
        # Critique/review modes have part-level templates; for chapter narration,
        # fall back to the generic chapter_narration.md template.
        if mode in _PART_ONLY_TEMPLATE_MODES:
            template_path = _TEMPLATES_DIR / "chapter_narration.md"
        else:
            template_path = _TEMPLATES_DIR / f"{mode}.md"
        if template_path.exists():
            template_source = template_path.read_text()
        else:
            logger.debug("No template for mode '%s', using inline default", mode)
            template_source = self._default_template()

        template_vars = {
            "chapter_title": chapter.get("title", ""),
            "chapter_subtitle": chapter.get("subtitle", ""),
            "chapter_pages": ", ".join(str(p) for p in chapter.get("pages", [])),
            "narrative_arc": chapter.get("narrative_arc", ""),
            "key_moments": ", ".join(str(m) for m in chapter.get("key_moments", [])),
            "tone": tone,
            "hooks": "",
            "feature_highlights": "",
            "estimated_duration_seconds": str(est_dur),
            "word_target": str(word_target),
        }

        # Substitute code quality vars
        self._substitute_code_quality_vars(template_vars, code_quality_facts)

        try:
            undefined_cls = (
                jinja2.StrictUndefined if not self.settings.is_production() else jinja2.Undefined
            )
            jinja_tpl = jinja2.Template(template_source, undefined=undefined_cls)
            prompt = jinja_tpl.render(**template_vars)
        except jinja2.TemplateError as exc:
            logger.warning("Template rendering failed for mode '%s': %s", mode, exc)
            prompt = self._default_template()
            jinja_tpl = jinja2.Template(prompt, undefined=jinja2.Undefined)
            prompt = jinja_tpl.render(**template_vars)

        self._validate_template_vars(prompt, mode)
        return prompt

    @staticmethod
    def _substitute_code_quality_vars(
        template_vars: dict,
        code_quality_facts: CodeQualityFacts | None,
    ) -> None:
        """Add code quality variables to template_vars dict."""
        if code_quality_facts:
            template_vars["code_quality_summary"] = (
                f"Codebase: {code_quality_facts.loc} lines of "
                f"{code_quality_facts.primary_language or 'code'}, "
                f"maintainability score {code_quality_facts.maintainability_score}/100"
            )
            template_vars["primary_language"] = code_quality_facts.primary_language
            template_vars["loc"] = str(code_quality_facts.loc)
            template_vars["maintainability_score"] = str(code_quality_facts.maintainability_score)
            template_vars["top_issues_summary"] = (
                "\n".join(f"- {issue}" for issue in code_quality_facts.top_issues)
                if code_quality_facts.top_issues
                else "No significant issues identified"
            )
        else:
            template_vars["code_quality_summary"] = ""
            template_vars["primary_language"] = ""
            template_vars["loc"] = ""
            template_vars["maintainability_score"] = ""
            template_vars["top_issues_summary"] = ""

    @staticmethod
    def _validate_template_vars(rendered: str, mode: str) -> None:
        """Warn on unresolved template variables in the rendered output."""
        # Look for {{ var }} patterns that weren't substituted
        unresolved = re.findall(r"\{\{\s*(\w+)\s*\}\}", rendered)
        for var in unresolved:
            if var in _KNOWN_TEMPLATE_VARS:
                logger.warning("Unresolved known template var '{{ %s }}' in mode '%s'", var, mode)
            else:
                logger.warning("Unknown template var '{{ %s }}' in mode '%s'", var, mode)

    @staticmethod
    def _default_template() -> str:
        """Inline fallback template for unknown modes."""
        return (
            "You are writing voice-over narration for a demo video chapter.\n"
            "Write 2-3 conversational sentences introducing the chapter content.\n"
            "Be engaging and natural. Output ONLY the narration text.\n\n"
            "Chapter title: {{ chapter_title }}\n"
            "Chapter subtitle: {{ chapter_subtitle }}\n"
            "Pages: {{ chapter_pages }}\n"
            "Narrative arc: {{ narrative_arc }}\n"
            "Key moments: {{ key_moments }}\n"
            "Tone: {{ tone }}\n"
            "Target duration: ~{{ estimated_duration_seconds }}s "
            "(~{{ word_target }} words)\n"
        )

    async def _rewrite_bookend(
        self,
        stage_direction: str,
        section: str,
        video_title: str,
        tone: str,
        message: str = "",
        model_override: str | None = None,
    ) -> tuple[str, bool]:
        """Rewrite intro/outro stage direction into speakable voiceover text.

        Returns ``(text, degraded)``. ``degraded`` is True whenever the LLM
        rewrite did not produce usable text and the caller is handed back the
        raw *stage direction* — a director's instruction, not voiceover, which
        TTS will happily read out loud. Callers MUST propagate the flag onto the
        emitted part; see ``narration._has_degraded_parts``.
        """
        if section == "intro":
            section_guidance = (
                "For the opening: Hook the viewer in 1-2 sentences. "
                "State the core value proposition. Do NOT say welcome."
            )
        else:
            section_guidance = (
                "For the closing: Summarize the key takeaway in 1 sentence "
                "and give a clear call-to-action. Do NOT say thank you."
            )
        prompt = (
            f"Write a {section} voice-over for a professional demo video "
            f'about "{video_title}".\n'
            f"Tone: {tone}.\n\n"
            f"{section_guidance}\n\n"
            f"Context: {stage_direction}\n"
            f"Output ONLY the narration text."
        )
        if message:
            prompt += f"\n\nUser direction: {message}"

        try:
            text, usage = await self._wm_call(prompt, model_override)
            _accumulate_usage(usage)
            if text:
                cleaned = clean_narration_text(text)
                if cleaned:
                    return cleaned, False
            logger.warning(
                "WM returned no usable %s narration — falling back to the stage direction",
                section,
            )
            return stage_direction, True
        except Exception as exc:  # noqa: BLE001 — bookend absorb; widened so a WM
            # httpx.HTTPError falls back to the original stage direction instead of
            # escaping and wedging the narration job in ``processing``.
            logger.warning("Failed to rewrite %s narration: %s", section, exc)
        return stage_direction, True

    def _resolve_shotlist_tone_and_message(
        self, storyline: dict, user_context: UserContext | None, message: str
    ) -> tuple[str, str]:
        tone = storyline.get("tone", "professional")
        if user_context and user_context.narration_guidance.tone:
            tone = user_context.narration_guidance.tone
        if user_context:
            context_msg = self._build_narration_context(user_context)
            if context_msg:
                message = f"{context_msg}\n\n{message}" if message else context_msg
        return tone, message

    @staticmethod
    def _build_shotlist_lookups(
        shot_list: dict, storyline: dict
    ) -> tuple[dict[str, dict], dict[int, dict]]:
        # shot_list is free-form: SKIP a part that is not a dict or has no id, rather
        # than KeyError/TypeError the whole narration job on one malformed row.
        parts_by_id: dict[str, dict] = {
            p["id"]: p for p in shot_list.get("parts", []) if isinstance(p, dict) and p.get("id")
        }
        chapters_by_num: dict[int, dict] = {
            ch.get("number", 0): ch for ch in storyline.get("chapters", [])
        }
        return parts_by_id, chapters_by_num

    @staticmethod
    def _count_chapter_recordings(
        shot_list: dict, parts_by_id: dict[str, dict]
    ) -> tuple[dict[int, int], dict[int, int]]:
        counts: dict[int, int] = {}
        seen: dict[int, int] = {}
        for part_id in shot_list.get("recording_order", []):
            part = parts_by_id.get(part_id)
            if part and part.get("part_type") == "browser_recording":
                ch_num = part.get("chapter", 0)
                counts[ch_num] = counts.get(ch_num, 0) + 1
                seen[ch_num] = 0
        return counts, seen

    async def _handle_intro_outro_part(
        self,
        part: dict,
        part_id: str,
        storyline: dict,
        tone: str,
        message: str,
        model_override: str | None,
    ) -> dict | None:
        narrative_text = part.get("narrative_text", "") or ""
        if not narrative_text:
            section = "intro" if part_id == "intro" else "outro"
            narrative_text = storyline.get(section, {}).get("narrative_text", "")
        if not narrative_text.strip():
            return None
        rewritten, degraded = await self._rewrite_bookend(
            narrative_text,
            "intro" if part_id == "intro" else "outro",
            storyline.get("video_title", ""),
            tone,
            message,
            model_override=model_override,
        )
        word_count = len(rewritten.split())
        return {
            "part_id": part_id,
            "text": rewritten,
            "estimated_duration_seconds": word_count / self.words_per_minute * 60,
            "word_count": word_count,
            # Same contract as the browser-recording fallback below: a stage
            # direction served in place of voiceover is degraded output.
            "degraded": degraded,
        }

    def _handle_chapter_title_card(self, part: dict) -> dict | None:
        ch_num = part.get("chapter", 0)
        card_title = part.get("title", "")
        card_subtitle = part.get("subtitle", "")
        if card_subtitle:
            transition_text = f"Next, let's look at {card_title}. {card_subtitle}."
        elif card_title:
            transition_text = f"Moving on to {card_title}."
        else:
            return None
        word_count = len(transition_text.split())
        return {
            "part_id": part.get("id", f"ch{ch_num:02d}-title"),
            "text": transition_text,
            "estimated_duration_seconds": word_count / self.words_per_minute * 60,
            "word_count": word_count,
        }

    @staticmethod
    def _compute_chapter_position(
        ch_num: int,
        chapter_recording_counts: dict[int, int],
        chapter_recording_seen: dict[int, int],
    ) -> str:
        total_in_chapter = chapter_recording_counts.get(ch_num, 1)
        seen = chapter_recording_seen.get(ch_num, 0)
        chapter_recording_seen[ch_num] = seen + 1
        if total_in_chapter == 1:
            return "only"
        if seen == 0:
            return "first"
        if seen + 1 >= total_in_chapter:
            return "last"
        return "middle"

    async def _handle_browser_recording_part(
        self,
        part: dict,
        part_id: str,
        chapters_by_num: dict[int, dict],
        chapter_recording_counts: dict[int, int],
        chapter_recording_seen: dict[int, int],
        tone: str,
        mode: str,
        previous_narration: str,
        message: str,
        code_quality_facts: CodeQualityFacts | None,
        model_override: str | None,
    ) -> dict:
        ch_num = part.get("chapter", 0)
        chapter = chapters_by_num.get(ch_num, {})
        position = self._compute_chapter_position(
            ch_num, chapter_recording_counts, chapter_recording_seen
        )
        try:
            return await self._generate_part_narration(
                part=part,
                chapter=chapter,
                tone=tone,
                mode=mode,
                position_in_chapter=position,
                previous_narration=previous_narration,
                message=message,
                code_quality_facts=code_quality_facts,
                model_override=model_override,
            )
        except Exception:  # noqa: BLE001 — per-part absorb; widened so a WM
            # httpx.HTTPError produces the deterministic fallback instead of
            # escaping into the recording loop and wedging the job.
            logger.warning("Part narration failed for %s, using fallback", part_id)
            fallback_text = f"Let's take a look at {part.get('title', 'this section')}."
            word_count = len(fallback_text.split())
            return {
                "part_id": part_id,
                "text": fallback_text,
                "estimated_duration_seconds": word_count / self.words_per_minute * 60,
                "word_count": word_count,
                "degraded": True,
            }

    async def _narration_for_shotlist_part(
        self,
        part: dict,
        part_id: str,
        storyline: dict,
        chapters_by_num: dict[int, dict],
        chapter_recording_counts: dict[int, int],
        chapter_recording_seen: dict[int, int],
        tone: str,
        mode: str,
        message: str,
        previous_narration: str,
        code_quality_facts: CodeQualityFacts | None,
        model_override: str | None,
    ) -> dict | None:
        part_type = part.get("part_type", "")
        if part_type == "title_card" and part_id in ("intro", "outro"):
            return await self._handle_intro_outro_part(
                part, part_id, storyline, tone, message, model_override
            )
        if part_type == "title_card":
            return self._handle_chapter_title_card(part)
        if part_type == "browser_recording":
            return await self._handle_browser_recording_part(
                part,
                part_id,
                chapters_by_num,
                chapter_recording_counts,
                chapter_recording_seen,
                tone,
                mode,
                previous_narration,
                message,
                code_quality_facts,
                model_override,
            )
        return None

    async def generate_for_shot_list(
        self,
        shot_list: dict,
        storyline: dict,
        user_context: UserContext | None = None,
        message: str = "",
        synthesize_audio: bool = False,
        mode: str = "webapp_commercial",
        code_quality_facts: CodeQualityFacts | None = None,
        llm_config: LLMConfigOverride | None = None,
        license_token: str | None = None,
    ) -> dict:
        """Generate narration aligned to individual shot list parts.

        Returns a dict matching NarrationScript structure:
        {parts, total_words, estimated_total_duration_seconds, tone}
        """
        local_tokens: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        ctx_token = _token_accumulator.set(local_tokens)
        try:
            return await self._generate_for_shot_list_inner(
                shot_list,
                storyline,
                user_context,
                message,
                synthesize_audio,
                mode,
                code_quality_facts,
                llm_config,
                license_token,
                local_tokens,
            )
        finally:
            _token_accumulator.reset(ctx_token)

    async def _generate_for_shot_list_inner(
        self,
        shot_list: dict,
        storyline: dict,
        user_context: UserContext | None,
        message: str,
        synthesize_audio: bool,
        mode: str,
        code_quality_facts: CodeQualityFacts | None,
        llm_config: LLMConfigOverride | None,
        license_token: str | None,
        local_tokens: dict[str, int],
    ) -> dict:
        t0 = time.monotonic()
        recording_order = shot_list.get("recording_order", [])
        logger.info(
            "NarrationBuilder.generate_for_shot_list: mode=%s, parts=%d, tts=%s",
            mode,
            len(recording_order),
            synthesize_audio,
        )
        tone, message = self._resolve_shotlist_tone_and_message(storyline, user_context, message)

        model_override = self._resolve_model_override(llm_config)

        parts_by_id, chapters_by_num = self._build_shotlist_lookups(shot_list, storyline)
        chapter_recording_counts, chapter_recording_seen = self._count_chapter_recordings(
            shot_list, parts_by_id
        )

        narration_parts: list[dict] = []
        previous_narration = ""

        for part_id in shot_list.get("recording_order", []):
            part = parts_by_id.get(part_id)
            if not part:
                continue

            narration_part = await self._narration_for_shotlist_part(
                part=part,
                part_id=part_id,
                storyline=storyline,
                chapters_by_num=chapters_by_num,
                chapter_recording_counts=chapter_recording_counts,
                chapter_recording_seen=chapter_recording_seen,
                tone=tone,
                mode=mode,
                message=message,
                previous_narration=previous_narration,
                code_quality_facts=code_quality_facts,
                model_override=model_override,
            )
            if narration_part is None:
                continue
            narration_parts.append(narration_part)
            previous_narration = str(narration_part.get("text", ""))

        # TTS audio synthesis (fail-open per part)
        if synthesize_audio:
            await self._synthesize_parts(narration_parts, license_token=license_token)

        total_words = sum(p["word_count"] for p in narration_parts)
        elapsed = time.monotonic() - t0
        logger.info(
            "NarrationBuilder.generate_for_shot_list: completed in %.2fs, %d parts, %d words",
            elapsed,
            len(narration_parts),
            total_words,
        )
        result: dict = {
            "parts": narration_parts,
            "total_words": total_words,
            "estimated_total_duration_seconds": total_words / self.words_per_minute * 60,
            "tone": tone,
        }
        if local_tokens["total_tokens"] > 0:
            result["token_usage"] = dict(local_tokens)
        return result

    @staticmethod
    def _part_prompt_with_retry(base_prompt: str, attempt: int, prev_text: str) -> str:
        if attempt <= 1:
            return base_prompt
        return base_prompt + (
            "\n## CRITICAL: Reading Level Requirement\n\n"
            f"Your previous narration scored FK grade {_compute_fk_grade(prev_text):.1f} "
            f"(target: 8-12). It is TOO SIMPLE.\n"
            "You MUST use compound sentences, domain vocabulary, and varied "
            "sentence lengths. Never write only simple subject-verb-object "
            "sentences.\n\n"
            f"Previous output (DO NOT repeat):\n{prev_text[:1500]}\n"
        )

    async def _part_llm_call(self, prompt: str, model_override: str | None) -> str:
        text, usage = await self._wm_call(prompt, model_override)
        _accumulate_usage(usage)
        return clean_narration_text(text) if text else ""

    async def _generate_part_narration(
        self,
        part: dict,
        chapter: dict,
        tone: str,
        mode: str = "webapp_commercial",
        position_in_chapter: str = "only",
        previous_narration: str = "",
        message: str = "",
        code_quality_facts: CodeQualityFacts | None = None,
        model_override: str | None = None,
    ) -> dict:
        """Generate narration for a single shot list part via LLM."""
        part_id = part.get("id", "unknown")
        est_dur = int(part.get("estimated_duration_seconds", 30))
        word_target = int(
            est_dur * self.words_per_minute / 60 * self.settings.narration.duration_safety_margin
        )

        # Use mode-specific part template if available, else generic part_narration.md
        mode_template_path = _TEMPLATES_DIR / f"{mode}.md"
        generic_template_path = _TEMPLATES_DIR / "part_narration.md"
        if mode in _PART_ONLY_TEMPLATE_MODES and mode_template_path.exists():
            template_path = mode_template_path
        elif generic_template_path.exists():
            template_path = generic_template_path
        else:
            template_path = None

        if template_path is not None:
            template_source = template_path.read_text()
        else:
            template_source = (
                "Write voice-over narration for a video segment.\n"
                "Part: {{ part_title }}\nActions: {{ part_actions }}\n"
                "Tone: {{ tone }}\nTarget: ~{{ word_target }} words\n"
                "Output ONLY the narration text."
            )

        template_vars = {
            "part_title": part.get("title", ""),
            "part_actions": ", ".join(str(a) for a in part.get("actions", [])),
            "part_pages": ", ".join(str(p) for p in part.get("pages", [])),
            "chapter_title": chapter.get("title", ""),
            "narrative_arc": chapter.get("narrative_arc", ""),
            "tone": tone,
            "mode": mode,
            "estimated_duration_seconds": str(est_dur),
            "word_target": str(word_target),
            "position_in_chapter": position_in_chapter,
            "previous_narration": previous_narration[-300:] if previous_narration else "None",
        }

        self._substitute_code_quality_vars(template_vars, code_quality_facts)

        try:
            undefined_cls = (
                jinja2.StrictUndefined if not self.settings.is_production() else jinja2.Undefined
            )
            tpl = jinja2.Template(template_source, undefined=undefined_cls)
            base_prompt = tpl.render(**template_vars)
        except jinja2.TemplateError as exc:
            logger.warning("Part narration template rendering failed: %s", exc)
            base_prompt = (
                f"Write narration for: {part.get('title', 'this section')}. "
                f"Tone: {tone}. Target: ~{word_target} words. Output ONLY narration text."
            )

        if message:
            base_prompt += f"\n## User Direction\n{message}\n"

        max_attempts = self.settings.narration.max_retry_attempts + 1

        text = ""
        # Fail-open visibility, same contract as the chapter path: every branch
        # that substitutes deterministic boilerplate must say so HERE, at the
        # point of substitution. The caller's ``except`` in
        # ``_handle_browser_recording_part`` cannot do it — the absorb below
        # swallows the dominant failure (WM timeout / httpx.HTTPError) before it
        # can ever reach the caller, so an unmarked placeholder would be
        # indistinguishable from real narration to every downstream consumer.
        degraded = False
        for attempt in range(1, max_attempts + 1):
            prompt = self._part_prompt_with_retry(base_prompt, attempt, text)
            try:
                text = await self._part_llm_call(prompt, model_override)
                if not text.strip():
                    text = f"{part.get('title', 'This section')} demonstrates the key capabilities."
                    degraded = True
                    break
                grade = _compute_fk_grade(text)
                if grade >= _FK_GRADE_MIN:
                    logger.debug(
                        "Part %s narration FK grade %.1f — accepted (attempt %d)",
                        part_id,
                        grade,
                        attempt,
                    )
                    break
                logger.info(
                    "Part %s narration FK grade %.1f < %.1f — retrying (%d/%d)",
                    part_id,
                    grade,
                    _FK_GRADE_MIN,
                    attempt,
                    max_attempts,
                )
            except Exception as exc:  # noqa: BLE001 — per-part absorb; widened so a WM
                # httpx.HTTPError produces the deterministic fallback instead of
                # escaping and wedging the narration job in ``processing``.
                logger.warning("LLM part narration failed for %s: %s", part_id, exc)
                text = f"{part.get('title', 'This section')} showcases important functionality."
                degraded = True
                break

        word_count = len(text.split())
        duration = word_count / self.words_per_minute * 60

        return {
            "part_id": part_id,
            "text": text,
            "estimated_duration_seconds": duration,
            "word_count": word_count,
            "degraded": degraded,
        }

    @staticmethod
    def _build_narration_context(user_context: UserContext) -> str:
        """Build a narration context block from UserContext."""
        parts: list[str] = []
        if user_context.goals:
            parts.append("**Goals:** " + "; ".join(user_context.goals))
        if user_context.target_audience:
            parts.append(f"**Target Audience:** {user_context.target_audience}")
        if user_context.narration_guidance.key_messages:
            bullet_list = "\n".join(
                f"  - {msg}" for msg in user_context.narration_guidance.key_messages
            )
            parts.append(f"**Key Messages:**\n{bullet_list}")
        if user_context.narration_guidance.avoid_topics:
            exclusion_list = "\n".join(
                f"  - {topic}" for topic in user_context.narration_guidance.avoid_topics
            )
            parts.append(f"**Topics to Avoid:**\n{exclusion_list}")
        if not parts:
            return ""
        return "## User Direction\n" + "\n".join(parts)
