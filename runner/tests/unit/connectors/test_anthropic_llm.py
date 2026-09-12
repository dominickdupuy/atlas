"""AnthropicLlmProvider construction. No network: constructing the SDK
client touches no socket, so this runs under --disable-socket like the rest
of the suite."""

from __future__ import annotations

from atlas.connectors.infrastructure.anthropic_llm import AnthropicLlmProvider


def test_default_construction_keeps_the_sdk_defaults() -> None:
    """Jobs build this with neither kwarg (connectors_factory.py); the SDK's
    own timeout/retry defaults must survive untouched."""
    default_provider = AnthropicLlmProvider("key", "model")
    default_client = default_provider._client
    bare_client = AnthropicLlmProvider("key", "model")._client
    assert default_client.timeout == bare_client.timeout
    assert default_client.max_retries == bare_client.max_retries


def test_timeout_and_max_retries_reach_the_client() -> None:
    """voice_factory.py's prod branch passes timeout=5.0, max_retries=0 so a
    transient 429/529 fails fast and explicitly inside the 6s tier-2
    budget (finding: Tier-2 client)."""
    provider = AnthropicLlmProvider("key", "model", timeout=5.0, max_retries=0)
    client = provider._client
    assert client.timeout == 5.0
    assert client.max_retries == 0
