from pathlib import Path


LOGIN_FILES = tuple(Path("media_platform").glob("*/login.py"))


def test_login_failures_exit_nonzero():
    for path in LOGIN_FILES:
        assert "sys.exit()" not in path.read_text(encoding="utf-8"), path


def test_qr_wait_is_two_minutes_on_every_platform():
    for path in LOGIN_FILES:
        source = path.read_text(encoding="utf-8")
        assert "stop_after_attempt(120)" in source, path
        assert "remaining time is 20s" not in source, path
