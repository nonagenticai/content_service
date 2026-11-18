"""Usage repository -- daily usage tracking per tenant."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from w2v_common.database.rls import bind_session_tenant

from src.repository.models import ScriptUsage
from src.tenant import require_tenant

logger = logging.getLogger(__name__)


class UsageRepository:
    """Manages ScriptUsage rows (upsert daily counters)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record(
        self,
        *,
        tenant_id: str,
        scripts_generated: int = 0,
        scripts_accepted: int = 0,
        llm_tokens_used: int = 0,
        is_error: bool = False,
    ) -> None:
        """Upsert daily usage counters for a tenant."""
        async with self._session_factory() as session:
            await bind_session_tenant(session, require_tenant(tenant_id))
            # Bucket on the UTC calendar day, not the host's local timezone:
            # ``date.today()`` rolls over at local midnight, so a host running
            # behind UTC would attribute usage to the wrong day for the hours
            # around UTC midnight and split a single tenant-day across two rows.
            today = datetime.now(tz=UTC).date()
            stmt = pg_insert(ScriptUsage).values(
                tenant_id=tenant_id,
                date=today,
                request_count=1,
                scripts_generated=scripts_generated,
                scripts_accepted=scripts_accepted,
                llm_tokens_used=llm_tokens_used,
                error_count=1 if is_error else 0,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_script_usage_tenant_date",
                set_={
                    "request_count": ScriptUsage.request_count + 1,
                    "scripts_generated": ScriptUsage.scripts_generated + scripts_generated,
                    "scripts_accepted": ScriptUsage.scripts_accepted + scripts_accepted,
                    "llm_tokens_used": ScriptUsage.llm_tokens_used + llm_tokens_used,
                    "error_count": ScriptUsage.error_count + (1 if is_error else 0),
                },
            )
            await session.execute(stmt)
            await session.commit()
            logger.debug("Recorded usage for tenant=%s", tenant_id)
