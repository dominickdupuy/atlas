"""Settings: env-driven configuration (12-factor; compose injects paths,
`.env` holds user config and secrets). The child process reads the same
environment — nothing configuration-shaped travels over stdin except the
job definition itself."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from pihome.budget.domain.ledger import UsdMicros, usd


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PIHOME_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
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

    daily_ceiling_usd: str = "5.00"
    model: str = "claude-sonnet-5"
    price_input_per_mtok: str | None = None
    price_output_per_mtok: str | None = None

    ntfy_url: str = "http://127.0.0.1:8090"
    ntfy_topic: str = "pihome-approvals"
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
