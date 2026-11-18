"""Distributed job locking via Redis SET NX to prevent duplicate processing.

⚠️ CURRENTLY UNUSED BY PRODUCTION CODE — and that is the correct state.

The narrative background tasks (``_process_storyline``, ``_process_narration``,
``_process_narration_shotlist``) used to call ``acquire_job_lock`` on their
``job_id``. That guard could never fire: each of those tasks has exactly ONE
call site — the POST handler that mints ``job_id = str(uuid7())`` immediately
before spawning it. A freshly minted uuid7 is unknown to every other replica,
so ``SET NX`` always succeeded and the "locked by another pod" branch was dead
code advertising a duplicate-processing guard that did not exist.

Nothing re-dispatches an existing job_id, verified across ``src/``:
  * one call site per task, all in ``src/api/narrative/`` request handlers;
  * no retry / requeue / resume path;
  * ``StaleJobReconciler`` (``src/services/reconciler.py``) only UPDATEs stuck
    ``processing`` rows to ``failed`` — it never re-runs work;
  * none of ``StorylineRequest`` / ``NarrationRequest`` /
    ``NarrationForShotListRequest`` accepts a caller-supplied ``job_id``.

Note what this lock could NOT have fixed either way: two identical concurrent
POSTs get two DIFFERENT job_ids, so a job-id-keyed lock never deduplicated
them. Result de-duplication is the tenant-namespaced Redis result cache, not
this module.

This module is kept — correct, fail-open and unit-tested — because the moment
any of the four bullets above stops holding (a caller-supplied job_id, a retry,
a resume, or a reconciler that re-dispatches instead of failing out), the
duplicate-LLM-spend hazard becomes real and this is the primitive to re-instate
at the top of the affected ``_process_*`` task. ``tests/unit/narrative/
test_job_lock_is_vestigial.py`` fails if that day arrives without the lock
coming back.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable
from typing import TYPE_CHECKING, cast

from redis.exceptions import RedisError

if TYPE_CHECKING:
    from w2v_common.cache.redis_client import RedisClient

logger = logging.getLogger(__name__)

_LOCK_PREFIX = "narrative:lock:job:"
_OWNER_ID = uuid.uuid7().hex[-12:]

# Lua script: release only if this pod owns the lock
_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


async def acquire_job_lock(redis_client: RedisClient | None, job_id: str, ttl: int = 300) -> bool:
    """Attempt to acquire a distributed lock for a job.

    Uses Redis SET NX + EX for atomic lock acquisition.
    Fail-open: returns True if Redis is unavailable (allows processing).

    The fail-open handler must name ``redis.exceptions.RedisError``: it derives
    from ``Exception``, not ``OSError``, and its ``ConnectionError``/
    ``TimeoutError`` subclasses only shadow the builtin names. The original
    all-builtins tuple therefore never matched a real Redis failure, so a blip
    escaped and the duplicate-processing guard killed the job instead of
    letting it through. ``OSError`` stays for socket-level failures raised
    below the client.

    Args:
        redis_client: RedisClient instance or None.
        job_id: Job ID to lock.
        ttl: Lock TTL in seconds (should exceed max expected processing time).

    Returns:
        True if lock acquired (or Redis unavailable), False if another pod holds the lock.
    """
    if redis_client is None:
        return True
    try:
        client = redis_client._ensure_connected()
        key = f"{_LOCK_PREFIX}{job_id}"
        result = await client.set(key, _OWNER_ID, nx=True, ex=ttl)
        if result:
            logger.debug("Acquired lock for job %s", job_id)
            return True
        logger.info("Job %s already locked by another pod", job_id)
        return False
    except (OSError, RedisError, RuntimeError) as exc:
        logger.warning("Redis lock acquisition failed for job %s: %s (fail-open)", job_id, exc)
        return True


async def release_job_lock(redis_client: RedisClient | None, job_id: str) -> None:
    """Release a distributed lock for a job.

    Uses a Lua script to ensure only the lock owner can release it.
    Fail-open: silently ignores errors.

    Args:
        redis_client: RedisClient instance or None.
        job_id: Job ID to unlock.
    """
    if redis_client is None:
        return
    try:
        client = redis_client._ensure_connected()
        key = f"{_LOCK_PREFIX}{job_id}"
        await cast(Awaitable[str], client.eval(_RELEASE_SCRIPT, 1, key, _OWNER_ID))
        logger.debug("Released lock for job %s", job_id)
    except (OSError, RedisError, RuntimeError) as exc:
        logger.warning("Redis lock release failed for job %s: %s", job_id, exc)
