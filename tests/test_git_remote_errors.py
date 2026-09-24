"""
Classification of failing network git commands (app.lib.git): only output
that shows the *remote* was down/unreachable becomes a GitRemoteError, an
HTTP 503 additionally flags maintenance, and credentials in remote URLs are
never echoed.
"""

from app.lib import git


def _failure(stderr: str) -> git.GitError:
    return git.GitError(f"Command git pull failed with: {stderr}", returncode=128)


def test_http_503_is_maintenance():
    error = _failure(
        "fatal: unable to access 'https://git.example.com/r.git/':"
        " The requested URL returned error: 503"
    )
    classified = git.classify_remote_failure(error)
    assert isinstance(classified, git.GitRemoteError)
    assert classified.maintenance is True
    assert classified.returncode == 128


def test_other_http_5xx_is_unavailable_not_maintenance():
    classified = git.classify_remote_failure(
        _failure("fatal: unable to access 'https://x/': The requested URL returned error: 502")
    )
    assert isinstance(classified, git.GitRemoteError)
    assert classified.maintenance is False


def test_connection_level_failures_are_unavailable():
    for stderr in [
        "fatal: unable to access 'https://x/': Could not resolve host: git.example.com",
        "fatal: unable to access 'https://x/': Failed to connect to git.example.com port 443",
        "ssh: connect to host git.example.com port 22: Connection refused",
    ]:
        classified = git.classify_remote_failure(_failure(stderr))
        assert isinstance(classified, git.GitRemoteError), stderr
        assert classified.maintenance is False


def test_our_own_failures_stay_plain_git_errors():
    for stderr in [
        "fatal: Authentication failed for 'https://git.example.com/r.git/'",
        "fatal: unable to access 'https://x/': The requested URL returned error: 403",
        "! [rejected] main -> main (fetch first)",
        "git@git.example.com: Permission denied (publickey).",
    ]:
        error = _failure(stderr)
        assert git.classify_remote_failure(error) is error, stderr


def test_timeouts_and_remote_errors_pass_through_unchanged():
    timeout = git.GitTimeoutError("Timeout of 30 seconds exceeded")
    assert git.classify_remote_failure(timeout) is timeout
    remote = git.GitRemoteError("x", maintenance=True)
    assert git.classify_remote_failure(remote) is remote


def test_credentials_in_urls_are_redacted():
    text = (
        "fatal: unable to access 'https://yac:s3cr3t-token@git.example.com/r.git/':"
        " The requested URL returned error: 503"
    )
    redacted = git.redact_credentials(text)
    assert "s3cr3t" not in redacted
    assert "https://***@git.example.com/r.git/" in redacted
    # URLs without credentials are untouched
    assert git.redact_credentials("https://git.example.com/r.git") == (
        "https://git.example.com/r.git"
    )
