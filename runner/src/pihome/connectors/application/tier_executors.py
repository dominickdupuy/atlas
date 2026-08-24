"""Tier executors (D7): what actually runs inside the child process.

Tier 1: deterministic steps only. Tier 2: steps, then exactly one model
call. Tier 3: a bounded agent round — deliberately a stub until phase 7
(spec §10: the riskiest capability ships last), but every guard is already
live: wall clock is enforced by the child's timeout, tokens by max_tokens on
the request, tool calls by the gateway ceiling.

Templating is deliberately primitive: `{{name}}` refers to a prior step's
saved result. An argument that is exactly one placeholder keeps the bound
value's type; placeholders inside longer strings substitute as JSON text.
Anything needing more logic than that belongs in a higher tier, not in a
cleverer template language.
"""

from __future__ import annotations

import json
import logging
import re

from pydantic import JsonValue, ValidationError

from pihome.connectors.application.gateway import ToolGateway
from pihome.connectors.application.ports import LlmProvider, LlmRequest
from pihome.connectors.domain.tools import ToolCall
from pihome.jobs.domain.definition import JobDefinition, ProposeTemplate
from pihome.jobs.domain.run import ProposedAction, RunReport

logger = logging.getLogger(__name__)

_PLACEHOLDER = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")
_TIER2_SYSTEM = (
    "You are the summarization step of a scheduled home-automation job. "
    "Write the requested output directly, with no preamble."
)
_TIER3_SYSTEM = (
    "You are one bounded planning round of a scheduled home-automation agent. "
    "Respond ONLY with a JSON array of proposed actions, each an object with "
    'keys "tool" (one of the allowed tools), "args" (object), and "summary" '
    "(one human-readable sentence). Respond with [] if nothing should be done."
)


def _as_text(value: JsonValue) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


def render_text(template: str, bindings: dict[str, JsonValue]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in bindings:
            raise KeyError(f"template references unknown step result {name!r}")
        return _as_text(bindings[name])

    return _PLACEHOLDER.sub(replace, template)


def render_value(value: JsonValue, bindings: dict[str, JsonValue]) -> JsonValue:
    if isinstance(value, str):
        exact = _PLACEHOLDER.fullmatch(value.strip())
        if exact:
            name = exact.group(1)
            if name not in bindings:
                raise KeyError(f"template references unknown step result {name!r}")
            return bindings[name]
        return render_text(value, bindings)
    if isinstance(value, dict):
        return {key: render_value(item, bindings) for key, item in value.items()}
    if isinstance(value, list):
        return [render_value(item, bindings) for item in value]
    return value


def render_args(args: dict[str, JsonValue], bindings: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {key: render_value(value, bindings) for key, value in args.items()}


async def _run_steps(
    definition: JobDefinition, gateway: ToolGateway
) -> tuple[dict[str, JsonValue], str | None]:
    """Returns (bindings, error). A failing step stops the job — later steps
    and templates depend on its result, so continuing would compound."""
    bindings: dict[str, JsonValue] = {}
    for step in definition.steps:
        call = ToolCall(tool=step.tool, args=render_args(step.args, bindings))
        result = await gateway.call(call)
        if result.is_error:
            return bindings, f"step {step.save_as!r} ({step.tool}) failed: {result.content}"
        bindings[step.save_as] = result.content
    return bindings, None


def _render_proposal(template: ProposeTemplate, bindings: dict[str, JsonValue]) -> ProposedAction:
    return ProposedAction(
        call=ToolCall(tool=template.tool, args=render_args(template.args, bindings)),
        summary=render_text(template.summary, bindings),
    )


class Tier1Executor:
    def __init__(self, gateway: ToolGateway) -> None:
        self._gateway = gateway

    async def execute(self, definition: JobDefinition) -> RunReport:
        try:
            bindings, error = await _run_steps(definition, self._gateway)
            if error:
                return RunReport(status="error", error=error, tool_calls=self._gateway.calls_made)
            proposals: tuple[ProposedAction, ...] = ()
            if definition.propose is not None:
                proposals = (_render_proposal(definition.propose, bindings),)
            return RunReport(
                status="ok",
                output=bindings,
                proposals=proposals,
                tool_calls=self._gateway.calls_made,
            )
        except Exception as exc:
            return RunReport(status="error", error=str(exc), tool_calls=self._gateway.calls_made)


class Tier2Executor:
    def __init__(self, gateway: ToolGateway, llm: LlmProvider) -> None:
        self._gateway = gateway
        self._llm = llm

    async def execute(self, definition: JobDefinition) -> RunReport:
        assert definition.synthesize is not None  # schema-guaranteed
        assert definition.budget.max_tokens is not None
        try:
            bindings, error = await _run_steps(definition, self._gateway)
            if error:
                return RunReport(status="error", error=error, tool_calls=self._gateway.calls_made)
            response = await self._llm.complete(
                LlmRequest(
                    system=_TIER2_SYSTEM,
                    prompt=render_text(definition.synthesize, bindings),
                    max_tokens=definition.budget.max_tokens,
                )
            )
            proposals: tuple[ProposedAction, ...] = ()
            if definition.propose is not None:
                proposals = (_render_proposal(definition.propose, bindings),)
            return RunReport(
                status="ok",
                output=response.text,
                proposals=proposals,
                usage=response.usage,
                tool_calls=self._gateway.calls_made,
            )
        except Exception as exc:
            return RunReport(status="error", error=str(exc), tool_calls=self._gateway.calls_made)


class Tier3AgentExecutor:
    """Phase 7 stub: one planning round, no execution. The model sees the
    goal, the allowlisted tools, and any step results, and may only emit
    proposals — which the parent routes through the D8/D16 approval flow
    like any other. A real multi-round loop replaces this in phase 7."""

    def __init__(self, gateway: ToolGateway, llm: LlmProvider) -> None:
        self._gateway = gateway
        self._llm = llm

    async def execute(self, definition: JobDefinition) -> RunReport:
        assert definition.goal is not None  # schema-guaranteed
        assert definition.budget.max_tokens is not None
        try:
            bindings, error = await _run_steps(definition, self._gateway)
            if error:
                return RunReport(status="error", error=error, tool_calls=self._gateway.calls_made)
            prompt = (
                f"Goal: {definition.goal}\n\n"
                f"Allowed tools: {', '.join(definition.tools)}\n\n"
                f"Context gathered by deterministic steps:\n{json.dumps(bindings, default=str)}"
            )
            response = await self._llm.complete(
                LlmRequest(
                    system=_TIER3_SYSTEM,
                    prompt=prompt,
                    max_tokens=definition.budget.max_tokens,
                )
            )
            proposals = self._parse_proposals(response.text, definition)
            return RunReport(
                status="ok",
                output=response.text,
                proposals=proposals,
                usage=response.usage,
                tool_calls=self._gateway.calls_made,
            )
        except Exception as exc:
            return RunReport(status="error", error=str(exc), tool_calls=self._gateway.calls_made)

    @staticmethod
    def _parse_proposals(text: str, definition: JobDefinition) -> tuple[ProposedAction, ...]:
        """Lenient parse; anything malformed or non-allowlisted is dropped
        loudly. A dropped proposal is a non-event by design — the safe
        failure mode for an agent is to do nothing."""
        try:
            raw = json.loads(text.strip())
        except json.JSONDecodeError:
            logger.warning("tier-3 model output was not JSON; proposing nothing")
            return ()
        if not isinstance(raw, list):
            logger.warning("tier-3 model output was not a JSON array; proposing nothing")
            return ()
        proposals: list[ProposedAction] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                action = ProposedAction(
                    call=ToolCall(tool=item.get("tool", ""), args=item.get("args") or {}),
                    summary=str(item.get("summary", "")) or "unlabeled proposal",
                )
            except ValidationError:
                logger.warning("dropping malformed tier-3 proposal: %r", item)
                continue
            if action.call.tool not in definition.tools:
                logger.warning("dropping non-allowlisted tier-3 proposal: %s", action.call.tool)
                continue
            proposals.append(action)
        return tuple(proposals)
