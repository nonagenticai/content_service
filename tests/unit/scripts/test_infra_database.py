"""Tests for infra/database.py -- engine creation with retry and database existence checks."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from src.database.connection import (
    _ensure_database_exists,
    create_engine_with_retry,
    create_session_factory,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine(*, conn=None, begin_side_effect=None):
    """Build a mock async engine with proper async-with support for .begin().

    If begin_side_effect is set, entering the context manager will raise it.
    Otherwise, the context manager yields `conn` (an AsyncMock by default).
    """
    if conn is None:
        conn = AsyncMock()

    @asynccontextmanager
    async def _begin():
        if begin_side_effect:
            raise begin_side_effect
        yield conn

    engine = MagicMock()
    engine.begin = _begin
    return engine


# ---------------------------------------------------------------------------
# _ensure_database_exists
# ---------------------------------------------------------------------------


class TestEnsureDatabaseExists:
    @pytest.mark.asyncio
    async def test_database_already_exists(self) -> None:
        """_ensure_database_exists returns early when database exists."""
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1  # database exists
        mock_conn.execute.return_value = mock_result

        mock_engine = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        mock_engine.dispose = MagicMock()

        with patch("src.database.connection.create_engine", return_value=mock_engine):
            await _ensure_database_exists("postgresql+psycopg://user:pass@localhost:5432/mydb")

        mock_conn.execute.assert_called()
        mock_engine.dispose.assert_called_once()

    @pytest.mark.asyncio
    async def test_database_created(self) -> None:
        """_ensure_database_exists creates the database when it doesn't exist."""
        mock_conn = MagicMock()
        check_result = MagicMock()
        check_result.scalar.return_value = None
        mock_conn.execute.side_effect = [check_result, None]

        mock_engine = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        mock_engine.dispose = MagicMock()

        with patch("src.database.connection.create_engine", return_value=mock_engine):
            await _ensure_database_exists("postgresql+psycopg://user:pass@localhost:5432/newdb")

        assert mock_conn.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_database_already_exists_race(self) -> None:
        """_ensure_database_exists handles race condition where create fails with 'already exists'."""
        mock_conn = MagicMock()
        check_result = MagicMock()
        check_result.scalar.return_value = None

        prog_err = ProgrammingError(
            statement="CREATE DATABASE",
            params={},
            orig=Exception("database already exists"),
        )
        mock_conn.execute.side_effect = [check_result, prog_err]

        mock_engine = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        mock_engine.dispose = MagicMock()

        with patch("src.database.connection.create_engine", return_value=mock_engine):
            await _ensure_database_exists("postgresql+psycopg://user:pass@localhost:5432/racedb")

    @pytest.mark.asyncio
    async def test_invalid_database_name(self) -> None:
        """_ensure_database_exists rejects invalid database names."""
        with pytest.raises(ValueError, match="invalid characters"):
            await _ensure_database_exists("postgresql+psycopg://user:pass@localhost:5432/bad-name!")

    @pytest.mark.asyncio
    async def test_connection_retry_exhaustion(self) -> None:
        """_ensure_database_exists raises ConnectionError after all retries fail."""
        mock_engine = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(
            side_effect=OperationalError(
                statement="SELECT 1",
                params={},
                orig=Exception("connection refused"),
            )
        )
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        mock_engine.dispose = MagicMock()

        with (
            patch("src.database.connection.create_engine", return_value=mock_engine),
            patch("src.database.connection.asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(ConnectionError, match="Failed to connect"),
        ):
            await _ensure_database_exists(
                "postgresql+psycopg://user:pass@localhost:5432/unreachable",
                max_retries=2,
            )

    @pytest.mark.asyncio
    async def test_psycopg_url_preserved(self) -> None:
        """_ensure_database_exists preserves psycopg driver for sync engine (psycopg v3 works sync)."""
        mock_conn = MagicMock()
        check_result = MagicMock()
        check_result.scalar.return_value = 1
        mock_conn.execute.return_value = check_result

        mock_engine = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        mock_engine.dispose = MagicMock()

        with patch(
            "src.database.connection.create_engine", return_value=mock_engine
        ) as mock_create:
            await _ensure_database_exists("postgresql+psycopg://user:pass@localhost:5432/mydb")
            call_args = mock_create.call_args
            url = call_args[0][0]
            assert url.startswith("postgresql+psycopg://")
            assert "user:pass@" in url

    @pytest.mark.asyncio
    async def test_engine_dispose_failure_logged(self) -> None:
        """_ensure_database_exists handles dispose failure gracefully."""
        mock_conn = MagicMock()
        check_result = MagicMock()
        check_result.scalar.return_value = 1
        mock_conn.execute.return_value = check_result

        mock_engine = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        mock_engine.dispose.side_effect = RuntimeError("dispose failed")

        with patch("src.database.connection.create_engine", return_value=mock_engine):
            await _ensure_database_exists("postgresql+psycopg://user:pass@localhost:5432/mydb")

    @pytest.mark.asyncio
    async def test_unexpected_error_raises(self) -> None:
        """_ensure_database_exists re-raises unexpected exceptions."""
        mock_engine = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(side_effect=TypeError("unexpected"))
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        mock_engine.dispose = MagicMock()

        with (
            patch("src.database.connection.create_engine", return_value=mock_engine),
            pytest.raises(TypeError, match="unexpected"),
        ):
            await _ensure_database_exists("postgresql+psycopg://user:pass@localhost:5432/mydb")


# ---------------------------------------------------------------------------
# create_engine_with_retry
# ---------------------------------------------------------------------------


class TestCreateEngineWithRetry:
    @pytest.mark.asyncio
    async def test_success_first_attempt(self) -> None:
        """create_engine_with_retry succeeds on first attempt."""
        mock_conn = AsyncMock()
        mock_engine = _make_engine(conn=mock_conn)

        with patch("src.database.connection.create_async_engine", return_value=mock_engine):
            engine = await create_engine_with_retry(
                "postgresql+psycopg://user:pass@localhost:5432/mydb",
                max_retries=1,
            )

        assert engine is mock_engine

    @pytest.mark.asyncio
    async def test_retry_on_timeout_error(self) -> None:
        """create_engine_with_retry retries on TimeoutError."""
        mock_engine_fail = _make_engine(begin_side_effect=TimeoutError("timed out"))
        mock_conn_ok = AsyncMock()
        mock_engine_ok = _make_engine(conn=mock_conn_ok)

        with (
            patch(
                "src.database.connection.create_async_engine",
                side_effect=[mock_engine_fail, mock_engine_ok],
            ),
            patch("src.database.connection.asyncio.sleep", new_callable=AsyncMock),
        ):
            engine = await create_engine_with_retry(
                "postgresql+psycopg://user:pass@localhost:5432/mydb",
                max_retries=2,
                retry_delay=0.01,
            )

        assert engine is mock_engine_ok

    @pytest.mark.asyncio
    async def test_all_retries_exhausted(self) -> None:
        """create_engine_with_retry raises ConnectionError after exhaustion."""
        mock_engine = _make_engine(begin_side_effect=ConnectionRefusedError("refused"))

        with (
            patch("src.database.connection.create_async_engine", return_value=mock_engine),
            patch("src.database.connection.asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(ConnectionError, match="Failed to connect"),
        ):
            await create_engine_with_retry(
                "postgresql+psycopg://user:pass@localhost:5432/mydb",
                max_retries=2,
                retry_delay=0.01,
            )

    @pytest.mark.asyncio
    async def test_generic_exception_retries(self) -> None:
        """create_engine_with_retry retries on generic exceptions."""
        mock_engine_fail = _make_engine(begin_side_effect=RuntimeError("unexpected"))
        mock_conn_ok = AsyncMock()
        mock_engine_ok = _make_engine(conn=mock_conn_ok)

        with (
            patch(
                "src.database.connection.create_async_engine",
                side_effect=[mock_engine_fail, mock_engine_ok],
            ),
            patch("src.database.connection.asyncio.sleep", new_callable=AsyncMock),
        ):
            engine = await create_engine_with_retry(
                "postgresql+psycopg://user:pass@localhost:5432/mydb",
                max_retries=2,
                retry_delay=0.01,
            )

        assert engine is mock_engine_ok

    @pytest.mark.asyncio
    async def test_creates_database_on_not_exist(self) -> None:
        """create_engine_with_retry creates database when 'does not exist' error."""
        prog_err = ProgrammingError(
            statement="SELECT 1",
            params={},
            orig=Exception('database "mydb" does not exist'),
        )
        mock_engine_fail = _make_engine(begin_side_effect=prog_err)
        mock_conn_ok = AsyncMock()
        mock_engine_ok = _make_engine(conn=mock_conn_ok)

        with (
            patch(
                "src.database.connection.create_async_engine",
                side_effect=[mock_engine_fail, mock_engine_ok],
            ),
            patch(
                "src.database.connection._ensure_database_exists",
                new_callable=AsyncMock,
            ),
            patch("src.database.connection.asyncio.sleep", new_callable=AsyncMock),
        ):
            engine = await create_engine_with_retry(
                "postgresql+psycopg://user:pass@localhost:5432/mydb",
                max_retries=2,
                retry_delay=0.01,
            )

        assert engine is mock_engine_ok

    @pytest.mark.asyncio
    async def test_retry_on_operational_error(self) -> None:
        """create_engine_with_retry retries on OperationalError."""
        op_err = OperationalError(
            statement="SELECT 1",
            params={},
            orig=Exception("connection refused"),
        )
        mock_engine_fail = _make_engine(begin_side_effect=op_err)
        mock_conn_ok = AsyncMock()
        mock_engine_ok = _make_engine(conn=mock_conn_ok)

        with (
            patch(
                "src.database.connection.create_async_engine",
                side_effect=[mock_engine_fail, mock_engine_ok],
            ),
            patch("src.database.connection.asyncio.sleep", new_callable=AsyncMock),
        ):
            engine = await create_engine_with_retry(
                "postgresql+psycopg://user:pass@localhost:5432/mydb",
                max_retries=2,
                retry_delay=0.01,
            )

        assert engine is mock_engine_ok


# ---------------------------------------------------------------------------
# create_session_factory
# ---------------------------------------------------------------------------


class TestCreateSessionFactory:
    def test_create_session_factory(self) -> None:
        """create_session_factory returns an async_sessionmaker."""
        mock_engine = MagicMock()
        factory = create_session_factory(mock_engine)

        assert callable(factory)
