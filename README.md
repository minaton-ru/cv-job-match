# CV Job Match

Searching for and parsing job openings on company websites, and preparing a resume and cover
letter for a selected vacancy. Domain terms are defined in [GLOSSARY.md](GLOSSARY.md), and design
decisions are recorded in [docs/adr/](docs/adr/).

## Install

The project uses `uv` and Python 3.13.

```sh
uv sync
```

## Configure

Settings come from environment variables prefixed with `CV_JOB_MATCH_`, or from a `.env` file in
the working directory. Never commit `.env`.

| Variable                    | Default        | Meaning                                                   |
| --------------------------- | -------------- | --------------------------------------------------------- |
| `CV_JOB_MATCH_SOURCES_PATH` | `sources.yaml` | Path to the Sources config.                               |
| `CV_JOB_MATCH_LOG_LEVEL`    | `DEBUG`        | Lowest level logged: DEBUG, INFO, WARNING, ERROR or CRITICAL. |

### Sources config

The Sources config is a YAML file that lists each Source to monitor. It is excluded from Git
(see `.gitignore`), because it names the Companies being watched. It is validated at startup,
and an invalid file stops the app with an error that names the Source and the problem.

```yaml
defaults:
  interval: 1d            # how often each Source is checked; default 1d

sources:
  - id: example-backend   # unique Source id
    company: Example Co   # several Sources may share a Company
    technique: json_endpoint   # json_endpoint, html_css or embedded_state
    enabled: true         # optional, default true; false switches the Source off
    interval: 12h         # optional override of defaults.interval
    notes: Backend roles  # optional free text
    list:                 # every other key is a technique option,
      url: https://careers.example.com/api/jobs   # read by the technique adapter
```

Intervals are a whole number followed by `s`, `m`, `h` or `d`, such as `30m`, `12h` or `1d`.

## Run

```sh
uv run cv-job-match check-config                  # validate the config and list the Sources
uv run cv-job-match --sources other.yaml check-config
```

The command exits with code 3 when the Sources config is invalid and prints the error.

### Logs

Every log of the service, from `CV_JOB_MATCH_LOG_LEVEL` up, goes to `cv_job_match.log` in the working directory.
The file is rotated at 10 MB, and the three previous files are kept as `cv_job_match.log.1` to
`.3`. Log files are excluded from Git.

## Develop

```sh
uv run ruff format .
uv run ruff check --fix .
uv run pytest -q
```

Everything is wired by one factory, `build_app(settings, *, repos, llm, http, notifier, clock)`
in `src/cv_job_match/app.py`. Production passes only settings, and tests pass Fake classes for
any dependency.

| Path                                 | Holds                                             |
| ------------------------------------ | ------------------------------------------------- |
| `src/cv_job_match/app.py`            | `App` and the `build_app` factory                 |
| `src/cv_job_match/settings.py`       | Settings read from the environment                |
| `src/cv_job_match/sources.py`        | Sources config schema and loader                  |
| `src/cv_job_match/exceptions.py`     | Every custom exception                            |
| `src/cv_job_match/constants.py`      | Every module-level constant                       |
| `src/cv_job_match/utils/cli.py`      | CLI; register new commands in `create_parser`     |
| `src/cv_job_match/utils/clock.py`    | `Clock` interface and `SystemClock`               |
| `src/cv_job_match/utils/logger.py`   | `configure_file_logging`, called once at startup  |

Modules never configure logging. Each one creates `logger = logging.getLogger(__name__)`, and
its records reach the log file through the `cv_job_match` package logger.
