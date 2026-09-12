"""CLI: serve | execute-job | validate-jobs | migrate | lights.

`execute-job` is the child half of the D14 protocol and is also how a job
is debugged by hand: `atlas execute-job < request.json`.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path


def _configure_logging() -> None:
    # Everything to stderr: in the child, stdout is the NDJSON protocol.
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _serve() -> int:
    import uvicorn

    from atlas.bootstrap.container import build_application
    from atlas.config import Settings
    from atlas.presentation.http.app import create_app

    settings = Settings()
    application = build_application(settings)
    api = create_app(application)
    uvicorn.run(api, host=settings.bind_host, port=settings.bind_port, log_level="info")
    return 0


def _validate_jobs(jobs_dir: Path) -> int:
    from atlas.jobs.application.catalog import JobCatalog
    from atlas.jobs.infrastructure.yaml_source import YamlJobDefinitionSource

    catalog = JobCatalog(YamlJobDefinitionSource(jobs_dir))
    try:
        catalog.load()
    except Exception as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {len(catalog.all_jobs)} job definition(s) valid")
    return 0


def _migrate(status_only: bool) -> int:
    from atlas.config import Settings
    from atlas.persistence.db import Database

    async def run() -> int:
        db = Database(Settings().db_path)
        await db.connect()
        try:
            if status_only:
                for version, name, applied_at in await db.migration_status():
                    marker = applied_at or "PENDING"
                    print(f"{version:04d} {name}: {marker}")
            else:
                applied = await db.migrate()
                print(f"applied {len(applied)} migration(s)" if applied else "up to date")
        finally:
            await db.close()
        return 0

    return asyncio.run(run())


def _lights(args: argparse.Namespace) -> int:
    from atlas.config import Settings
    from atlas.lights.application import commissioning
    from atlas.lights.application.ports import ControllerUnavailable, MatterController, MatterError
    from atlas.lights.application.registry import LightsRegistry, RegistryError

    settings = Settings()
    if not settings.matter_ws_url or settings.matter_ws_url == "stub":
        print(
            "ATLAS_MATTER_WS_URL must point at the controller (ws://127.0.0.1:5580/ws)",
            file=sys.stderr,
        )
        return 2

    # Checked before ever opening a connection: no point waiting on the
    # controller for a removal the operator hasn't confirmed.
    if args.lights_command == "remove" and not args.yes:
        print("refusing without --yes", file=sys.stderr)
        return 2

    async def action(controller: MatterController) -> int:
        match args.lights_command:
            case "nodes":
                registry = LightsRegistry.load(settings.lights_file)
                for node in await commissioning.list_nodes(controller, registry):
                    state = "available" if node.available else "UNAVAILABLE"
                    print(
                        f"node {node.node_id}: {node.name or '(unnamed)'} | "
                        f"{node.vendor or '?'} {node.product or '?'} | {node.label or ''} | {state}"
                    )
            case "commission":
                node_id = await commissioning.commission(controller, args.code)
                print(f"commissioned node {node_id}; add it to lights.yaml under a name")
            case "identify":
                await commissioning.identify(controller, args.node_id, seconds=args.seconds)
                print(f"node {args.node_id} identifying for {args.seconds}s")
            case "remove":
                await commissioning.remove(controller, args.node_id)
                print(f"removed node {args.node_id}")
        return 0

    try:
        return asyncio.run(commissioning.with_controller(settings.matter_ws_url, action))
    except (ControllerUnavailable, MatterError, RegistryError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="atlas")
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("serve", help="run the scheduler, API, and board")
    subcommands.add_parser(
        "execute-job", help="child process: read a RunRequest from stdin, report on stdout"
    )
    validate = subcommands.add_parser("validate-jobs", help="validate all job YAML files")
    validate.add_argument("--jobs-dir", type=Path, default=Path("../jobs"))
    migrate = subcommands.add_parser("migrate", help="apply pending database migrations")
    migrate.add_argument("--status", action="store_true", help="show status; change nothing")

    lights = subcommands.add_parser("lights", help="matter controller operations")
    lights_sub = lights.add_subparsers(dest="lights_command", required=True)
    lights_sub.add_parser("nodes", help="list commissioned nodes with their registry names")
    commission_p = lights_sub.add_parser("commission", help="commission a bulb by pairing code")
    commission_p.add_argument("code", help="manual pairing code or MT: QR payload")
    identify_p = lights_sub.add_parser("identify", help="blink a node so you can name it")
    identify_p.add_argument("node_id", type=int)
    identify_p.add_argument("--seconds", type=int, default=10)
    remove_p = lights_sub.add_parser("remove", help="decommission a node from the atlas fabric")
    remove_p.add_argument("node_id", type=int)
    remove_p.add_argument("--yes", action="store_true")

    args = parser.parse_args()
    _configure_logging()

    match args.command:
        case "serve":
            sys.exit(_serve())
        case "execute-job":
            from atlas.jobs.infrastructure.child_main import child_entrypoint

            sys.exit(child_entrypoint())
        case "validate-jobs":
            sys.exit(_validate_jobs(args.jobs_dir))
        case "migrate":
            sys.exit(_migrate(args.status))
        case "lights":
            sys.exit(_lights(args))


if __name__ == "__main__":
    main()
