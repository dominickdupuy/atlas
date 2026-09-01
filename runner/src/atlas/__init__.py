"""atlas job runner: a modular monolith of bounded contexts (D5, D18).

Context map:
    jobs        scheduling + execution (D7, D14)
    approvals   the read/propose/write guardrail (D8, D16)
    budget      spend ledger, ceilings, pre-flight gates
    connectors  everything that talks to the outside world (D4, D19)
    telemetry   events out (D6), SSE stream, health

Dependency rules: contexts communicate through application services and
domain events on the in-process bus. `connectors.domain` is a published
language (ToolCall, TokenUsage) other contexts may import; application and
infrastructure layers never cross context boundaries directly — wiring
happens in `bootstrap`.
"""
