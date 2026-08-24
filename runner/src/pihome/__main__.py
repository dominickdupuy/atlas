"""CLI: serve | execute-job | validate-jobs | migrate.

`execute-job` is the child half of the D14 protocol and is also how a job
is debugged by hand: `pihome execute-job < request.json`.
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

    from pihome.bootstrap.container import build_application
    from pihome.config import Settings
    from pihome.presentation.http.app import create_app

    settings = Settings()
    application = build_application(settings)
    api = create_app(application)
    uvicorn.run(api, host=settings.bind_host, port=settings.bind_port, log_level="info")
    return 0


def _validate_jobs(jobs_dir: Path) -> int:
    from pihome.jobs.application.catalog import JobCatalog
    from pihome.jobs.infrastructure.yaml_source import YamlJobDefinitionSource

    catalog = JobCatalog(YamlJobDefinitionSource(jobs_dir))
    try:
        catalog.load()
    except Exception as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {len(catalog.all_jobs)} job definition(s) valid")
    return 0


def _migrate(status_only: bool) -> int:
    from pihome.config import Settings
    from pihome.persistence.db import Database

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


def main() -> None:
    parser = argparse.ArgumentParser(prog="pihome")
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("serve", help="run the scheduler, API, and board")
    subcommands.add_parser(
        "execute-job", help="child process: read a RunRequest from stdin, report on stdout"
    )
    validate = subcommands.add_parser("validate-jobs", help="validate all job YAML files")
    validate.add_argument("--jobs-dir", type=Path, default=Path("../jobs"))
    migrate = subcommands.add_parser("migrate", help="apply pending database migrations")
    migrate.add_argument("--status", action="store_true", help="show status; change nothing")

    args = parser.parse_args()
    _configure_logging()

    match args.command:
        case "serve":
            sys.exit(_serve())
        case "execute-job":
            from pihome.jobs.infrastructure.child_main import child_entrypoint

            sys.exit(child_entrypoint())
        case "validate-jobs":
            sys.exit(_validate_jobs(args.jobs_dir))
        case "migrate":
            sys.exit(_migrate(args.status))


if __name__ == "__main__":
    main()
