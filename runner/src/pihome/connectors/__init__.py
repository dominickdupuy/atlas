"""Connectors context: everything that talks to the outside world (D4, D19).

MCP clients, the LLM provider, plain-HTTP connectors (weather), and the
notifier. `connectors.domain` is the published language — ToolCall,
ToolResult, TokenUsage — that jobs, approvals, and budget may import.
"""
