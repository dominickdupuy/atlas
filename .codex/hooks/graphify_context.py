#!/usr/bin/env python3
"""Read-only Codex SessionStart hook; no graph rebuilds or network calls."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or payload.get("hook_event_name") != "SessionStart":
            return
        if not Path(payload.get("cwd") or root).resolve().is_relative_to(root):
            return
    except (OSError, ValueError, TypeError):
        return

    context = [f"Atlas repository: {root}"]
    graph_path = root / "graphify-out" / "graph.json"
    try:
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        if not isinstance(graph, dict):
            raise ValueError("graph must be an object")
        nodes = len(graph.get("nodes", []))
        edges = len(graph.get("links", graph.get("edges", [])))
        modified = datetime.fromtimestamp(graph_path.stat().st_mtime, UTC).isoformat()
        context.append(f"Local Graphify index: {nodes} nodes, {edges} edges; written {modified}.")
        context.append(
            'Use graphify query "HostedRepoReader PanelRenderer StatusAssembler" --budget 1500 '
            "from the repo root, then verify in source. Refresh after uncommitted source edits "
            "with PYTHONHASHSEED=0 graphify update .; Git hooks refresh after commits/checkouts."
        )
    except (OSError, ValueError, TypeError):
        context.append("Graph unavailable. See docs/repo-map.md for local rebuild instructions.")

    try:
        context.append((root / "docs" / "repo-map.md").read_text(encoding="utf-8")[:8000])
    except OSError:
        context.append("Read AGENTS.md and docs/architecture.md for repository guidance.")

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": "\n\n".join(context),
                }
            }
        )
    )


if __name__ == "__main__":
    main()
