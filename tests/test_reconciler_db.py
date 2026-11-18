"""DB-backed test for the stale-job reconciler SECURITY DEFINER path.

Requires a real postgres (the ``db_session_factory`` fixture, content_service_ci
in CI / a local postgres otherwise). The CI schema is built by
``Base.metadata.create_all`` (not alembic), so the ``reconcile_stale_jobs``
function does NOT exist by default — we create it here from the same single-source
DDL the migration uses. The test session connects as the table owner, which
bypasses ENABLE-not-FORCE RLS, so cross-tenant inserts/reads work without a GUC.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from sqlalchemy import select, text

from src.database.models import NarrationJob, ScriptBatch, ScriptJob, StorylineJob
from src.services.reconciler import (
    SCRIPT_BATCH_BACKFILL_SQL,
    SCRIPT_BATCH_NON_TERMINAL_STATUSES,
    SCRIPT_BATCH_REAPED_STATUS,
    SCRIPT_BATCH_TERMINAL_STATUSES,
    StaleJobReconciler,
    create_reconcile_function_statements,
    upgrade_statements,
)
from tests.conftest import TENANT_A, TENANT_B, TENANT_C


async def _ensure_function(session_factory) -> None:
    async with session_factory() as session:
        for stmt in create_reconcile_function_statements():
            await session.execute(text(stmt))
        await session.commit()


async def test_reconcile_marks_only_stale_processing_failed(db_session_factory) -> None:
    now = dt.datetime.now(dt.UTC)
    stale = now - dt.timedelta(hours=2)

    async with db_session_factory() as session:
        session.add_all(
            [
                # Stale processing rows — should be reconciled to failed.
                StorylineJob(
                    job_id="sl-stale",
                    tenant_id=TENANT_A,
                    status="processing",
                    created_at=stale,
                    updated_at=stale,
                ),
                NarrationJob(
                    job_id="nr-stale",
                    tenant_id=TENANT_A,
                    status="processing",
                    created_at=stale,
                    updated_at=stale,
                ),
                ScriptJob(
                    job_id="sc-stale",
                    tenant_id=TENANT_B,
                    status="processing",
                    part_id="p1",
                    created_at=stale,
                    started_at=stale,
                ),
                # Fresh processing rows — must be left alone.
                StorylineJob(
                    job_id="sl-fresh",
                    tenant_id=TENANT_A,
                    status="processing",
                    created_at=now,
                    updated_at=now,
                ),
                ScriptJob(
                    job_id="sc-fresh",
                    tenant_id=TENANT_B,
                    status="processing",
                    part_id="p2",
                    created_at=now,
                    started_at=now,
                ),
                # Terminal rows — must be left alone even though they are old.
                StorylineJob(
                    job_id="sl-done",
                    tenant_id=TENANT_A,
                    status="completed",
                    created_at=stale,
                    updated_at=stale,
                ),
                ScriptJob(
                    job_id="sc-failed",
                    tenant_id=TENANT_B,
                    status="failed",
                    part_id="p3",
                    created_at=stale,
                    started_at=stale,
                ),
            ]
        )
        await session.commit()

    await _ensure_function(db_session_factory)

    reconciler = StaleJobReconciler(
        interval_seconds=999,
        stale_after_seconds=1800,  # 30 min — the 2h-old rows are stale
        pending_stale_after_seconds=21600,  # 6h — no pending row here is that old
        session_provider=db_session_factory,
    )
    count = await reconciler.reconcile_once()

    # Exactly the three stale processing rows (one per table).
    assert count == 3

    async with db_session_factory() as session:

        async def _get(model, job_id):
            return (await session.execute(select(model).where(model.job_id == job_id))).scalar_one()

        sl_stale = await _get(StorylineJob, "sl-stale")
        assert sl_stale.status == "failed"
        assert sl_stale.error == "reconciled: stale processing job"

        nr_stale = await _get(NarrationJob, "nr-stale")
        assert nr_stale.status == "failed"
        assert nr_stale.error == "reconciled: stale processing job"

        sc_stale = await _get(ScriptJob, "sc-stale")
        assert sc_stale.status == "failed"
        assert sc_stale.error_message == "reconciled: stale processing job"
        assert sc_stale.completed_at is not None

        # Untouched rows.
        assert (await _get(StorylineJob, "sl-fresh")).status == "processing"
        assert (await _get(ScriptJob, "sc-fresh")).status == "processing"
        assert (await _get(StorylineJob, "sl-done")).status == "completed"
        assert (await _get(ScriptJob, "sc-failed")).status == "failed"

    # Second sweep is a no-op (idempotent — nothing left in processing+stale).
    assert await reconciler.reconcile_once() == 0


async def test_migration_upgrade_statements_apply_and_callable(db_session_factory) -> None:
    """The full migration DDL payload applies cleanly and leaves a callable function.

    ``upgrade_statements()`` is what migration 007 runs: CREATE OR REPLACE + REVOKE
    EXECUTE FROM PUBLIC + the ``DO``-block GRANT guarded on ``pg_roles``. The simpler
    ``create_reconcile_function_statements()`` used above omits the REVOKE and GRANT, so
    this test is what proves the migration's actual payload (incl. the CI-safety guard,
    since the ``w2v_content_app`` role does not exist in CI) executes without error.
    """
    async with db_session_factory() as session:
        for stmt in upgrade_statements():
            await session.execute(text(stmt))
        await session.commit()

    async with db_session_factory() as session:
        result = await session.execute(
            text(
                "SELECT reconcile_stale_jobs("
                "make_interval(secs => :secs), make_interval(secs => :pending_secs))"
            ),
            {"secs": 1800, "pending_secs": 21600},
        )
        assert isinstance(result.scalar_one(), int)
        await session.commit()


_ON_FUNCTION_RE = re.compile(r"ON FUNCTION\s+(reconcile_stale_jobs\s*\([^)]*\))", re.IGNORECASE)


async def test_every_privilege_statement_names_a_signature_that_exists(db_session_factory) -> None:
    """Every GRANT/REVOKE in the migration must name a signature the migration LEAVES behind.

    This is the assertion the test above cannot make. ``upgrade_statements()`` runs its
    GRANT inside a ``DO`` block guarded on ``pg_roles``, and ``w2v_content_app`` exists
    only in prod -- so in CI the guard is FALSE, the GRANT never executes, and a wrong
    signature inside it is INVISIBLE to a test that merely applies the payload. That is
    exactly how a GRANT naming the dropped one-argument overload shipped green and then
    crash-looped the prod ``run-migrations`` initContainer with ``function
    reconcile_stale_jobs(interval) does not exist``: ``DROP FUNCTION IF EXISTS
    reconcile_stale_jobs(interval)`` runs first, so the overload the GRANT named was
    already gone.

    Asserting the signatures directly needs no role, so this fails in CI, where it matters.
    """
    async with db_session_factory() as session:
        for stmt in upgrade_statements():
            await session.execute(text(stmt))
        await session.commit()

    signatures = [
        match.group(1) for stmt in upgrade_statements() for match in _ON_FUNCTION_RE.finditer(stmt)
    ]
    # Floor on what was actually found: an empty list would make the loop below
    # vacuously pass, which is the failure mode this test exists to prevent.
    assert len(signatures) == 2, f"expected the REVOKE and the GRANT, found {signatures}"

    async with db_session_factory() as session:
        for signature in signatures:
            resolved = (
                await session.execute(
                    text("SELECT to_regprocedure(:sig)::text"), {"sig": signature}
                )
            ).scalar_one()
            assert resolved is not None, (
                f"privilege statement names {signature}, which does not exist once the "
                f"migration payload has run -- it would raise UndefinedFunction and abort "
                f"the migration in any environment where the pg_roles guard does not skip "
                f"it (i.e. prod)"
            )


async def test_reconcile_reclaims_pending_jobs_that_never_started(db_session_factory) -> None:
    """A row stranded in ``pending`` must be reclaimed — but only on its OWN cutoff.

    ``TaskManager._run_with_semaphore`` does ``coro.close(); return`` for every job
    still queued behind the semaphore once ``_shutdown_event`` is set, so a rolling
    deploy silently discards the queued tail of a batch. That coroutine never runs
    ``mark_processing`` and never runs an ``except`` block, so with the sweep scoped
    to ``status = 'processing'`` NOTHING ever reclaimed the row: ``GET /jobs/{id}``
    answered ``pending`` for the life of the database.

    The second half of this test is the half that makes the fix safe rather than
    merely present: ``pending`` is a LEGITIMATE queued state, so a row that is old
    by the ``processing`` cutoff (30 min) but young by the ``pending`` cutoff (6h)
    must be SPARED. Collapsing the two intervals into one would fail out live
    queued work, and this asserts that it does not happen.
    """
    now = dt.datetime.now(dt.UTC)
    ancient = now - dt.timedelta(hours=8)  # past the 6h pending cutoff
    queued = now - dt.timedelta(hours=1)  # past 30 min, well inside 6h

    async with db_session_factory() as session:
        session.add_all(
            [
                StorylineJob(
                    job_id="sl-orphan",
                    tenant_id=TENANT_A,
                    status="pending",
                    created_at=ancient,
                    updated_at=ancient,
                ),
                NarrationJob(
                    job_id="nr-orphan",
                    tenant_id=TENANT_A,
                    status="pending",
                    created_at=ancient,
                    updated_at=ancient,
                ),
                ScriptJob(
                    job_id="sc-orphan",
                    tenant_id=TENANT_B,
                    status="pending",
                    part_id="p1",
                    created_at=ancient,
                ),
                # Legitimately queued behind the concurrency limit — must survive.
                StorylineJob(
                    job_id="sl-queued",
                    tenant_id=TENANT_A,
                    status="pending",
                    created_at=queued,
                    updated_at=queued,
                ),
                ScriptJob(
                    job_id="sc-queued",
                    tenant_id=TENANT_B,
                    status="pending",
                    part_id="p2",
                    created_at=queued,
                ),
            ]
        )
        await session.commit()

    await _ensure_function(db_session_factory)

    reconciler = StaleJobReconciler(
        interval_seconds=999,
        stale_after_seconds=1800,
        pending_stale_after_seconds=21600,
        session_provider=db_session_factory,
    )
    count = await reconciler.reconcile_once()

    assert count == 3

    async with db_session_factory() as session:

        async def _get(model, job_id):
            return (await session.execute(select(model).where(model.job_id == job_id))).scalar_one()

        sl = await _get(StorylineJob, "sl-orphan")
        assert sl.status == "failed"
        assert sl.error == "reconciled: never started (worker lost the job)"
        assert (await _get(NarrationJob, "nr-orphan")).status == "failed"
        sc = await _get(ScriptJob, "sc-orphan")
        assert sc.status == "failed"
        assert sc.error_message == "reconciled: never started (worker lost the job)"

        # Old by the processing cutoff, young by the pending cutoff -> spared.
        assert (await _get(StorylineJob, "sl-queued")).status == "pending"
        assert (await _get(ScriptJob, "sc-queued")).status == "pending"

    assert await reconciler.reconcile_once() == 0


# ---------------------------------------------------------------------------
# script_batches — the table the reaper did not cover at all.
# ---------------------------------------------------------------------------

_BATCH_REPOSITORY_SOURCE = Path("src/repository/batch_repository.py")

# Lines of the form ``status = "..."`` / ``status="..."`` / ``new_status = "..."``.
# ``update_status`` writes ternaries, so both literals on such a line count.
_STATUS_ASSIGNMENT_RE = re.compile(r"^\s*(?:new_status|status)\s*=[^=].*$", re.MULTILINE)
_STATUS_LITERAL_RE = re.compile(r'"([a-z_]+)"')


def _batch_status_vocabulary() -> set[str]:
    """Every batch status literal ``BatchRepository`` can write, read from its source.

    Deliberately derived from the REPOSITORY, not from the reconciler's own
    constants: a test that reads the same frozenset the predicate is built from
    could never fail. This is the independent side of the invariant.
    """
    source = _BATCH_REPOSITORY_SOURCE.read_text(encoding="utf-8")
    vocabulary: set[str] = set()
    for line in _STATUS_ASSIGNMENT_RE.findall(source):
        vocabulary.update(_STATUS_LITERAL_RE.findall(line))
    return vocabulary


async def _seed_batch(session_factory, *, batch_id, tenant_id, status, created_at, total_parts=6):
    async with session_factory() as session:
        session.add(
            ScriptBatch(
                batch_id=batch_id,
                tenant_id=tenant_id,
                total_parts=total_parts,
                status=status,
                created_at=created_at,
            )
        )
        await session.commit()


async def test_every_non_terminal_batch_status_is_reachable_by_the_reaper(
    db_session_factory,
) -> None:
    """INVARIANT: every non-terminal ``script_batches`` status the repository can
    write must be reachable by the reaper's predicate.

    ``script_batches`` was in NO pass of ``reconcile_stale_jobs`` — not the
    ``processing`` pass, not the ``pending`` pass. The failure mode this test
    exists to prevent is silent: a future migration that adds a batch state, or
    narrows the sweep's ``IN`` list, strands rows exactly the way the twelve
    childless 2026-05-06 batches were stranded, and NOTHING errors. Statuses are
    read out of ``batch_repository.py`` rather than out of the reconciler's own
    constant, so the two sides can actually disagree.
    """
    vocabulary = _batch_status_vocabulary()

    # Floor on what the parse found: an empty or truncated vocabulary would make
    # the loop below vacuously pass, which is the whole failure mode here.
    assert vocabulary >= {"pending", "in_progress", "completed", "partial"}, (
        f"parsed batch status vocabulary {sorted(vocabulary)} is missing known members — "
        f"the regex no longer matches {_BATCH_REPOSITORY_SOURCE}, so this test is vacuous"
    )

    non_terminal = vocabulary - SCRIPT_BATCH_TERMINAL_STATUSES
    assert non_terminal, "no non-terminal batch status found; the parse is wrong"

    ancient = dt.datetime.now(dt.UTC) - dt.timedelta(hours=8)
    for status in sorted(non_terminal):
        await _seed_batch(
            db_session_factory,
            batch_id=f"batch-{status}",
            tenant_id=TENANT_A,
            status=status,
            created_at=ancient,
        )

    await _ensure_function(db_session_factory)

    reconciler = StaleJobReconciler(
        interval_seconds=999,
        stale_after_seconds=1800,
        pending_stale_after_seconds=21600,
        session_provider=db_session_factory,
    )
    reaped = await reconciler.reconcile_once()

    # Per-status assertions FIRST: a bare count assertion here would fail with a
    # bare `assert 0 == 2` and name neither the stranded status nor the predicate,
    # which is useless to whoever's migration broke it.
    async with db_session_factory() as session:
        for status in sorted(non_terminal):
            row = (
                await session.execute(
                    select(ScriptBatch).where(ScriptBatch.batch_id == f"batch-{status}")
                )
            ).scalar_one()
            assert row.status == SCRIPT_BATCH_REAPED_STATUS, (
                f"a batch stranded at {status!r} was NOT reclaimed: the reaper's predicate "
                f"({sorted(SCRIPT_BATCH_NON_TERMINAL_STATUSES)}) does not cover every "
                f"non-terminal status BatchRepository writes ({sorted(non_terminal)}). "
                f"Rows in that state are stranded forever, exactly as the twelve childless "
                f"prod batches were."
            )
            assert row.completed_at is not None
            assert row.error is not None, (
                f"batch stranded at {status!r} reached a terminal status with NO recorded "
                f"reason — 'partial' alone cannot distinguish a reconciled batch from one "
                f"whose parts genuinely half-failed"
            )

    assert reaped == len(non_terminal), (
        f"the sweep reported {reaped} rows but {len(non_terminal)} non-terminal batches were seeded"
    )


async def test_reconcile_reaps_stranded_batches_with_an_honest_reason(db_session_factory) -> None:
    """A batch whose part jobs were never created is reclaimed, and says so.

    Two abandonment shapes must be distinguishable from the recorded reason,
    because they are different bugs: a childless batch (``create_batch`` committed
    the batch row and then lost the worker before the ``create_job`` loop) versus a
    batch whose children exist but whose terminal recount never ran.

    The second half is the half that makes the sweep safe rather than merely
    present: a batch inside the ``pending`` cutoff is live queued work and MUST be
    spared, and a batch already terminal must not be rewritten.
    """
    now = dt.datetime.now(dt.UTC)
    ancient = now - dt.timedelta(hours=8)  # past the 6h pending cutoff
    queued = now - dt.timedelta(hours=1)  # past 30 min, well inside 6h

    await _seed_batch(
        db_session_factory,
        batch_id="b-childless",
        tenant_id=TENANT_A,
        status="pending",
        created_at=ancient,
    )
    await _seed_batch(
        db_session_factory,
        batch_id="b-halfdone",
        tenant_id=TENANT_B,
        status="in_progress",
        created_at=ancient,
        total_parts=3,
    )
    await _seed_batch(
        db_session_factory,
        batch_id="b-queued",
        tenant_id=TENANT_A,
        status="pending",
        created_at=queued,
    )
    await _seed_batch(
        db_session_factory,
        batch_id="b-terminal",
        tenant_id=TENANT_A,
        status="completed",
        created_at=ancient,
    )

    async with db_session_factory() as session:
        session.add_all(
            [
                ScriptJob(
                    job_id="b-halfdone-1",
                    tenant_id=TENANT_B,
                    batch_id="b-halfdone",
                    status="completed",
                    part_id="p1",
                    created_at=ancient,
                ),
                # Belongs to another tenant's batch id namespace — proves the reason
                # counts children by (batch_id, tenant_id) off the persisted row.
                ScriptJob(
                    job_id="b-halfdone-2",
                    tenant_id=TENANT_B,
                    batch_id="b-halfdone",
                    status="failed",
                    part_id="p2",
                    created_at=ancient,
                ),
            ]
        )
        await session.commit()

    await _ensure_function(db_session_factory)

    reconciler = StaleJobReconciler(
        interval_seconds=999,
        stale_after_seconds=1800,
        pending_stale_after_seconds=21600,
        session_provider=db_session_factory,
    )
    # Two batches reaped. The two ancient script_jobs rows are terminal already, so
    # the job passes contribute nothing.
    assert await reconciler.reconcile_once() == 2

    async with db_session_factory() as session:

        async def _batch(batch_id):
            return (
                await session.execute(select(ScriptBatch).where(ScriptBatch.batch_id == batch_id))
            ).scalar_one()

        childless = await _batch("b-childless")
        assert childless.status == "partial"
        assert childless.completed_at is not None
        assert "0 of 6 part job rows exist (0 completed)" in childless.error
        assert "part jobs were never created" in childless.error
        # Must not imply the user did anything.
        assert "cancel" not in childless.error.lower()

        halfdone = await _batch("b-halfdone")
        assert halfdone.status == "partial"
        assert "2 of 3 part job rows exist (1 completed)" in halfdone.error
        assert "no terminal recount ever ran" in halfdone.error

        # Live queued work and already-terminal rows are untouched.
        queued_batch = await _batch("b-queued")
        assert queued_batch.status == "pending"
        assert queued_batch.error is None
        assert (await _batch("b-terminal")).status == "completed"

    # Idempotent: nothing left in the non-terminal set past the cutoff.
    assert await reconciler.reconcile_once() == 0


async def test_migration_backfill_is_chunked_and_idempotent(db_session_factory) -> None:
    """Migration 008's backfill payload reaps in chunks and re-runs to a no-op.

    ``SCRIPT_BATCH_BACKFILL_SQL`` takes a ``:chunk`` bound so the migration never
    issues one giant UPDATE across every tenant. Driving it with ``chunk=1`` here
    proves the LIMIT is honoured (a payload that ignored it would reap all three
    rows on the first pass) as well as the re-runnability the migration relies on.
    """
    ancient = dt.datetime.now(dt.UTC) - dt.timedelta(hours=8)
    for i, tenant in enumerate((TENANT_A, TENANT_B, TENANT_C)):
        await _seed_batch(
            db_session_factory,
            batch_id=f"bf-{i}",
            tenant_id=tenant,
            status="pending",
            created_at=ancient,
        )

    async with db_session_factory() as session:
        first = await session.execute(
            text(SCRIPT_BATCH_BACKFILL_SQL), {"cutoff": "6 hours", "chunk": 1}
        )
        assert first.rowcount == 1, "the :chunk LIMIT is not being honoured"
        await session.commit()

        reaped = 1
        for _ in range(10):
            result = await session.execute(
                text(SCRIPT_BATCH_BACKFILL_SQL), {"cutoff": "6 hours", "chunk": 1}
            )
            await session.commit()
            if result.rowcount <= 0:
                break
            reaped += result.rowcount
        assert reaped == 3

    async with db_session_factory() as session:
        rows = (
            (await session.execute(select(ScriptBatch).where(ScriptBatch.batch_id.like("bf-%"))))
            .scalars()
            .all()
        )
        assert {r.status for r in rows} == {"partial"}
        assert all(r.error and "part jobs were never created" in r.error for r in rows)
