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
| `CV_JOB_MATCH_EXTRA_CA_PATH` | not set       | PEM file with an extra root certificate, trusted only for the hosts of Sources with `extra_ca: true`. |

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

A Source with `extra_ca: true` is fetched with the default CA bundle plus the root certificate
from `CV_JOB_MATCH_EXTRA_CA_PATH`. Certificate checks are never turned off. If the variable is
not set, a warning is logged and those hosts use the default bundle only.

### `json_endpoint` options

The list is fetched with one GET request (pagination and POST are not supported yet; with
`pagination` set, only the first page is read and a warning is logged). Paths are dotted, such
as `data.items` or `tags.0`.

```yaml
    list:
      url: https://careers.example.com/api/jobs   # required
      params: {q: python}           # optional query parameters
      items_path: data.items        # required; '' when the response is the list itself
      id_field: id                  # required; path to the site's own ID inside an item
      fields:                       # optional; field name -> path inside an item
        title: name
        url: link
    detail:                         # optional; without it, no description is fetched
      url: https://careers.example.com/api/jobs/{id}   # {id} is the Vacancy's ID
      params: {lang: en}            # optional; {id} is replaced in values too
      description_fields: [body.summary, body.duties]  # joined by blank lines
```

An item without an ID makes the whole list fetch fail, so the run is reported as suspect.

## Run

```sh
uv run cv-job-match check-config                  # validate the config and list the Sources
uv run cv-job-match --sources other.yaml check-config
uv run cv-job-match check-source example-backend  # dry-run one Source, saving nothing
```

Every command exits with code 3 when the Sources config is invalid and prints the error.

`check-source <source id>` fetches the Source's list and the descriptions of the first three
Vacancies, then prints how many Vacancies it found, a preview of those three, and the suspect
reason when the list fetch failed (network error, non-2xx status, malformed JSON, or a missing
items path or ID). Nothing is saved. Switched-off Sources can be checked too. It exits with:

| Code | Meaning                                                        |
| ---- | -------------------------------------------------------------- |
| 0    | The list was read                                              |
| 1    | The run is suspect; the reason is printed                      |
| 2    | Unknown Source id, or its technique has no adapter yet         |
| 3    | Invalid Sources config or technique options, or a bad extra CA |

Requests go through `PoliteHttpClient`: at most one request per second per host, with a
browser-like User-Agent. Redirects are not followed; a redirected list shows up as an
`HTTP 3xx` suspect reason, so point `url` at the final address.

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
any dependency, such as `FakeHttpClient` in `tests/fakes.py` with canned responses per URL.

| Path                                 | Holds                                             |
| ------------------------------------ | ------------------------------------------------- |
| `src/cv_job_match/app.py`            | `App` and the `build_app` factory                 |
| `src/cv_job_match/settings.py`       | Settings read from the environment                |
| `src/cv_job_match/sources.py`        | Sources config schema and loader                  |
| `src/cv_job_match/discovery.py`      | `Discovery.check_source` and the adapter interface |
| `src/cv_job_match/adapters/`         | Technique adapters, wired by name in `build_app`  |
| `src/cv_job_match/http_client.py`    | `HttpClient` seam and `PoliteHttpClient`          |
| `src/cv_job_match/exceptions.py`     | Every custom exception                            |
| `src/cv_job_match/constants.py`      | Every module-level constant                       |
| `src/cv_job_match/utils/cli.py`      | CLI; register new commands in `create_parser`     |
| `src/cv_job_match/utils/clock.py`    | `Clock` interface and `SystemClock`               |
| `src/cv_job_match/utils/logger.py`   | `configure_file_logging`, called once at startup  |

Modules never configure logging. Each one creates `logger = logging.getLogger(__name__)`, and
its records reach the log file through the `cv_job_match` package logger.
