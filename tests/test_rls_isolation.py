"""Proves the Phase D RLS policies ISOLATE tenants (not just that they exist).

Connects AS a throwaway non-owner ``NOBYPASSRLS`` role — the posture the runtime
``w2v_content_app`` role has under enforce — and asserts the shipped policies
(applied by ``register_rls_ddl`` on ``create_all``) actually scope reads/writes.
Needs a superuser test DB (``CREATE ROLE`` + ``SET ROLE``); skips loudly in CI.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from tests.conftest import TENANT_A, TENANT_B

_ROLE = "w2v_content_app_rls_test"
_INS = "INSERT INTO script_jobs (job_id, tenant_id, part_id) VALUES (:job_id, :tenant, 'p1')"


@pytest.mark.asyncio
async def test_rls_isolates_tenants_under_restricted_role(db_engine) -> None:
    async with db_engine.begin() as conn:
        # skip-gate FIRST (before the RLS-affected seed): a non-superuser CI role
        # can't CREATE ROLE and its seed would hit the policy with no GUC.
        try:
            await conn.execute(text(f'CREATE ROLE "{_ROLE}" NOSUPERUSER NOBYPASSRLS NOINHERIT'))
        except ProgrammingError as exc:
            pytest.skip(f"cannot CREATE ROLE (needs superuser/CREATEROLE): {exc.orig}")
        await conn.execute(
            text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON script_jobs TO "{_ROLE}"')
        )

        await conn.execute(text(_INS), {"job_id": "job-a", "tenant": TENANT_A})
        await conn.execute(text(_INS), {"job_id": "job-b", "tenant": TENANT_B})

        try:
            await conn.execute(text(f'SET LOCAL ROLE "{_ROLE}"'))

            n = (await conn.execute(text("SELECT count(*) FROM script_jobs"))).scalar()
            assert n == 0, f"leaked {n} rows with no GUC"

            await conn.execute(
                text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(TENANT_A)}
            )
            rows = (await conn.execute(text("SELECT tenant_id FROM script_jobs"))).scalars().all()
            assert rows == [TENANT_A], f"not isolated under GUC=A: {rows}"

            await conn.execute(text("SAVEPOINT s"))
            with pytest.raises(ProgrammingError):
                await conn.execute(text(_INS), {"job_id": "job-x", "tenant": TENANT_B})
            await conn.execute(text("ROLLBACK TO SAVEPOINT s"))
        finally:
            await conn.execute(text("RESET ROLE"))
            await conn.execute(text(f'DROP OWNED BY "{_ROLE}"'))
            await conn.execute(text(f'DROP ROLE IF EXISTS "{_ROLE}"'))


@pytest.mark.asyncio
async def test_tenant_guc_rebinds_after_commit(db_session_factory, monkeypatch) -> None:
    """The after_begin listener keeps ``app.current_tenant`` set across COMMITs.

    Defense-in-depth regression: if a background path ever reuses one session
    across multiple commits, ``bind_session_tenant`` (transaction-scoped) would
    leave the later writes unbound → RLS ``InsufficientPrivilege`` under enforce.
    The ``install_rls_tenant_rebind`` listener re-binds from the identity
    ContextVar on every transaction. Mirrors analysis_service's regression test.
    """
    from w2v_common.auth import clear_identity_context, set_identity_context

    from src.database.rls_events import install_rls_tenant_rebind

    monkeypatch.setenv("TENANT_RLS_MODE", "enforce")
    install_rls_tenant_rebind()
    # The identity ContextVar carries the tenant as a STRING (w2v_common
    # AuthMiddleware sets it from the token claim); the GUC is TEXT too.
    set_identity_context(str(TENANT_A), None, None)
    try:
        async with db_session_factory() as session:
            # First transaction: a write that commits (ends the bound transaction).
            await session.execute(text("SELECT 1"))
            await session.commit()
            # Second, fresh transaction: the GUC must still be the tenant — proving
            # the re-bind fired on after_begin, not just on the initial bind.
            guc = (
                await session.execute(text("SELECT current_setting('app.current_tenant', true)"))
            ).scalar()
            assert guc == str(TENANT_A), f"GUC not re-bound after commit: {guc!r}"
    finally:
        clear_identity_context()

    # Control: with no tenant in context the listener is a no-op (request/no-tenant paths).
    async with db_session_factory() as session:
        guc = (
            await session.execute(text("SELECT current_setting('app.current_tenant', true)"))
        ).scalar()
        assert not guc, f"GUC leaked with empty identity context: {guc!r}"
