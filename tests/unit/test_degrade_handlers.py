"""Regression tests for degrade handlers that could never fire.

WHY THIS FILE EXISTS
--------------------
``redis.exceptions.RedisError`` derives from ``Exception``, **not** from
``OSError``. Its subclasses ``redis.exceptions.ConnectionError`` and
``redis.exceptions.TimeoutError`` merely *shadow the builtin names* — they are
different classes that happen to be spelled the same. So every handler written
as::

    except (OSError, ConnectionError, TimeoutError):

was dead code against a redis client: none of those three classes can ever
match what redis-py raises. Each site carried a docstring promising graceful
degradation ("returns a cache miss", "degrades to a no-op", "fail-open") and
delivered a hard failure instead:

* ``src/infra/redis_client.py`` — a Redis blip became a 500, not a cache miss.
* ``src/infra/cancel_flags.py`` — ``is_cancelled`` raised, the exception
  propagated through ``raise_if_cancelled`` into the running job and 500'd
  ``DELETE /jobs/{job_id}``: a cross-pod cancel became an **unkillable job**.
* ``src/cache/job_lock.py`` — the duplicate-processing guard killed the job it
  was supposed to wave through.

The fix is ``except (OSError, RedisError)``. Both halves are load-bearing:
``RedisError`` for anything the client raises, ``OSError`` for socket-level
failures raised below it. These tests pin the behaviour *and* the subclass fact
(:meth:`TestRedisExceptionHierarchy.test_rediserror_is_not_an_oserror`) so
nobody "simplifies" the tuples back to builtins.

Every fault injected below uses ``redis.exceptions.ConnectionError`` — the
class production actually raises. Injecting the *builtin* ``ConnectionError``
is what made the original tests pass against broken handlers.

A second defect class is covered here: unbounded ``asyncio.gather`` over
request-derived collections in ``src/services/narrative/narration_builder.py``.
One request could open one LLM call per chapter against a single small vLLM
device; the fan-out is now gated by an ``asyncio.Semaphore``.

Every test is hermetic: no real Redis, no real LLM, no network.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
import redis.exceptions

from src.cache.job_lock import acquire_job_lock
from src.infra.cancel_flags import CancelFlags, JobCancelledError
from src.infra.redis_client import RedisClient
from src.services.narrative import narration_builder as nb
from src.services.narrative.narration_builder import NarrationBuilder


def _redis_down() -> redis.exceptions.ConnectionError:
    """Return the failure redis-py actually raises when the server is gone.

    Returns:
        A fresh ``redis.exceptions.ConnectionError`` — NOT the builtin of the
        same name. The distinction is the entire point of this module.
    """
    return redis.exceptions.ConnectionError("Error 111 connecting to redis:6379.")


# ---------------------------------------------------------------------------
# The documentation assertion that makes the rest of this file necessary
# ---------------------------------------------------------------------------


class TestRedisExceptionHierarchy:
    """Pin the class relationships that made the original handlers unreachable."""

    def test_rediserror_is_not_an_oserror(self) -> None:
        """``except (OSError, ...)`` cannot catch anything redis-py raises.

        Kept deliberately. ``except (OSError, ConnectionError, TimeoutError)``
        reads as if it covers redis failures; it covers none of them. If this
        assertion ever fails, the degrade tuples in ``redis_client``,
        ``cancel_flags`` and ``job_lock`` can be revisited — until then,
        removing ``RedisError`` from any of them silently restores the bug.
        """
        assert not issubclass(redis.exceptions.RedisError, OSError)

    def test_redis_error_names_shadow_the_builtins(self) -> None:
        """The two name-collision traps, spelled out."""
        assert redis.exceptions.ConnectionError is not ConnectionError
        assert redis.exceptions.TimeoutError is not TimeoutError
        assert not issubclass(redis.exceptions.ConnectionError, ConnectionError)
        assert not issubclass(redis.exceptions.TimeoutError, TimeoutError)

    def test_rediserror_is_the_sufficient_base(self) -> None:
        """Naming ``RedisError`` alone covers the concrete failure classes."""
        assert issubclass(redis.exceptions.ConnectionError, redis.exceptions.RedisError)
        assert issubclass(redis.exceptions.TimeoutError, redis.exceptions.RedisError)


# ---------------------------------------------------------------------------
# src/infra/redis_client.py — every cache op degrades on RedisError
# ---------------------------------------------------------------------------


class _BrokenRedis:
    """Stand-in for ``redis.asyncio.Redis`` where every command fails."""

    async def get(self, key: str) -> str | None:
        raise _redis_down()

    async def set(self, key: str, value: str) -> bool:
        raise _redis_down()

    async def setex(self, key: str, ttl: int, value: str) -> bool:
        raise _redis_down()

    async def delete(self, key: str) -> int:
        raise _redis_down()

    async def exists(self, key: str) -> int:
        raise _redis_down()

    async def ping(self) -> bool:
        raise _redis_down()


class _HealthyRedis:
    """In-memory stand-in for ``redis.asyncio.Redis`` that answers commands."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str) -> bool:
        self.store[key] = value
        return True

    async def setex(self, key: str, ttl: int, value: str) -> bool:
        self.store[key] = value
        return True

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0

    async def exists(self, key: str) -> int:
        return 1 if key in self.store else 0

    async def ping(self) -> bool:
        return True


def _client_with(monkeypatch: pytest.MonkeyPatch, backend: object) -> RedisClient:
    """Build a ``RedisClient`` whose connected client is *backend*.

    ``_client`` is the object ``_ensure_connected()`` hands to every cache op,
    so this is the narrowest seam that runs the real handler bodies without a
    live server.

    Args:
        monkeypatch: pytest monkeypatch fixture (restores the attribute).
        backend: Object to install as the connected redis client.

    Returns:
        A ``RedisClient`` wired to *backend*.
    """
    client = RedisClient(redis_url="redis://redis.test.invalid:6379/0")
    monkeypatch.setattr(client, "_client", backend)
    return client


class TestRedisClientDegrades:
    """Every cache op returns its miss/no-op value on a real RedisError."""

    async def test_get_degrades_to_miss(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``get`` returns None (a cache miss) instead of raising."""
        client = _client_with(monkeypatch, _BrokenRedis())
        assert await client.get("k") is None

    async def test_set_degrades_to_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``set`` returns False on both the TTL and non-TTL code paths."""
        client = _client_with(monkeypatch, _BrokenRedis())
        assert await client.set("k", {"a": 1}) is False
        assert await client.set("k", {"a": 1}, ttl=60) is False

    async def test_delete_degrades_to_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``delete`` returns False instead of raising."""
        client = _client_with(monkeypatch, _BrokenRedis())
        assert await client.delete("k") is False

    async def test_exists_degrades_to_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``exists`` returns False instead of raising."""
        client = _client_with(monkeypatch, _BrokenRedis())
        assert await client.exists("k") is False

    async def test_ping_degrades_to_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``ping`` reports unreachable rather than raising into a health check."""
        client = _client_with(monkeypatch, _BrokenRedis())
        assert await client.ping() is False

    async def test_get_degrades_on_plain_oserror(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The ``OSError`` half of the tuple is load-bearing too.

        Socket-level failures can surface from below the client without being
        wrapped in a ``RedisError``; dropping ``OSError`` re-breaks those.
        """

        class _SocketBroken:
            async def get(self, key: str) -> str | None:
                raise OSError("EPIPE")

        client = _client_with(monkeypatch, _SocketBroken())
        assert await client.get("k") is None

    async def test_happy_path_still_returns_real_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Positive control: the miss/no-op values above are not hardcoded stubs.

        Without this, every assertion in this class would also pass against a
        method whose entire body was ``return None`` / ``return False``.
        """
        client = _client_with(monkeypatch, _HealthyRedis())

        assert await client.set("k", {"a": 1}) is True
        assert await client.get("k") == {"a": 1}
        assert await client.exists("k") is True
        assert await client.ping() is True
        assert await client.delete("k") is True
        # ...and the key really is gone, so these track state rather than
        # returning a constant.
        assert await client.exists("k") is False
        assert await client.get("k") is None


# ---------------------------------------------------------------------------
# src/infra/cancel_flags.py — genuinely a no-op when Redis is down
# ---------------------------------------------------------------------------


class _BrokenFlagStore:
    """``_RedisLike`` whose every call raises what redis-py actually raises."""

    async def set(self, key: str, value: object, ttl: int | None = None) -> bool:
        raise _redis_down()

    async def exists(self, key: str) -> bool:
        raise _redis_down()

    async def delete(self, key: str) -> bool:
        raise _redis_down()


class _HealthyFlagStore:
    """In-memory ``_RedisLike`` implementing the three commands used."""

    def __init__(self) -> None:
        self.keys: set[str] = set()

    async def set(self, key: str, value: object, ttl: int | None = None) -> bool:
        self.keys.add(key)
        return True

    async def exists(self, key: str) -> bool:
        return key in self.keys

    async def delete(self, key: str) -> bool:
        self.keys.discard(key)
        return True


class TestCancelFlagsDegrade:
    """A Redis outage must not turn a cancel checkpoint into a job killer."""

    async def test_is_cancelled_is_a_no_op_when_redis_is_down(self) -> None:
        """``is_cancelled`` returns False rather than raising.

        The module docstring promises "if Redis is unavailable the helper
        degrades to a no-op". It did not: the ``RedisError`` bypassed the
        handler entirely.
        """
        flags = CancelFlags(_BrokenFlagStore())

        assert await flags.is_cancelled("job-1", tenant_id="t1") is False

    async def test_raise_if_cancelled_does_not_raise_when_redis_is_down(self) -> None:
        """This is the site that produced an **unkillable job**.

        ``is_cancelled`` raised, ``raise_if_cancelled`` propagated the
        ``RedisError`` into the running generation task, and ``DELETE
        /api/scripts/jobs/{job_id}`` 500'd — so a cross-pod cancel could
        neither stop the job nor return successfully. No ``pytest.raises``
        here: the whole contract is that nothing escapes.
        """
        flags = CancelFlags(_BrokenFlagStore())

        await flags.raise_if_cancelled("job-1", tenant_id="t1")

    async def test_write_side_helpers_swallow_redis_failures(self) -> None:
        """``request_cancel``/``clear`` never fail the caller's HTTP request."""
        flags = CancelFlags(_BrokenFlagStore())

        await flags.request_cancel("job-1", tenant_id="t1")
        await flags.clear("job-1", tenant_id="t1")

    async def test_no_client_configured_is_the_same_no_op(self) -> None:
        """No Redis configured at all is the same documented no-op."""
        flags = CancelFlags(None)

        assert await flags.is_cancelled("job-1", tenant_id="t1") is False
        await flags.raise_if_cancelled("job-1", tenant_id="t1")

    async def test_raise_if_cancelled_still_raises_on_a_healthy_client(self) -> None:
        """Positive control: degradation, not a permanently dead checkpoint.

        With a working store and the flag set, the cooperative cancel must
        still fire — otherwise the tests above would pass against a
        ``raise_if_cancelled`` that never raised at all.
        """
        flags = CancelFlags(_HealthyFlagStore())

        await flags.request_cancel("job-1", tenant_id="t1")
        assert await flags.is_cancelled("job-1", tenant_id="t1") is True
        with pytest.raises(JobCancelledError):
            await flags.raise_if_cancelled("job-1", tenant_id="t1")

        # ...and clearing it un-arms the checkpoint again.
        await flags.clear("job-1", tenant_id="t1")
        assert await flags.is_cancelled("job-1", tenant_id="t1") is False
        await flags.raise_if_cancelled("job-1", tenant_id="t1")


# ---------------------------------------------------------------------------
# src/cache/job_lock.py — fail-open
# ---------------------------------------------------------------------------


def _mock_redis_client(inner: object) -> MagicMock:
    """Wrap *inner* so ``redis_client._ensure_connected()`` returns it.

    ``acquire_job_lock`` reaches the raw command object through
    ``_ensure_connected()``; that call is the seam.

    Args:
        inner: The object standing in for ``redis.asyncio.Redis``.

    Returns:
        A ``MagicMock`` shaped like ``RedisClient``.
    """
    client = MagicMock()
    client._ensure_connected.return_value = inner
    return client


class TestJobLockFailsOpen:
    """The duplicate-processing guard must never become the thing that fails a job."""

    async def test_returns_true_when_set_raises_rediserror(self) -> None:
        """A Redis blip lets the job through instead of killing it.

        ``acquire_job_lock`` is documented "Fail-open: returns True if Redis is
        unavailable". With the old all-builtins tuple a ``RedisError`` from the
        ``SET NX`` escaped and the guard failed the job. The injected failure
        is the redis-py class, not the builtin.
        """
        inner = AsyncMock()
        inner.set = AsyncMock(side_effect=_redis_down())
        client = _mock_redis_client(inner)

        assert await acquire_job_lock(client, "job-1") is True

    async def test_returns_true_when_set_raises_redis_timeout(self) -> None:
        """``redis.exceptions.TimeoutError`` is covered by the same base class."""
        inner = AsyncMock()
        inner.set = AsyncMock(side_effect=redis.exceptions.TimeoutError("timed out"))
        client = _mock_redis_client(inner)

        assert await acquire_job_lock(client, "job-1") is True

    async def test_returns_true_without_a_client(self) -> None:
        """No Redis configured means processing proceeds unguarded, by design."""
        assert await acquire_job_lock(None, "job-1") is True

    async def test_still_rejects_a_held_lock(self) -> None:
        """Positive control: fail-open is not "always True".

        Against a backend with real ``SET NX`` semantics the second
        acquisition of the same job must lose and return False — that is the
        duplicate-processing guard actually working.
        """
        store: dict[str, str] = {}

        async def _set_nx(
            key: str, value: str, nx: bool = False, ex: int | None = None
        ) -> bool | None:
            if nx and key in store:
                return None
            store[key] = value
            return True

        inner = AsyncMock()
        inner.set = _set_nx
        client = _mock_redis_client(inner)

        assert await acquire_job_lock(client, "job-lock-control") is True
        assert await acquire_job_lock(client, "job-lock-control") is False


# ---------------------------------------------------------------------------
# narration_builder — the per-request LLM fan-out is bounded
# ---------------------------------------------------------------------------


class _ConcurrencyProbe:
    """Fake chapter-narration call recording peak simultaneous in-flight calls.

    Instrumentation, not timing: increment a counter on entry, record a
    high-water mark, yield to the event loop several times so sibling tasks
    genuinely interleave, decrement on exit. No wall-clock assertions anywhere
    — those are flaky on a loaded CI runner.
    """

    def __init__(self, *, yields: int = 5, fail_numbers: frozenset[int] | None = None) -> None:
        """Initialise the probe.

        Args:
            yields: How many times each call yields to the loop before
                returning. Greater than 1 so the scheduler really interleaves.
            fail_numbers: Chapter numbers for which the call raises, used to
                check per-item error handling is unchanged.
        """
        self.in_flight = 0
        self.peak = 0
        self.calls = 0
        self._yields = yields
        self._fail_numbers = fail_numbers if fail_numbers is not None else frozenset()

    async def __call__(self, chapter: dict, *args: object, **kwargs: object) -> dict:
        """Stand in for ``NarrationBuilder._generate_chapter_narration``."""
        self.calls += 1
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            for _ in range(self._yields):
                await asyncio.sleep(0)
            number = int(chapter.get("number", 0))
            if number in self._fail_numbers:
                # A KeyError specifically: the previous narrow per-chapter
                # tuple (OSError, TimeoutError, ValueError, RuntimeError) did
                # not list it, so it escaped into gather() and took out every
                # sibling chapter via task cancellation.
                raise KeyError(f"probe failure for chapter {number}")
            return {
                "part_id": f"ch{number:02d}-narration",
                "text": f"Narration for chapter {number}.",
                "estimated_duration_seconds": 1.0,
                "word_count": 4,
            }
        finally:
            self.in_flight -= 1


def _chapters(count: int) -> list[dict]:
    """Build *count* minimal chapter dicts as a request would supply them."""
    return [
        {
            "number": n,
            "title": f"Chapter {n}",
            "subtitle": "",
            "narrative_arc": "",
            "key_moments": [],
            "pages": [],
            "estimated_duration_seconds": 30.0,
        }
        for n in range(1, count + 1)
    ]


async def _run_fanout(
    builder: NarrationBuilder,
    monkeypatch: pytest.MonkeyPatch,
    *,
    bound: int,
    chapters: list[dict],
    fail_numbers: frozenset[int] | None = None,
) -> tuple[list[dict], _ConcurrencyProbe]:
    """Drive ``_build_chapter_parts`` with the semaphore bound patched to *bound*.

    ``_build_chapter_parts`` is the reachable seam: it owns the
    ``asyncio.Semaphore`` and the ``gather``, and it reads the module constant
    at call time, so monkeypatching the constant is faithful to production.
    Going through the public ``generate()`` would add bookend LLM calls and a
    full ``AnalysisInput`` without adding any coverage of the bound.

    Args:
        builder: The NarrationBuilder under test.
        monkeypatch: pytest monkeypatch fixture.
        bound: Value patched over ``_MAX_CONCURRENT_CHAPTER_LLM_CALLS``.
        chapters: Chapter dicts to fan out over.
        fail_numbers: Chapter numbers whose LLM call should raise.

    Returns:
        The resulting narration parts and the probe that observed the fan-out.
    """
    probe = _ConcurrencyProbe(fail_numbers=fail_numbers)
    monkeypatch.setattr(nb, "_MAX_CONCURRENT_CHAPTER_LLM_CALLS", bound)
    monkeypatch.setattr(builder, "_generate_chapter_narration", probe)
    parts = await builder._build_chapter_parts(
        chapters, "professional", "", "webapp_commercial", None, None
    )
    return parts, probe


@pytest.fixture
def builder(mock_settings) -> NarrationBuilder:
    """A NarrationBuilder whose WM client is never reached (calls are patched)."""
    return NarrationBuilder(settings=mock_settings)


class TestChapterFanOutIsBounded:
    """One request must not open one LLM call per chapter."""

    async def test_never_exceeds_the_bound(
        self, builder: NarrationBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """12 chapters, bound 2: at most 2 LLM calls in flight at any instant.

        Before the fix this was an unbounded ``asyncio.gather`` over a
        request-derived list, against a single small vLLM device fronted by
        worker_management.
        """
        parts, probe = await _run_fanout(builder, monkeypatch, bound=2, chapters=_chapters(12))

        assert probe.peak <= 2
        # A peak of 1 satisfies any bound trivially and would prove nothing, so
        # require that the probe actually observed concurrency.
        assert probe.peak > 1
        assert probe.calls == 12
        assert len(parts) == 12

    async def test_probe_reports_a_higher_peak_at_a_higher_bound(
        self, builder: NarrationBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Control for the probe itself: raise the bound and the peak follows.

        Without this, ``peak <= 2`` above could pass simply because the probe
        is blind — never observing more than one in-flight call regardless of
        the gate — which would make the bound assertion meaningless.
        """
        _, probe = await _run_fanout(builder, monkeypatch, bound=8, chapters=_chapters(12))

        assert probe.peak > 2
        assert probe.peak <= 8

    async def test_every_chapter_still_completes(
        self, builder: NarrationBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Gating must not drop work: each chapter yields exactly one part."""
        parts, probe = await _run_fanout(builder, monkeypatch, bound=4, chapters=_chapters(9))

        assert probe.calls == 9
        assert [p["part_id"] for p in parts] == [f"ch{n:02d}-narration" for n in range(1, 10)]
        assert all(not p.get("degraded") for p in parts)

    async def test_error_handling_is_still_per_chapter(
        self, builder: NarrationBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One failing chapter degrades alone; siblings are untouched.

        The semaphore must not have changed the per-chapter absorb contract:
        the failure is caught inside the gate, the deterministic fallback part
        is marked ``degraded``, and no sibling task is cancelled.
        """
        parts, probe = await _run_fanout(
            builder,
            monkeypatch,
            bound=2,
            chapters=_chapters(6),
            fail_numbers=frozenset({3}),
        )

        assert probe.calls == 6
        assert len(parts) == 6
        by_id = {p["part_id"]: p for p in parts}
        assert by_id["ch03-narration"].get("degraded") is True
        assert all(not p.get("degraded") for pid, p in by_id.items() if pid != "ch03-narration")

    async def test_empty_chapter_list(
        self, builder: NarrationBuilder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No chapters means no LLM calls and no semaphore work."""
        parts, probe = await _run_fanout(builder, monkeypatch, bound=2, chapters=[])

        assert parts == []
        assert probe.calls == 0

    def test_tts_fan_out_has_the_same_ceiling(self) -> None:
        """The TTS gather is bounded by a constant of the same shape.

        ``_synthesize_parts`` builds a real ``TTSClient`` against
        ``service_url("tts")`` inside the function body, so exercising its
        semaphore would mean mocking service discovery plus the HTTP client —
        an unfaithful mock of the very seam under test. Only the constant is
        pinned here; the behavioural proof lives in the chapter fan-out tests
        above, which exercise the identical pattern.
        """
        assert nb._MAX_CONCURRENT_TTS_CALLS >= 1
        assert nb._MAX_CONCURRENT_CHAPTER_LLM_CALLS >= 1
