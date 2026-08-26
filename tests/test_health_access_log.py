"""
Tests for `app.main.HealthAccessLogFilter`: successful `GET /health`
access-log lines must be demoted from INFO to DEBUG (dropped unless DEBUG
logging is enabled); everything else passes through untouched.
"""

import logging

import pytest

from app.main import HealthAccessLogFilter


def _access_record(method: str, path: str, status_code: int) -> logging.LogRecord:
    # Mirrors uvicorn.access: '%s - "%s %s HTTP/%s" %d' with these args.
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:1234", method, path, "1.1", status_code),
        exc_info=None,
    )


@pytest.fixture
def access_logger():
    logger = logging.getLogger("uvicorn.access")
    old_level = logger.level
    yield logger
    logger.setLevel(old_level)


def test_successful_health_dropped_at_info(access_logger):
    access_logger.setLevel(logging.INFO)
    record = _access_record("GET", "/health", 204)
    assert HealthAccessLogFilter().filter(record) is False


def test_successful_health_kept_as_debug_at_debug(access_logger):
    access_logger.setLevel(logging.DEBUG)
    record = _access_record("GET", "/health", 204)
    assert HealthAccessLogFilter().filter(record) is True
    assert record.levelno == logging.DEBUG
    assert record.levelname == "DEBUG"


def test_failed_health_kept_at_info(access_logger):
    access_logger.setLevel(logging.INFO)
    record = _access_record("GET", "/health", 500)
    assert HealthAccessLogFilter().filter(record) is True
    assert record.levelno == logging.INFO


def test_other_paths_kept_at_info(access_logger):
    access_logger.setLevel(logging.INFO)
    for method, path in [("GET", "/status"), ("POST", "/health"), ("GET", "/me")]:
        record = _access_record(method, path, 200)
        assert HealthAccessLogFilter().filter(record) is True
        assert record.levelno == logging.INFO


def test_non_access_records_pass_through():
    record = logging.LogRecord(
        name="app.lib.repo",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg="plain message with %s",
        args=("one-arg",),
        exc_info=None,
    )
    assert HealthAccessLogFilter().filter(record) is True
