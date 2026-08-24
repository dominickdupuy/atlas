"""Tool-call value objects — the published language of the connector layer.

A tool name is dotted "server.tool" (spec §7): the prefix routes to an MCP
server or a built-in connector, the suffix names the tool there.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, JsonValue

WEATHER_SERVER = "weather"


class ToolCall(BaseModel):
    """One concrete tool invocation. Frozen: this is also the shape that gets
    serialized as an approval's frozen payload (D16) and executed verbatim."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*\.[a-z0-9][a-z0-9_.-]*$")
    args: dict[str, JsonValue] = Field(default_factory=dict)

    @property
    def server(self) -> str:
        return self.tool.partition(".")[0]

    @property
    def name(self) -> str:
        return self.tool.partition(".")[2]


class ToolResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool: str
    content: JsonValue
    is_error: bool = False


class TokenUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class ToolAllowlist(BaseModel):
    """Per-job allowlist from the job YAML (spec §7). Enforced by the
    ToolGateway on every call — the second layer of defense after
    provider-side credential scoping (D19)."""

    model_config = ConfigDict(frozen=True)

    tools: frozenset[str]

    def permits(self, tool: str) -> bool:
        return tool in self.tools
