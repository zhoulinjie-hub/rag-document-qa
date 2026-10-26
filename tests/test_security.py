import pytest

from security import (
    DemoAuthenticationError,
    DemoRateLimitError,
    DemoSecurity,
)


def test_signed_session_accepts_valid_token_and_rejects_tampering():
    security = DemoSecurity("a-valid-test-access-code-with-32-chars")
    token, created = security.create_session("a-valid-test-access-code-with-32-chars")

    verified = security.verify_session(token)
    assert verified.session_id == created.session_id
    assert verified.expires_at == created.expires_at

    with pytest.raises(DemoAuthenticationError):
        security.verify_session(token + "tampered")


def test_wrong_access_code_is_rejected():
    security = DemoSecurity("a-valid-test-access-code-with-32-chars")

    with pytest.raises(DemoAuthenticationError):
        security.create_session("wrong-access-code")


def test_short_access_code_is_rejected():
    with pytest.raises(RuntimeError):
        DemoSecurity("too-short")


def test_sliding_window_rate_limit_is_enforced():
    security = DemoSecurity("a-valid-test-access-code-with-32-chars")

    security.rate_limiter.check("pytest", limit=2, window_seconds=60)
    security.rate_limiter.check("pytest", limit=2, window_seconds=60)
    with pytest.raises(DemoRateLimitError) as exc_info:
        security.rate_limiter.check("pytest", limit=2, window_seconds=60)

    assert exc_info.value.retry_after >= 1
