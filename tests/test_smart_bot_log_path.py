from pathlib import Path

from src.core import smart_bot


def test_test_mode_uses_null_device(monkeypatch) -> None:
    monkeypatch.delenv("TRADING_BOT_LOG_PATH", raising=False)
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.delenv("UNIT_TESTING", raising=False)

    assert smart_bot._resolve_log_path() == Path(smart_bot.os.devnull)


def test_configured_log_path_overrides_test_mode(monkeypatch, tmp_path) -> None:
    configured_path = tmp_path / "configured.log"
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("TRADING_BOT_LOG_PATH", str(configured_path))

    assert smart_bot._resolve_log_path() == configured_path


def test_default_log_path_is_in_repository_root(monkeypatch) -> None:
    monkeypatch.delenv("TRADING_BOT_LOG_PATH", raising=False)
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("UNIT_TESTING", raising=False)

    expected_path = Path(smart_bot.__file__).resolve().parents[2] / "trading_bot.log"

    assert smart_bot._resolve_log_path() == expected_path
