"""Command-line entry point.

Later tickets add a command by writing a handler that takes the built
:class:`~cv_job_match.app.App` and the parsed arguments and returns an exit
code, and registering it in :func:`create_parser`.
"""

import argparse
import asyncio
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import ValidationError

from cv_job_match.app import App, build_app
from cv_job_match.constants import (
    CONFIG_ERROR_EXIT_CODE,
    SUSPECT_RUN_EXIT_CODE,
    USAGE_ERROR_EXIT_CODE,
)
from cv_job_match.discovery import RunSummary
from cv_job_match.exceptions import (
    HttpClientSetupError,
    SourcesConfigError,
    UnknownSourceError,
    UnsupportedTechniqueError,
)
from cv_job_match.settings import Settings
from cv_job_match.utils.logger import configure_file_logging

logger = logging.getLogger(__name__)

CommandHandler = Callable[[App, argparse.Namespace], int]
AppFactory = Callable[[Settings], App]


def show_sources_config(app: App, args: argparse.Namespace) -> int:
    """Print every configured Source with its effective interval.

    Args:
        app: The built application.
        args: Parsed command-line arguments.

    Returns:
        Exit code 0.
    """
    sources = app.sources.sources
    enabled_count = sum(source.enabled for source in sources)
    print(f"{len(sources)} Sources, {enabled_count} enabled")
    for source in sources:
        hours = app.sources.resolve_interval(source).total_seconds() / 3600
        state = "on" if source.enabled else "off"
        print(f"{source.id}  {source.company}  {source.technique}  every {hours:g}h  {state}")
    return 0


def check_source_dry_run(app: App, args: argparse.Namespace) -> int:
    """Dry-run one Source and print what it found, without saving anything.

    Args:
        app: The built application.
        args: Parsed command-line arguments with ``source_id``.

    Returns:
        0 when the run is fine, 1 when it is suspect, 2 for an unknown Source
        or a technique without an adapter, and 3 for invalid technique options.
        Errors are printed to stderr.
    """
    try:
        summary = asyncio.run(app.discovery.check_source(args.source_id))
    except (UnknownSourceError, UnsupportedTechniqueError) as error:
        print(error, file=sys.stderr)
        return USAGE_ERROR_EXIT_CODE
    except SourcesConfigError as error:
        print(error, file=sys.stderr)
        return CONFIG_ERROR_EXIT_CODE
    print_run_summary(summary)
    return SUSPECT_RUN_EXIT_CODE if summary.suspect_reason else 0


def print_run_summary(summary: RunSummary) -> None:
    """Print the count, the previews and any suspect reason of a run.

    Args:
        summary: The outcome of checking a Source.
    """
    print(f"{summary.source_id}: {summary.found_count} Vacancies found (dry run, nothing saved)")
    for preview in summary.previews:
        print(
            "  "
            + "  ".join(part for part in (preview.vacancy_id, preview.title, preview.url) if part)
        )
        if preview.description:
            print(f"      {preview.description}")
        if preview.description_error:
            print(f"      description unavailable: {preview.description_error}")
    if summary.suspect_reason:
        print(f"Suspect: {summary.suspect_reason}")


def create_parser() -> argparse.ArgumentParser:
    """Build the argument parser with every command.

    Returns:
        The parser. Each command stores its handler in ``handler``.
    """
    parser = argparse.ArgumentParser(prog="cv-job-match", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--sources", type=Path, help="path to the Sources config, overriding settings"
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    check_config = commands.add_parser(
        "check-config", help="validate the Sources config and list the Sources"
    )
    check_config.set_defaults(handler=show_sources_config)
    check_source = commands.add_parser(
        "check-source", help="dry-run one Source and preview what it finds, saving nothing"
    )
    check_source.add_argument("source_id", help="id of the Source to check")
    check_source.set_defaults(handler=check_source_dry_run)
    return parser


def run_cli(
    argv: Sequence[str], settings: Settings, *, app_factory: AppFactory = build_app
) -> int:
    """Parse arguments, build the app and run one command.

    Args:
        argv: Command-line arguments without the program name.
        settings: Settings for this run. ``--sources`` overrides their
            Sources path.
        app_factory: Builds the app from settings. Tests pass one that
            injects fakes.

    Returns:
        The command's exit code, 2 when no command is given, or 3 when the
        Sources config or the HTTP client setup is invalid. The error is
        printed to stderr.
    """
    parser = create_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        logger.info("No command given")
        parser.print_usage(sys.stderr)
        return USAGE_ERROR_EXIT_CODE
    if args.sources:
        settings = settings.model_copy(update={"sources_path": args.sources})
    try:
        app = app_factory(settings)
    except (SourcesConfigError, HttpClientSetupError) as error:
        print(error, file=sys.stderr)
        return CONFIG_ERROR_EXIT_CODE
    logger.info("Running command %s", args.command)
    handler: CommandHandler = args.handler
    return handler(app, args)


def start_cli() -> int:
    """Load settings, set up the log file and run the CLI.

    Returns:
        The exit code for the process, or 3 when the settings are invalid.
    """
    try:
        settings = Settings()
    except ValidationError as error:
        print(f"Invalid settings: {error}", file=sys.stderr)
        return CONFIG_ERROR_EXIT_CODE
    configure_file_logging(settings.log_level)
    return run_cli(sys.argv[1:], settings)
