"""Settings precedence: environment must beat the TOML file, and a missing
file must be a non-event rather than a crash on a box that never had one."""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.config import Settings, config_files

ATLAS_ENV_VARS = ("ATLAS_PROFILE", "ATLAS_BIND_PORT", "ATLAS_DAILY_CEILING_USD", "ATLAS_TZ")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The developer's real environment must not decide these assertions."""
    for name in (*ATLAS_ENV_VARS, "ATLAS_CONFIG_FILE"):
        monkeypatch.delenv(name, raising=False)


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_values_load_from_the_toml_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _write_config(
        tmp_path,
        """
        profile = "prod"
        bind_port = 9999
        daily_ceiling_usd = "12.50"
        """,
    )
    monkeypatch.setenv("ATLAS_CONFIG_FILE", str(config))

    settings = Settings()

    assert settings.profile == "prod"
    assert settings.bind_port == 9999
    assert settings.daily_ceiling_usd == "12.50"


def test_environment_beats_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _write_config(tmp_path, 'profile = "prod"\nbind_port = 9999\n')
    monkeypatch.setenv("ATLAS_CONFIG_FILE", str(config))
    monkeypatch.setenv("ATLAS_BIND_PORT", "8123")

    settings = Settings()

    assert settings.bind_port == 8123, "environment must win over the file"
    assert settings.profile == "prod", "unset env vars still fall through to the file"


def test_missing_file_falls_back_to_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATLAS_CONFIG_FILE", str(tmp_path / "nope.toml"))

    settings = Settings()

    assert settings.bind_port == 8100
    assert settings.bind_host == "127.0.0.1"


def test_explicit_config_file_replaces_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_CONFIG_FILE", "/tmp/somewhere.toml")
    assert config_files() == (Path("/tmp/somewhere.toml"),)


def test_default_config_files_are_deduplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATLAS_CONFIG_FILE", raising=False)
    files = config_files()
    assert len(files) == len(set(files)), "the same file must not be read twice"
    assert all(path.is_absolute() for path in files)


def test_typed_coercion_survives_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A TOML string must still land as a typed value, not a raw str."""
    config = _write_config(tmp_path, 'db_path = "/var/lib/atlas/state.db"\nweather_lat = 41.5\n')
    monkeypatch.setenv("ATLAS_CONFIG_FILE", str(config))

    settings = Settings()

    assert settings.db_path == Path("/var/lib/atlas/state.db")
    assert settings.weather_lat == pytest.approx(41.5)
