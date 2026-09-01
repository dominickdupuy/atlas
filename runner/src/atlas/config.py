"""Settings: env-driven configuration (12-factor; compose injects paths,
`.env` holds user config and secrets), with an optional TOML file for the
values a human edits by hand. The child process reads the same environment —
nothing configuration-shaped travels over stdin except the job definition
itself.

Precedence, highest first: init kwargs, environment, `.env`, secrets dir,
`config.toml`. Environment beats the file so a systemd drop-in or a
one-off `ATLAS_PROFILE=dev` always wins over what is on disk.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from atlas.budget.domain.ledger import UsdMicros, usd

# Deployed layout is /opt/atlas/config.toml with the runner's working
# directory at /opt/atlas/runner, so the relative path is the same file;
# resolving de-duplicates them. Neither needs to exist.
_DEFAULT_CONFIG_FILES: tuple[Path, ...] = (
    Path("/opt/atlas/config.toml"),
    Path("../config.toml"),
)


def config_files() -> tuple[Path, ...]:
    """The TOML files to read, in order. `ATLAS_CONFIG_FILE` replaces the
    defaults outright — an explicit path is an assertion, not a hint, and
    silently merging it with a stray /opt file would be worse than ignoring
    it. Missing files are skipped by the source itself."""
    override = os.environ.get("ATLAS_CONFIG_FILE")
    if override:
        return (Path(override),)
    unique: dict[Path, None] = {}
    for candidate in _DEFAULT_CONFIG_FILES:
        unique.setdefault(candidate.resolve(), None)
    return tuple(unique)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ATLAS_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    profile: Literal["dev", "prod"] = "dev"
    api_token: str = "change-me"
    bind_host: str = "127.0.0.1"
    bind_port: int = 8100
    tz: str = "America/New_York"
    db_path: Path = Path("./data/state.db")
    jobs_dir: Path = Path("../jobs")

    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883

    # Probed for the board's container-health panel; never written to.
    homeassistant_host: str = "127.0.0.1"
    homeassistant_port: int = 8123

    daily_ceiling_usd: str = "5.00"
    model: str = "claude-sonnet-5"
    price_input_per_mtok: str | None = None
    price_output_per_mtok: str | None = None

    ntfy_url: str = "http://127.0.0.1:8090"
    ntfy_topic: str = "atlas-approvals"
    ntfy_token: str = ""
    public_url: str = "http://127.0.0.1:8100"

    weather_lat: float = 40.7128
    weather_lon: float = -74.0060

    # Unprefixed secrets, named as their providers name them.
    anthropic_api_key: str = Field(default="", validation_alias=AliasChoices("ANTHROPIC_API_KEY"))
    github_token: str = Field(default="", validation_alias=AliasChoices("GITHUB_TOKEN"))

    # MCP server endpoints (phase 5). Empty means not configured.
    mcp_github_url: str = ""
    mcp_gcal_url: str = ""
    mcp_home_assistant_url: str = ""
    mcp_home_assistant_token: str = ""

    @property
    def daily_ceiling(self) -> UsdMicros:
        return usd(self.daily_ceiling_usd)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Sources are consulted highest-precedence first; TOML goes last."""
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
            TomlConfigSettingsSource(settings_cls, toml_file=list(config_files())),
        )
