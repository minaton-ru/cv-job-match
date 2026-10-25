"""Command-line entry point.

Later tickets add a command by writing a handler that takes the built
:class:`~cv_job_match.app.App` and returns an exit code, and registering it
in :func:`create_parser`.
"""

import argparse
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import ValidationError

from cv_job_match.app import App, build_app
from cv_job_match.constants import CONFIG_ERROR_EXIT_CODE, USAGE_ERROR_EXIT_CODE
from cv_job_match.exceptions import SourcesConfigError
from cv_job_match.settings import Settings
from cv_job_match.utils.logger import configure_file_logging

logger = logging.getLogger(__name__)

CommandHandler = Callable[[App], int]


def show_sources_config(app: App) -> int:
    """Print every configured Source with its effective interval.

    Args:
        app: The built application.

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
    return parser


def run_cli(argv: Sequence[str], settings: Settings) -> int:
    """Parse arguments, build the app and run one command.

    Args:
        argv: Command-line arguments without the program name.
        settings: Settings for this run. ``--sources`` overrides their
            Sources path.

    Returns:
        The command's exit code, 2 when no command is given, or 3 when the
        Sources config is invalid. The config error is printed to stderr.
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
        app = build_app(settings)
    except SourcesConfigError as error:
        print(error, file=sys.stderr)
        return CONFIG_ERROR_EXIT_CODE
    logger.info("Running command %s", args.command)
    handler: CommandHandler = args.handler
    return handler(app)


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
