"""Tests for structured logging configuration."""

from __future__ import annotations

import json
import logging

from w2v_common.observability.request_context import (
    clear_request_id,
    set_request_id,
)

from src.logging_config import (
    ContextFormatter,
    JSONFormatter,
    configure_logging,
    generate_request_id,
    job_id_var,
    tenant_id_var,
)


class TestJSONFormatter:
    """Tests for JSONFormatter."""

    def test_formats_as_json(self):
        formatter = JSONFormatter(datefmt="%Y-%m-%dT%H:%M:%S")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["message"] == "hello world"
        assert data["level"] == "INFO"
        assert data["logger"] == "test"
        assert "timestamp" in data

    def test_includes_context_vars(self):
        formatter = JSONFormatter(datefmt="%Y-%m-%dT%H:%M:%S")
        set_request_id("req-123")
        token_tid = tenant_id_var.set("tenant-abc")
        token_jid = job_id_var.set("job-456")
        try:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="test",
                args=(),
                exc_info=None,
            )
            output = formatter.format(record)
            data = json.loads(output)
            assert data["request_id"] == "req-123"
            assert data["tenant_id"] == "tenant-abc"
            assert data["job_id"] == "job-456"
        finally:
            clear_request_id()
            tenant_id_var.reset(token_tid)
            job_id_var.reset(token_jid)

    def test_omits_empty_context_vars(self):
        formatter = JSONFormatter(datefmt="%Y-%m-%dT%H:%M:%S")
        clear_request_id()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert "request_id" not in data
        assert "tenant_id" not in data
        assert "job_id" not in data


class TestContextFormatter:
    """Tests for ContextFormatter."""

    def test_adds_context_prefix(self):
        formatter = ContextFormatter(
            fmt="%(ctx)s%(message)s",
        )
        set_request_id("req-abc")
        try:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="hello",
                args=(),
                exc_info=None,
            )
            output = formatter.format(record)
            assert "[req=req-abc]" in output
            assert "hello" in output
        finally:
            clear_request_id()

    def test_empty_context_no_prefix(self):
        formatter = ContextFormatter(
            fmt="%(ctx)s%(message)s",
        )
        clear_request_id()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        assert output == "hello"


class TestConfigureLogging:
    """Tests for configure_logging()."""

    def test_configures_json_mode(self):
        configure_logging(json_logs=True, level="DEBUG")
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JSONFormatter)

    def test_configures_text_mode(self):
        configure_logging(json_logs=False, level="INFO")
        root = logging.getLogger()
        assert root.level == logging.INFO
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, ContextFormatter)


class TestGenerateRequestId:
    """Tests for generate_request_id()."""

    def test_returns_12_char_hex(self):
        rid = generate_request_id()
        assert len(rid) == 12
        int(rid, 16)  # should not raise

    def test_unique_each_call(self):
        ids = {generate_request_id() for _ in range(100)}
        assert len(ids) == 100
