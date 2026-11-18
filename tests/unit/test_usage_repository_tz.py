"""Unit test: daily usage bucket keys on the UTC calendar day, not local tz.

Regression guard for the previous ``date.today()`` (local-timezone) bug, which
split a tenant-day across two rows for the hours around UTC midnight on hosts
running behind UTC. No database required — a fake session captures the emitted
INSERT and we read back the bound ``date`` value.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, cast

from sqlalchemy.dialects import postgresql

from src.repository import usage_repository
from src.repository.usage_repository import UsageRepository
from tests.conftest import TENANT_A_STR


class _FakeSession:
    def __init__(self) -> None:
        self.statements: list = []
        self.committed = False

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def execute(self, stmt, params=None):
        self.statements.append(stmt)
        return

    async def commit(self) -> None:
        self.committed = True


class _FixedDateTime:
    """Stand-in for ``datetime`` frozen at one instant, on a host BEHIND UTC.

    ⚠️ ``now(tz=None)`` MUST honour its argument by returning the *local naive*
    reading, exactly as the real ``datetime.now()`` does. An earlier version
    returned the UTC-aware ``_FIXED`` for every call, tz or not — so the two
    readings this test exists to distinguish were identical, and reintroducing
    the very bug named in the module docstring (``datetime.now(tz=UTC)`` →
    ``datetime.now()``) left the test GREEN.

    The instant is chosen so the two readings fall on DIFFERENT calendar days:
    00:30 UTC on 2026-06-18 is 19:30 on 2026-06-17 for a host at UTC-5.
    """

    _LOCAL = dt.timezone(dt.timedelta(hours=-5), "UTC-5")
    _FIXED = dt.datetime(2026, 6, 18, 0, 30, tzinfo=dt.UTC)  # 00:30 UTC
    # What a naive ``datetime.now()`` returns on that UTC-5 host: 2026-06-17.
    LOCAL_DATE = _FIXED.astimezone(_LOCAL).date()
    UTC_DATE = _FIXED.date()

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            # Naive local wall-clock time, like the stdlib.
            return cls._FIXED.astimezone(cls._LOCAL).replace(tzinfo=None)
        return cls._FIXED.astimezone(tz)


async def test_record_buckets_on_utc_date(monkeypatch) -> None:
    # Precondition: the fake really does distinguish the two readings, so a
    # failure below is about the repository, not a fake that ignores ``tz``.
    assert _FixedDateTime.LOCAL_DATE != _FixedDateTime.UTC_DATE
    assert dt.date(2026, 6, 17) == _FixedDateTime.LOCAL_DATE

    monkeypatch.setattr(usage_repository, "datetime", _FixedDateTime)

    session = _FakeSession()
    repo = UsageRepository(cast(Any, lambda: session))

    # A real tenant uuid: ``record`` binds the RLS tenant GUC before its INSERT and
    # ``require_tenant`` rejects the old ``"t1"`` slug outright.
    await repo.record(tenant_id=TENANT_A_STR, scripts_generated=1)

    assert session.committed is True
    inserts = [s for s in session.statements if s.__class__.__name__ == "Insert"]
    assert len(inserts) == 1

    compiled = inserts[0].compile(dialect=postgresql.dialect())
    bound = list(compiled.params.values())
    # The bucket date must be the UTC calendar day (2026-06-18), independent of
    # the host's local timezone...
    assert dt.date(2026, 6, 18) in bound
    # ...and must NOT be the host-local day (2026-06-17), which is what a
    # tz-naive ``datetime.now()`` / ``date.today()`` would have produced here.
    assert _FixedDateTime.LOCAL_DATE not in bound, (
        "usage bucketed on the host's LOCAL calendar day: a host behind UTC "
        "splits one tenant-day across two rows around UTC midnight"
    )
